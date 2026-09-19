#!/usr/bin/env python3
"""Parse measured context data pasted by the user and reconcile it with estimates.

Two commands produce numbers this tool cannot obtain on its own: the context
breakdown and the usage attribution. Both are slash commands run by the user.
This script parses whatever they paste, in either the plain terminal form or the
rendered table form, and compares it against the byte-derived estimates.

Without pasted data the caller still gets a usable result: every figure is then
labelled "estimated" and the known bias is reported alongside it.

Usage:
    python3 measured.py context  <file|->        # parse a pasted context breakdown
    python3 measured.py usage    <file|->        # parse a pasted usage attribution
    python3 measured.py reconcile --inventory inv.json [--context ctx.txt]
    python3 measured.py --self-test              # run the built-in fixtures
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# "2.4k", "653", "~180", "12.6k tokens", "1.2m"
AMOUNT = r"~?\s*(\d+(?:[.,]\d+)?)\s*([kKmM])?"
LABELLED_LINE = re.compile(rf"^\s*[-*⠀-⣿\W]*([A-Za-z][\w .'/()-]+?)\s*:\s*{AMOUNT}\s*(?:tokens?)?\b", re.M)
TABLE_ROW = re.compile(rf"^\s*\|\s*([^|]+?)\s*\|\s*(?:[^|]*?\|\s*)?{AMOUNT}\s*(?:tokens?)?\s*\|", re.M)
PERCENT_ROW = re.compile(r"^\s*\|?\s*([A-Za-z][\w .:@'/-]+?)\s*[|:]\s*(\d+(?:[.,]\d+)?)\s*%", re.M)

CONTEXT_CATEGORIES = {
    "system prompt", "system tools", "mcp tools", "custom agents", "memory files",
    "skills", "messages", "free space", "autocompact buffer",
}

# Recorded from a real session so the deviation check is reproducible.
FIXTURE_CONTEXT = """
System prompt: 2.4k tokens (0.2%)
System tools: 12.6k tokens (1.3%)
MCP tools: 653 tokens (0.1%)
Custom agents: 314 tokens (0.0%)
Memory files: 4.6k tokens (0.5%)
Skills: 9.1k tokens (0.9%)
Messages: 878 tokens (0.1%)
"""

FIXTURE_USAGE = """
| Skill | Share |
|---|---|
| openspec-propose-deep | 12.5% |
| release-plan | 4.0% |
| mem-search | 0.0% |
"""


def to_tokens(number: str, suffix: str | None) -> int:
    value = float(number.replace(",", "."))
    if suffix and suffix.lower() == "k":
        value *= 1_000
    elif suffix and suffix.lower() == "m":
        value *= 1_000_000
    return int(round(value))


def parse_context(text: str) -> dict:
    """Extract token counts per category from a pasted context breakdown."""
    found: dict[str, int] = {}
    for label, number, suffix in LABELLED_LINE.findall(text):
        key = " ".join(label.split()).lower()
        if key in CONTEXT_CATEGORIES:
            found[key] = to_tokens(number, suffix)
    for label, number, suffix in TABLE_ROW.findall(text):
        key = " ".join(label.split()).lower()
        if key in CONTEXT_CATEGORIES and key not in found:
            found[key] = to_tokens(number, suffix)

    counted = {k: v for k, v in found.items() if k not in ("free space", "autocompact buffer")}
    return {
        "categories": {
            k: {"value": v, "basis": "measured", "method": "pasted context breakdown"}
            for k, v in found.items()
        },
        "startup_total": {
            "value": sum(counted.values()),
            "basis": "measured",
            "method": "sum of pasted categories, excluding free space and buffer",
        },
        "parsed_category_count": len(found),
    }


def parse_usage(text: str) -> dict:
    """Extract per-element attribution shares from a pasted usage report."""
    entries = []
    for label, percent in PERCENT_ROW.findall(text):
        name = " ".join(label.split())
        if name.lower() in ("skill", "share", "name", "plugin", "mcp server", "percent"):
            continue
        entries.append({
            "name": name,
            "share_percent": {"value": float(percent.replace(",", ".")),
                              "basis": "measured", "method": "pasted usage attribution"},
        })
    return {"entries": entries, "parsed_entry_count": len(entries)}


def reconcile(inventory: dict, context: dict | None) -> dict:
    """Compare estimated figures against measured ones and report the deviation."""
    estimated_total = inventory["totals"]["startup_prompt_cost"]["value"]

    if not context:
        return {
            "mode": "estimate-only",
            "note": ("No measured data supplied. Every figure below is estimated from file size. "
                     "That method underestimates non-English text and text containing code, so "
                     "treat these numbers as a lower bound."),
            "estimated_startup_prompt_cost": {
                "value": estimated_total, "basis": "estimated", "method": "chars/4",
            },
            "comparisons": [],
        }

    comparisons = []
    measured_skills = context["categories"].get("skills")
    if measured_skills:
        comparisons.append(_compare("skills (descriptions in prompt)",
                                    estimated_total, measured_skills["value"]))

    measured_memory = context["categories"].get("memory files")
    if measured_memory:
        estimated_memory = sum(
            e["prompt_cost"]["value"] for e in inventory["entries"]
            if e["kind"] == "instructions" and isinstance(e["prompt_cost"]["value"], int)
        )
        comparisons.append(_compare("memory files", estimated_memory, measured_memory["value"]))

    return {
        "mode": "measured",
        "measured_startup_total": context["startup_total"],
        "estimated_startup_prompt_cost": {
            "value": estimated_total, "basis": "estimated", "method": "chars/4",
        },
        "comparisons": comparisons,
        "authoritative": "measured",
    }


def _compare(label: str, estimated: int, measured: int) -> dict:
    deviation = (estimated - measured) / measured * 100 if measured else 0.0
    return {
        "label": label,
        "estimated": {"value": estimated, "basis": "estimated", "method": "chars/4"},
        "measured": {"value": measured, "basis": "measured", "method": "pasted context breakdown"},
        "deviation_percent": round(deviation, 1),
        "direction": "underestimates" if estimated < measured else "overestimates",
    }


def read_source(argument: str) -> str:
    return sys.stdin.read() if argument == "-" else Path(argument).read_text(encoding="utf-8")


def self_test() -> int:
    failures = []

    context = parse_context(FIXTURE_CONTEXT)
    if context["categories"].get("system tools", {}).get("value") != 12600:
        failures.append(f"context: system tools parsed as {context['categories'].get('system tools')}")
    if context["startup_total"]["value"] != 30545:
        failures.append(f"context: startup total was {context['startup_total']['value']}, expected 30545")
    if context["startup_total"]["basis"] != "measured":
        failures.append("context: startup total not labelled measured")

    usage = parse_usage(FIXTURE_USAGE)
    names = [e["name"] for e in usage["entries"]]
    if names != ["openspec-propose-deep", "release-plan", "mem-search"]:
        failures.append(f"usage: parsed names were {names}")

    # A project CLAUDE.md of 8,715 chars estimates to 2,178 tokens but measures 3,600.
    fake_inventory = {
        "totals": {"startup_prompt_cost": {"value": 7599, "basis": "estimated"}},
        "entries": [{"kind": "instructions", "prompt_cost": {"value": 2178, "basis": "estimated"}}],
    }
    measured_ctx = parse_context("Memory files: 3.6k tokens\n")
    result = reconcile(fake_inventory, measured_ctx)
    memory = next(c for c in result["comparisons"] if c["label"] == "memory files")
    if memory["direction"] != "underestimates":
        failures.append("reconcile: expected the estimate to come in under the measurement")
    if not (-45 < memory["deviation_percent"] < -35):
        failures.append(f"reconcile: deviation was {memory['deviation_percent']}%, expected about -39%")

    fallback = reconcile(fake_inventory, None)
    if fallback["mode"] != "estimate-only" or "lower bound" not in fallback["note"]:
        failures.append("reconcile: fallback path did not degrade cleanly")

    for line in failures:
        print(f"FAIL {line}")
    if failures:
        return 1
    print("self-test: all checks passed")
    print(f"  context categories parsed : {context['parsed_category_count']}")
    print(f"  measured startup total    : {context['startup_total']['value']} ({context['startup_total']['basis']})")
    print(f"  usage entries parsed      : {usage['parsed_entry_count']}")
    print(f"  memory deviation          : {memory['deviation_percent']}% ({memory['direction']})")
    print(f"  no-data fallback mode     : {fallback['mode']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=["context", "usage", "reconcile"])
    parser.add_argument("source", nargs="?", help="file path, or - for stdin")
    parser.add_argument("--inventory", help="inventory JSON produced by inventory.py")
    parser.add_argument("--context", help="file holding a pasted context breakdown")
    parser.add_argument("--self-test", action="store_true", help="run the built-in fixtures")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    if args.command == "context":
        print(json.dumps(parse_context(read_source(args.source or "-")), indent=2))
    elif args.command == "usage":
        print(json.dumps(parse_usage(read_source(args.source or "-")), indent=2))
    elif args.command == "reconcile":
        if not args.inventory:
            parser.error("reconcile needs --inventory")
        inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
        context = parse_context(Path(args.context).read_text(encoding="utf-8")) if args.context else None
        print(json.dumps(reconcile(inventory, context), indent=2))
    else:
        parser.print_help()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
