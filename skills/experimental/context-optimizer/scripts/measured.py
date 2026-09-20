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

# Built-in skills ship inside the client. They are not on disk anywhere, so the
# pasted skill listing is the only way this tool can ever see them — and they
# are the category `skillOverrides` is most used on in practice. Without this
# parser, `applies_to: {"built-in"}` promises a mechanism for entries that can
# never reach a plan.
#
# The listing's exact rendering is not pinned down and changes between client
# versions, so the parser is deliberately tolerant: it keys off a source word
# and takes the name that precedes it. Anything it cannot read it leaves out
# rather than guessing, and the caller is told how many lines it recognised.
SOURCE_WORDS = (
    ("locked by plugin", "plugin"),
    ("built-in", "built-in"),
    ("builtin", "built-in"),
    ("plugin", "plugin"),
    ("synced", "claude-ai-synced"),
    ("claude.ai", "claude-ai-synced"),
    ("personal", "user-global"),
    ("user", "user-global"),
    ("project", "project"),
    ("local", "project"),
)

# Tokens that are furniture, not names: table headers, status words, and the
# source words themselves.
LISTING_NOISE = {
    "skill", "skills", "name", "source", "cost", "status", "tokens", "token",
    "enabled", "disabled", "managed", "via", "are", "and", "the", "off", "on",
    "built-in", "builtin", "plugin", "synced", "user", "project", "local",
    "personal", "locked", "description", "size", "type", "scope",
}

SKILL_NAME_TOKEN = re.compile(r"[A-Za-z][\w.-]*(?::[\w.-]+)?")

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

