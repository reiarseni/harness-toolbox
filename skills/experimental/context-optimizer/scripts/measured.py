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

from units import annotate, thousands

# "2.4k", "653", "~180", "12.6k tokens", "1.2m", "100,628"
#
# A comma is a decimal point in "2,4k" and a thousands separator in "100,628",
# and the client and these docs use both. The first alternative claims any
# number written in three-digit groups, so it is read as grouping; everything
# else falls through to the decimal reading. Without the split, "1,340 tokens"
# parsed as 1 and "100,628 uses" as 100 — silently, as measured figures.
GROUPED = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?"
AMOUNT = rf"~?\s*({GROUPED}|\d+(?:[.,]\d+)?)\s*([kKmM])?"
# The client writes "~280 tok" in the skill listing and a bare "~480" in the
# context table. Requiring the word "tokens" lost every per-skill cost on a
# real paste, which is the one number the merge exists to carry.
TOKEN_AMOUNT = re.compile(AMOUNT + r"\s*(?:tokens?|tok)?\s*$", re.I)
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
    ("built-in", "built-in"),
    ("builtin", "built-in"),
    ("plugin", "plugin"),
    ("claude.ai", "claude-ai-synced"),
    ("synced", "claude-ai-synced"),
    ("sync", "claude-ai-synced"),
    ("personal", "user-global"),
    ("user", "user-global"),
    ("project", "project"),
    ("local", "project"),
)

# The listing's real shape, measured on Claude Code 2.1.278:
#
#   ✘ off        anthropic-skills:pdf · claude.ai sync · ~150 tok
#   🔒 on         claude-mem:babysit · plugin · ~70 tok · locked by plugin
#   🔒 user-only  context-optimizer · user · ~130 tok · locked by author
#   ✔ on         disk-cleanup · user · ~180 tok
#
# Fields are separated by "·", which is what makes this readable at all. The
# first version of this parser searched the whole line for a source word and
# got three things wrong on the first real paste: it read
# "anthropic-skills:built-in-browser" as a built-in skill because the name
# contains the literal "built-in"; it lost every cost, because the real suffix
# is "tok"; and it dropped every "user-only" row, because "user" appears inside
# the state word before the name. Match the source field, never the line.
FIELD_SEPARATOR = "·"
STATE_WORDS = {"on", "off", "user-only", "enabled", "disabled"}
GLYPHS = "✘✔🔒⛀⛁·❯*-•> \t"

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

# Copied verbatim from Claude Code 2.1.278, not invented. Every trap in it was
# a real failure of the first parser: the name containing the literal
# "built-in", the "~N tok" suffix, the "user-only" state word containing
# "user", the namespace prefix the inventory does not carry, and the two
# different meanings of "locked".
FIXTURE_SKILLS = """
✘ off        anthropic-skills:built-in-browser · claude.ai sync · ~280 tok
✘ off        anthropic-skills:pdf · claude.ai sync · ~150 tok
🔒 on         claude-mem:mem-search · plugin · ~70 tok · locked by plugin
🔒 user-only  release-plan · user · ~100 tok · locked by author
✔ on         disk-cleanup · user · ~180 tok
"""

# Built-in skills are absent from /skills entirely; they show up only in the
# context breakdown's own table, in this shape.
FIXTURE_BUILTIN_TABLE = """
| Skill | Source | Tokens |
|-------|--------|--------|
| dataviz | Built-in | ~480 |
| code-review | Built-in | ~280 |
| disk-cleanup | User | ~180 |
"""


def to_tokens(number: str, suffix: str | None) -> int:
    if re.fullmatch(GROUPED, number):
        value = float(number.replace(",", ""))
    else:
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

    # "Messages" is the conversation, not startup context: it is whatever has
    # been said so far and nothing this skill can act on. Counting it made the
    # startup total read 287,283 on a long session, against a real startup cost
    # of about 26k. The fixture that missed this had 878 tokens of messages.
    counted = {k: v for k, v in found.items()
               if k not in ("free space", "autocompact buffer", "messages")}
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
            "method": ("sum of pasted categories, excluding free space, the autocompact "
                       "buffer and messages (messages are the conversation, not startup)"),
        },
        "floor": {
            "value": (found.get("system prompt", 0) + found.get("system tools", 0)),
            "basis": "measured",
            "method": "system prompt plus built-in tools; nothing here can reduce either",
        },
        "maneuverable_remainder": {
            "value": sum(counted.values()) - (found.get("system prompt", 0)
                                              + found.get("system tools", 0)),
            "basis": "measured",
            "method": "startup total less the floor; report every saving against this",
        },
        "parsed_category_count": len(found),
        "skill_listing": listing,
    }