# A skill listing, in the shape reported from a real client. The two facts that
# matter are that built-ins appear here and nowhere else, and that plugin rows
# carry "locked by plugin".
FIXTURE_SKILLS = """
  pdf                    built-in    on      420 tokens
  docx                   built-in    on      380 tokens
  release-plan           user        on      290 tokens
  mem-search          🔒 plugin      on · locked by plugin   210 tokens
  deep-research          synced      on      340 tokens
Plugin skills are managed via /plugin
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
    # Users paste everything they were asked for into one file. When the skill
    # listing came along with the breakdown, read it here too: the built-in
    # rows in it are the only sighting of an entire category of skills.
    listing = parse_skills(text)
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
        "skill_listing": listing,
    }


def parse_skills(text: str) -> dict:
    """Extract per-skill rows from a pasted skill listing.

    Returns every row it can read, with its source, whether the client reports
    it as locked, and its cost when one is shown. Built-in rows matter most:
    they exist nowhere on disk, so this is the only route by which they can
    enter an inventory.
    """
    rows: list[dict] = []
    seen: set[str] = set()

    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line) > 400:
            continue
        lowered = line.lower()

        # Take the source word that appears earliest, not the most specific
        # one. On a row reading "🔒 plugin  on · locked by plugin" the phrase
        # match sits far to the right, and anchoring on it would put the real
        # source column between the name and the anchor.
        hits = [(lowered.find(word), order, mapped)
                for order, (word, mapped) in enumerate(SOURCE_WORDS)
                if lowered.find(word) != -1]
        if not hits:
            continue
        source_at, _, source = min(hits)
        locked = "locked" in lowered

        # The name is the first name-like token on the row, and between it and
        # the source column there must be nothing but whitespace and
        # decoration. A listing has columns; a sentence has words. Without this
        # test, "the user asked about the plugin system" parses as a skill
        # called "asked".
        name = None
        for match in SKILL_NAME_TOKEN.finditer(line):
            if match.start() >= source_at:
                break
            token = match.group(0)
            if token.lower() in LISTING_NOISE or len(token) < 2:
                continue
            if re.search(r"[A-Za-z0-9]", line[match.end():source_at]):
                break
            name = token
            break
        if not name or name in seen:
            continue
        seen.add(name)

        cost = {"value": None, "basis": "unavailable",
                "note": "no cost shown on this row"}
        amount = re.search(AMOUNT + r"\s*tokens?\b", line, re.I)
        if amount:
            cost = {"value": to_tokens(amount.group(1), amount.group(2)),
                    "basis": "measured", "method": "pasted skill listing"}

        rows.append({"name": name, "source": source, "locked": locked, "prompt_cost": cost})

    return {
        "entries": rows,
        "parsed_entry_count": len(rows),
        "built_in_count": sum(1 for r in rows if r["source"] == "built-in"),
        "locked_count": sum(1 for r in rows if r["locked"]),
        "note": ("Rows this parser could not read are omitted, never guessed. If the count "
                 "here is far below what the listing showed, report that and fall back to "
                 "reading the listing directly."),
    }


def inject_skills(inventory: dict, skills: dict) -> dict:
    """Fold a parsed skill listing into an inventory, in place.

    Two effects, both of which a plan depends on:

    - every built-in row the inventory does not already hold is added as a real
      entry with source "built-in", so `skill-override-off` can finally reach
      the category it was written for;
    - every row the client reports as locked marks the matching entry, so the
      planner can drop it instead of proposing a cut that cannot be applied.
    """
    by_name = {e.get("name"): e for e in inventory["entries"]}
    overrides = inventory.get("skill_overrides") or {}
    added, marked = [], []

    for row in skills.get("entries", []):
        existing = by_name.get(row["name"])
        if existing is not None:
            if row["locked"] and not existing.get("locked_by_client"):
                existing["locked_by_client"] = True
                marked.append(row["name"])
            continue
        if row["source"] != "built-in":
            # A non-built-in row with no entry on disk is a disagreement worth
            # reporting, not a hole to fill with a fabricated path.
            continue
        disabled = overrides.get(row["name"]) == "off"
        entry = {
            "kind": "skill",
            "name": row["name"],
            "source": "built-in",
            "link_path": None,
            "real_path": None,
            "is_symlink": False,
            "description": "",
            "description_chars": 0,
            "body_chars": 0,
            "prompt_cost": row["prompt_cost"],
            "body_cost": {"value": None, "basis": "unavailable",
                          "note": "built-in skills ship inside the client"},
            "user_invocable_only": False,
            "locked_by_client": row["locked"],
            "disabled_by_override": disabled,
            "loads_at_startup": not disabled,
            "note": "not on disk; seen only in the pasted skill listing",
        }
        inventory["entries"].append(entry)
        by_name[row["name"]] = entry
        added.append(row["name"])

    if added:
        estimated = inventory["totals"]["startup_prompt_cost"]
        measured_added = sum(
            (by_name[n]["prompt_cost"] or {}).get("value") or 0
            for n in added if by_name[n]["loads_at_startup"]
        )
        estimated["value"] += measured_added
        estimated["method"] += "; plus measured built-in costs from the pasted listing"
        estimated["basis"] = "mixed"

    inventory["built_in_injection"] = {
        "added": added,
        "locked_marked": marked,
        "basis": "measured",
        "method": "pasted skill listing",
    }
    return inventory


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


def load_skill_rows(text: str) -> dict:
    """Accept either the raw paste or the JSON `measured.py skills` produced.

    The two are easy to confuse — one is the documented input of `skills` and
    the other its documented output — and feeding the JSON back in used to
    parse it as a listing, find nothing, and report an empty merge as a
    success. Detect the shape instead of trusting the caller.
    """
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("entries"), list):
            return parsed
    return parse_skills(text)


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

    # The skill listing is the only sighting of a built-in skill.
    listing = parse_skills(FIXTURE_SKILLS)
    rows = {r["name"]: r for r in listing["entries"]}
    if listing["built_in_count"] != 2:
        failures.append(f"skills: parsed {listing['built_in_count']} built-ins, expected 2")
    if rows.get("pdf", {}).get("prompt_cost", {}).get("value") != 420:
        failures.append("skills: a measured per-row cost was not read")
    if rows.get("release-plan", {}).get("source") != "user-global":
        failures.append("skills: a user row was misattributed")
    if not rows.get("mem-search", {}).get("locked"):
        failures.append("skills: a plugin row was not reported as locked")
    if rows.get("mem-search", {}).get("source") != "plugin":
        failures.append("skills: a locked row lost its source")
    if "Plugin" in rows or "skills" in rows:
        failures.append("skills: the footer sentence was parsed as an entry")

    # Prose that happens to contain "user", "project" or "plugin" is not a
    # listing. Before the column test, this text parsed as two skills.
    prose = ("The user asked about the project and the plugin system.\n"
             "A local project skill can be disabled by the user at any time.\n"
             "Plugin skills are managed via /plugin\n")
    if parse_skills(prose)["parsed_entry_count"] != 0:
        failures.append("skills: prose was parsed as listing rows")

    # merge accepts either the raw paste or this script's own JSON. Handing it
    # the JSON used to reparse it as a listing and report an empty merge as
    # success, which silently loses every built-in.
    round_tripped = load_skill_rows(json.dumps(listing))
    if [r["name"] for r in round_tripped["entries"]] != [r["name"] for r in listing["entries"]]:
        failures.append("merge did not accept its own JSON output")
    if load_skill_rows(FIXTURE_SKILLS)["parsed_entry_count"] != listing["parsed_entry_count"]:
        failures.append("merge did not accept a raw paste")

    # Injection puts built-ins into an inventory that could never see them, and
    # marks what the client reports as locked.
    target = {
        "entries": [
            {"name": "release-plan", "kind": "skill", "source": "user-global",
             "prompt_cost": {"value": 60, "basis": "estimated"}, "loads_at_startup": True},
            {"name": "mem-search", "kind": "skill", "source": "plugin",
             "prompt_cost": {"value": 50, "basis": "estimated"}, "loads_at_startup": True},
        ],
        "skill_overrides": {"docx": "off"},
        "totals": {"startup_prompt_cost": {"value": 110, "basis": "estimated",
                                           "method": "chars/4"}},
    }
    inject_skills(target, listing)
    injected = {e["name"]: e for e in target["entries"]}
    if "pdf" not in injected or injected["pdf"]["source"] != "built-in":
        failures.append("inject: a built-in skill did not reach the inventory")
    if injected["pdf"]["prompt_cost"]["basis"] != "measured":
        failures.append("inject: a built-in cost was not labelled measured")
    if not injected["docx"]["disabled_by_override"] or injected["docx"]["loads_at_startup"]:
        failures.append("inject: a built-in already switched off was counted as loading")
    if not injected["mem-search"].get("locked_by_client"):
        failures.append("inject: an existing plugin entry was not marked locked")
    if "deep-research" in injected:
        failures.append("inject: a synced row with no file on disk was fabricated")
    # 110 + pdf's 420; docx is off and must not be added to the total.
    if target["totals"]["startup_prompt_cost"]["value"] != 530:
        failures.append(f"inject: total became {target['totals']['startup_prompt_cost']['value']}, expected 530")

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
    print(f"  skill rows parsed         : {listing['parsed_entry_count']} "
          f"({listing['built_in_count']} built-in, {listing['locked_count']} locked)")
    print(f"  built-ins injected        : {target['built_in_injection']['added']}")
    print(f"  locked entries marked     : {target['built_in_injection']['locked_marked']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?",
                        choices=["context", "usage", "skills", "merge", "reconcile"])
    parser.add_argument("source", nargs="?", help="file path, or - for stdin")
    parser.add_argument("--inventory", help="inventory JSON produced by inventory.py")
    parser.add_argument("--context", help="file holding a pasted context breakdown")
    parser.add_argument("--skills", help="file holding a pasted skill listing")
    parser.add_argument("--self-test", action="store_true", help="run the built-in fixtures")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    if args.command == "context":
        print(json.dumps(parse_context(read_source(args.source or "-")), indent=2))
    elif args.command == "usage":
        print(json.dumps(parse_usage(read_source(args.source or "-")), indent=2))
    elif args.command == "skills":
        print(json.dumps(parse_skills(read_source(args.source or "-")), indent=2))
    elif args.command == "merge":
        if not args.inventory:
            parser.error("merge needs --inventory")
        inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
        source = args.skills or args.context or args.source
        if not source:
            parser.error("merge needs --skills (or --context holding the same paste)")
        rows = load_skill_rows(read_source(source))
        if not rows["entries"]:
            print("No skill rows could be read from that paste; nothing was merged. "
                  "Report this rather than continuing as if built-ins had been seen.",
                  file=sys.stderr)
        print(json.dumps(inject_skills(inventory, rows), indent=2))
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