def classify_source(field: str) -> str | None:
    """Map a source field to an inventory source. The field, never the line."""
    lowered = field.lower()
    for word, mapped in SOURCE_WORDS:
        if word in lowered:
            return mapped
    return None


def parse_delimited_row(line: str) -> dict | None:
    """Read one row of the dot-separated skill listing."""
    if FIELD_SEPARATOR not in line:
        return None
    fields = [f.strip() for f in line.split(FIELD_SEPARATOR)]
    if len(fields) < 2:
        return None

    # Field 0 is the status glyph, the state word and the name.
    head = fields[0].strip(GLYPHS)
    tokens = head.split()
    while tokens and tokens[0].lower() in STATE_WORDS:
        tokens.pop(0)
    if not tokens:
        return None
    name = tokens[-1]
    state = next((t.lower() for t in fields[0].split() if t.lower() in STATE_WORDS), None)

    source = classify_source(fields[1])
    if source is None:
        return None

    cost = {"value": None, "basis": "unavailable", "note": "no cost shown on this row"}
    for field in fields[2:]:
        match = TOKEN_AMOUNT.match(field)
        if match:
            cost = {"value": to_tokens(match.group(1), match.group(2)),
                    "basis": "measured", "method": "pasted skill listing"}
            break

    tail = " ".join(fields[2:]).lower()
    # "locked by plugin" means no mechanism here reaches it. "locked by author"
    # is the entry's own `disable-model-invocation`, which the user owns and
    # can undo. Treating them alike would hide a reversible choice behind the
    # same wall as an unreachable one.
    return {
        "name": name,
        "source": source,
        "locked": "locked by plugin" in tail,
        "user_invocable_only": state == "user-only" or "locked by author" in tail,
        "disabled": state == "off",
        "prompt_cost": cost,
    }


def parse_skills(text: str) -> dict:
    """Extract per-skill rows from a pasted skill listing.

    Handles the client's dot-separated `/skills` listing and the markdown table
    the context breakdown prints, and falls back to a loose scan for anything
    else. Built-in rows matter most: they exist nowhere on disk, so a paste is
    the only route by which they can enter an inventory — and on a real machine
    they came from the context table, not from `/skills`, which does not list
    them at all.
    """
    rows: list[dict] = []
    seen: set[str] = set()

    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line) > 400:
            continue

        delimited = parse_delimited_row(line)
        if delimited:
            if delimited["name"] not in seen:
                seen.add(delimited["name"])
                rows.append(delimited)
            continue

        lowered = line.lower()

        # A markdown table row, which is how the context breakdown prints the
        # same information: | dataviz | Built-in | ~480 |
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 2 and cells[0].lower() not in LISTING_NOISE:
                source = classify_source(cells[1])
                name = cells[0]
                if source and name and name not in seen and " " not in name:
                    seen.add(name)
                    cost = {"value": None, "basis": "unavailable",
                            "note": "no cost shown on this row"}
                    for cell in cells[2:]:
                        match = TOKEN_AMOUNT.match(cell)
                        if match:
                            cost = {"value": to_tokens(match.group(1), match.group(2)),
                                    "basis": "measured", "method": "pasted context breakdown"}
                            break
                    rows.append({"name": name, "source": source, "locked": False,
                                 "user_invocable_only": False, "disabled": False,
                                 "prompt_cost": cost})
            continue

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
        locked = "locked by plugin" in lowered

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
        amount = re.search(AMOUNT + r"\s*(?:tokens?|tok)\b", line, re.I)
        if amount:
            cost = {"value": to_tokens(amount.group(1), amount.group(2)),
                    "basis": "measured", "method": "pasted skill listing"}

        rows.append({"name": name, "source": source, "locked": locked,
                     "user_invocable_only": "locked by author" in lowered,
                     "disabled": False, "prompt_cost": cost})

    return {
        "entries": rows,
        "parsed_entry_count": len(rows),
        "built_in_count": sum(1 for r in rows if r["source"] == "built-in"),
        "locked_count": sum(1 for r in rows if r["locked"]),
        "user_only_count": sum(1 for r in rows if r.get("user_invocable_only")),
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
    added, marked, marked_user_only, removed = [], [], [], []

    for row in skills.get("entries", []):
        # The listing prefixes a namespace the inventory does not carry:
        # `anthropic-skills:pdf` on disk is just `pdf`. Match either form.
        bare = row["name"].split(":", 1)[-1]
        existing = by_name.get(row["name"]) or by_name.get(bare)
        if existing is not None:
            if row["locked"] and not existing.get("locked_by_client"):
                existing["locked_by_client"] = True
                marked.append(row["name"])
            # "locked by author" is the entry's own disable-model-invocation.
            # The client is authoritative about it, so trust it over the
            # frontmatter parse, and keep the entry off the startup total.
            if row.get("user_invocable_only") and not existing.get("user_invocable_only"):
                existing["user_invocable_only"] = True
                if existing.get("loads_at_startup"):
                    existing["loads_at_startup"] = False
                    removed.append(existing)
                marked_user_only.append(row["name"])
            continue
        if row["source"] != "built-in":
            # A non-built-in row with no entry on disk is a disagreement worth
            # reporting, not a hole to fill with a fabricated path.
            continue
        disabled = row.get("disabled") or overrides.get(row["name"]) == "off"
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
            "user_invocable_only": bool(row.get("user_invocable_only")),
            "locked_by_client": row["locked"],
            "disabled_by_override": disabled,
            "loads_at_startup": not (disabled or row.get("user_invocable_only")),
            "note": "not on disk; seen only in the pasted skill listing",
        }
        inventory["entries"].append(entry)
        by_name[row["name"]] = entry
        added.append(row["name"])

    if added or removed:
        total = inventory["totals"]["startup_prompt_cost"]
        gained = sum((by_name[n]["prompt_cost"] or {}).get("value") or 0
                     for n in added if by_name[n]["loads_at_startup"])
        # An entry the client reports as user-only does not load, so its cost
        # has to come back out of the total the inventory already counted it
        # into. Adding without subtracting overstated the startup figure by
        # exactly the entries the client had told us were free.
        lost = sum((e.get("prompt_cost") or {}).get("value") or 0 for e in removed)
        total["value"] += gained - lost
        total["method"] += ("; plus measured built-in costs from the paste, "
                            "less entries the client reports as user-only")
        total["basis"] = "mixed"

    inventory["built_in_injection"] = {
        "added": added,
        "locked_marked": marked,
        "user_only_marked": marked_user_only,
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
    if context["startup_total"]["value"] != 29667:
        failures.append(f"context: startup total was {context['startup_total']['value']}, expected 29667")
    if context["startup_total"]["basis"] != "measured":
        failures.append("context: startup total not labelled measured")

    # Both readings of a comma, on one parser. A client that groups thousands
    # and a locale that writes decimals with a comma both reach this code, and
    # reading "1,340" as 1 is worse than failing: it is labelled measured.
    for text, suffix, expected in (("1,340", None, 1340), ("100,628", None, 100628),
                                   ("1,234,567", None, 1234567), ("2,4", "k", 2400),
                                   ("12.6", "k", 12600), ("653", None, 653)):
        got = to_tokens(text, suffix)
        if got != expected:
            failures.append(f"amount: {text}{suffix or ''} read as {got}, expected {expected}")
    grouped = parse_context("Memory files: 23,142 tokens\n")
    if grouped["categories"].get("memory files", {}).get("value") != 23142:
        failures.append("amount: a grouped figure in a pasted breakdown was truncated")

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

    # The real listing, with every trap the first parser fell into.
    listing = parse_skills(FIXTURE_SKILLS)
    rows = {r["name"]: r for r in listing["entries"]}
    if listing["parsed_entry_count"] != 5:
        failures.append(f"skills: read {listing['parsed_entry_count']} of 5 real rows")
    # A name containing "built-in" is not a built-in skill.
    browser = rows.get("anthropic-skills:built-in-browser", {})
    if browser.get("source") != "claude-ai-synced":
        failures.append(f"skills: a name containing 'built-in' was read as source "
                        f"{browser.get('source')}")
    if listing["built_in_count"] != 0:
        failures.append("skills: /skills does not list built-ins, yet some were reported")
    # "~150 tok", not "150 tokens".
    if rows.get("anthropic-skills:pdf", {}).get("prompt_cost", {}).get("value") != 150:
        failures.append("skills: the '~N tok' cost form was not read")
    # "user-only" contains "user"; the name still has to survive.
    if "release-plan" not in rows:
        failures.append("skills: a user-only row was dropped")
    elif not rows["release-plan"].get("user_invocable_only"):
        failures.append("skills: 'locked by author' was not read as user-invocable-only")
    elif rows["release-plan"].get("locked"):
        failures.append("skills: 'locked by author' was conflated with 'locked by plugin'")
    if not rows.get("claude-mem:mem-search", {}).get("locked"):
        failures.append("skills: 'locked by plugin' was not reported as locked")
    if not rows.get("anthropic-skills:pdf", {}).get("disabled"):
        failures.append("skills: an 'off' row was not reported as disabled")

    # Built-ins come from the context table instead.
    table = parse_skills(FIXTURE_BUILTIN_TABLE)
    table_rows = {r["name"]: r for r in table["entries"]}
    if table["built_in_count"] != 2:
        failures.append(f"skills: the context table yielded {table['built_in_count']} built-ins")
    if table_rows.get("dataviz", {}).get("prompt_cost", {}).get("value") != 480:
        failures.append("skills: a bare '~480' cost in a table row was not read")
    if "Skill" in table_rows:
        failures.append("skills: the table header was parsed as an entry")

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

    # Injection: built-ins enter from the context table, and the /skills
    # listing settles what is locked and what is merely user-invocable.
    target = {
        "entries": [
            {"name": "release-plan", "kind": "skill", "source": "user-global",
             "prompt_cost": {"value": 60, "basis": "estimated"}, "loads_at_startup": True},
            {"name": "mem-search", "kind": "skill", "source": "plugin",
             "prompt_cost": {"value": 50, "basis": "estimated"}, "loads_at_startup": True},
            {"name": "pdf", "kind": "skill", "source": "claude-ai-synced",
             "prompt_cost": {"value": 40, "basis": "estimated"}, "loads_at_startup": True},
        ],
        "skill_overrides": {},
        "totals": {"startup_prompt_cost": {"value": 150, "basis": "estimated",
                                           "method": "chars/4"}},
    }
    inject_skills(target, parse_skills(FIXTURE_SKILLS + FIXTURE_BUILTIN_TABLE))
    injected = {e["name"]: e for e in target["entries"]}

    if "dataviz" not in injected or injected["dataviz"]["source"] != "built-in":
        failures.append("inject: a built-in skill did not reach the inventory")
    elif injected["dataviz"]["prompt_cost"]["basis"] != "measured":
        failures.append("inject: a built-in cost was not labelled measured")
    # The plugin lock must reach the entry; the author lock must not be read as one.
    if not injected["mem-search"].get("locked_by_client"):
        failures.append("inject: a plugin-locked entry was not marked locked")
    if injected["release-plan"].get("locked_by_client"):
        failures.append("inject: 'locked by author' was marked as a client lock")
    if not injected["release-plan"].get("user_invocable_only") or \
            injected["release-plan"]["loads_at_startup"]:
        failures.append("inject: a user-only entry was still counted as loading")
    # A namespaced listing name must find the bare entry on disk, not duplicate it.
    if "anthropic-skills:pdf" in injected:
        failures.append("inject: a namespaced row duplicated an entry already on disk")
    if len([e for e in target["entries"] if e["name"] == "pdf"]) != 1:
        failures.append("inject: the synced entry was duplicated")
    if "anthropic-skills:built-in-browser" in injected:
        failures.append("inject: a synced row with no file on disk was fabricated")
    # 150, minus release-plan's 60 now that it is user-only, plus dataviz 480
    # and code-review 280.
    if target["totals"]["startup_prompt_cost"]["value"] != 850:
        failures.append(f"inject: total became "
                        f"{target['totals']['startup_prompt_cost']['value']}, expected 850")

    for line in failures:
        print(f"FAIL {line}")
    if failures:
        return 1
    print("self-test: all checks passed")
    print(f"  context categories parsed : {context['parsed_category_count']}")
    print(f"  measured startup total    : {thousands(context['startup_total']['value'])} ({context['startup_total']['basis']})")
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
        print(json.dumps(annotate(parse_context(read_source(args.source or "-"))), indent=2))
    elif args.command == "usage":
        print(json.dumps(annotate(parse_usage(read_source(args.source or "-"))), indent=2))
    elif args.command == "skills":
        print(json.dumps(annotate(parse_skills(read_source(args.source or "-"))), indent=2))
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
        print(json.dumps(annotate(inject_skills(inventory, rows)), indent=2))
    elif args.command == "reconcile":
        if not args.inventory:
            parser.error("reconcile needs --inventory")
        inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
        context = parse_context(Path(args.context).read_text(encoding="utf-8")) if args.context else None
        print(json.dumps(annotate(reconcile(inventory, context)), indent=2))
    else:
        parser.print_help()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
