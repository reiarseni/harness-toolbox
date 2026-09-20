#!/usr/bin/env python3
"""Analyse the health of a Claude Code skill portfolio.

Answers three questions the cost figures cannot:
  - which entries are actually invoked, as opposed to merely named somewhere;
  - which entries overlap each other, within a level or across levels;
  - which entries match the project's detected stack, and what the stack needs
    that nothing covers.

Every classification carries the evidence behind it. When the signals disagree,
the conflict is reported instead of being resolved silently.

Usage:
    python3 portfolio.py --inventory inv.json [--usage usage.json] [--json]
    python3 portfolio.py --self-test
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import time
from pathlib import Path

# A bare name matches far too much: "do" would hit "/doctor" and "/docs".
# An invocation is a slash command, a Skill() call, or a skill field in a log.
INVOCATION_TEMPLATES = (
    r'"display"\s*:\s*"/{name}(?![\w-])',
    r'Skill\(\s*"?{name}"?\s*\)',
    r'"skill"\s*:\s*"(?:[\w-]+:)?{name}"',
    r'skill:\s*"(?:[\w-]+:)?{name}"',
    r'(?<![\w-])/{name}(?![\w-])',
)

STACK_MARKERS = {
    "composer.json": ["php", "composer"],
    "artisan": ["php", "laravel"],
    "package.json": ["javascript", "node"],
    "vite.config.ts": ["vite", "frontend"],
    "vite.config.js": ["vite", "frontend"],
    "tsconfig.json": ["typescript"],
    "compose.yml": ["docker"],
    "docker-compose.yml": ["docker"],
    "Dockerfile": ["docker"],
    "pyproject.toml": ["python"],
    "requirements.txt": ["python"],
    "Cargo.toml": ["rust"],
    "go.mod": ["go"],
    "Gemfile": ["ruby"],
    "pom.xml": ["java"],
    "components.json": ["shadcn", "frontend"],
    ".gitlab-ci.yml": ["gitlab", "ci"],
    ".github": ["github", "ci"],
}

PACKAGE_HINTS = {
    "react": ["react", "frontend"],
    "vue": ["vue", "frontend"],
    "svelte": ["svelte", "frontend"],
    "tailwindcss": ["tailwind", "frontend"],
    "next": ["nextjs", "frontend"],
    "vitest": ["testing"],
    "jest": ["testing"],
    "laravel/framework": ["laravel", "php"],
    "pestphp/pest": ["testing", "php"],
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "with", "when", "use", "uses", "user",
    "this", "that", "from", "into", "your", "you", "it", "its", "to", "of", "in",
    "on", "by", "is", "are", "be", "as", "at", "not", "all", "any", "each", "then",
    "skill", "claude", "code", "project", "run", "runs", "using", "used", "one",
}

OVERLAP_THRESHOLD = 0.34


# --------------------------------------------------------------------------
# Usage detection
# --------------------------------------------------------------------------

def invocation_pattern(name: str) -> re.Pattern:
    escaped = re.escape(name)
    joined = "|".join(t.format(name=escaped) for t in INVOCATION_TEMPLATES)
    return re.compile(joined)


def count_invocations(name: str, corpus: str) -> int:
    return len(invocation_pattern(name).findall(corpus))


def count_mentions(name: str, corpus: str) -> int:
    """Loose count, kept only to show how misleading a bare search would be."""
    return len(re.findall(re.escape(name), corpus))


def current_session_id(explicit: str | None = None) -> str | None:
    """Identify the session running this audit, so its own log can be excluded."""
    for candidate in (explicit, os.environ.get("CLAUDE_SESSION_ID")):
        if candidate:
            return candidate.strip()
    return None


def load_history(config_dir: Path, log_limit: int = 40, skip_seconds: int = 900,
                 session_id: str | None = None) -> str:
    """Read the prompt history plus the most recent session logs.

    The audit contaminates its own corpus. The session running it writes a log
    like any other, and that log names every entry under discussion —
    repeatedly, including in the sentence proposing to cut it. On one run a
    synced skill went from `invocations=0` to `invocations=1` between two
    passes, purely because the intervening conversation had discussed it.

    Two defences, in that order. The session id is exact, so it is preferred
    whenever the caller can supply one. The `skip_seconds` window is the
    fallback for when it cannot: it is a blunt instrument that also discards
    legitimate recent sessions, which is the safer direction to err in.

    The walk is recursive, matching every other log survey in this skill.
    Subagent transcripts sit a level deeper than session logs, and a
    non-recursive glob silently read a different set of files from the one the
    retention survey counted.
    """
    parts = []
    history = config_dir / "history.jsonl"
    if history.exists():
        parts.append(history.read_text(encoding="utf-8", errors="replace"))
    projects = config_dir / "projects"
    if projects.is_dir():
        now = time.time()
        logs = []
        for path in projects.rglob("*.jsonl"):
            if session_id and session_id in str(path):
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if now - mtime <= skip_seconds:
                continue
            logs.append((mtime, path))
        logs.sort(reverse=True)
        for _, log in logs[:log_limit]:
            try:
                parts.append(log.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    return "\n".join(parts)


def load_usage_counters(config_dir: Path) -> dict[str, dict]:
    """Read the client's own per-skill invocation counters.

    `~/.claude.json` carries a `skillUsage` map of {name: {usageCount,
    lastUsedAt}}, maintained by the client itself. It is the authority, and it
    is cheap. Log scraping is not: on the installation this was added from, the
    corpus scan reported zero invocations for seven entries the counter showed
    had been used — `opsx:apply` 14 times, most recently five days earlier. The
    recommendation to suppress them carried "invocations=0 (measured)" as its
    evidence, and that evidence was false.

    Only `disable-model-invocation` kept that mistake from costing the user
    anything, because it leaves the entry invocable. Prefer this counter, fall
    back to the corpus, and say which one a number came from.
    """
    for candidate in (config_dir.parent / ".claude.json", config_dir / ".claude.json"):
        if not candidate.is_file():
            continue
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        usage = raw.get("skillUsage")
        if isinstance(usage, dict):
            return usage
    return {}


def normalise_usage_key(name: str) -> str:
    """Drop punctuation and case, so "OPSX: Apply" meets "opsx:apply".

    The namespace is deliberately kept. Stripping it made "do" match
    "claude-mem:do" and inherit its 99 invocations, which would have saved an
    unused skill from the cut on somebody else's evidence.
    """
    return re.sub(r"[^a-z0-9]", "", name.lower())


def load_startup_count(config_dir: Path) -> int | None:
    """Read `numStartups`, the horizon the counter's silence is measured over.

    "No entry in skillUsage" only means something once you can say over how
    long. Without this the same absence reads as a fresh install or as 664
    sessions of disuse, and only the second justifies a cut.
    """
    for candidate in (config_dir.parent / ".claude.json", config_dir / ".claude.json"):
        if not candidate.is_file():
            continue
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        value = raw.get("numStartups")
        if isinstance(value, int):
            return value
    return None


def lookup_counter(name: str, counters: dict[str, dict]) -> int | None:
    """Return an entry's invocation count, or None when it has no counter at all.

    None and 0 are different answers and the caller must be able to tell them
    apart. On a real installation with 66 counters, *not one* had a usageCount
    of 0: the client writes a key on first use and never writes a zero. So an
    absent key is the "never invoked" signal, and a present 0 would be an
    anomaly worth reporting rather than acting on.

    Matching is exact first, then on the whole normalised key. It never matches
    across namespaces.
    """
    if not name:
        return None
    hit = counters.get(name)
    if hit is None:
        wanted = normalise_usage_key(name)
        for key, value in counters.items():
            if normalise_usage_key(key) == wanted:
                hit = value
                break
    if not isinstance(hit, dict):
        return None
    count = hit.get("usageCount")
    return count if isinstance(count, int) else None


def find_near_miss_key(name: str, counters: dict[str, dict]) -> dict | None:
    """Find a counter key that almost matches `name`, for a renamed entry.

    A rename leaves the old key behind with all of the history under it. One
    installation carried both `karpaty-code-workflow` (6) and
    `karpathy-code-workflow` (36) — a single transposed letter apart. Had only
    the stale key existed, the live skill would have looked untouched.

    Reported, never counted: a near match is a reason to ask, not evidence.
    """
    if not name or name in counters:
        return None
    matches = difflib.get_close_matches(name, list(counters), n=1, cutoff=0.9)
    if not matches:
        return None
    key = matches[0]
    if normalise_usage_key(key) == normalise_usage_key(name):
        return None
    return {
        "key": key,
        "usage_count": (counters[key] or {}).get("usageCount"),
        "note": (f"skillUsage holds no '{name}' but does hold '{key}', one near-identical "
                 f"name apart. If this entry was renamed, its history is under the old key "
                 f"and the silence here is not evidence of disuse. Confirm before cutting."),
    }


def measure_usage(entries: list[dict], corpus: str, attribution: dict | None,
                  counters: dict[str, dict] | None = None,
                  startups: int | None = None) -> None:
    """Attach usage evidence to each entry, in place."""
    counters = counters or {}
    shares = {}
    if attribution:
        for item in attribution.get("entries", []):
            shares[item["name"]] = item["share_percent"]

    for entry in entries:
        name = entry.get("name", "")
        if entry["kind"] not in ("skill", "command", "agent"):
            continue
        counted = lookup_counter(name, counters)
        scraped = count_invocations(name, corpus) if name else 0
        near_miss = None
        if counters and entry["kind"] in ("skill", "command"):
            # The counter covers every skill and command, so its silence is
            # evidence, not absence of evidence. Trusting the scrape here
            # invents invocations: the corpus includes the session running this
            # audit, where every entry under discussion is named repeatedly. A
            # skill got "invocations=1" from the sentence proposing to cut it.
            if counted is None:
                # No key at all — which is how the client records "never used".
                # Say so in those words, and say over how long, because the
                # horizon is what turns the silence into an argument.
                invocations = 0
                horizon = (f" across {startups} recorded startups"
                           if isinstance(startups, int) else "")
                method = (f"no entry in the client skillUsage counter{horizon}; "
                          f"log scrape saw {scraped} and was not trusted over it")
                near_miss = find_near_miss_key(name, counters)
            else:
                invocations = counted
                method = (f"client skillUsage counter ({counted}); "
                          f"log scrape saw {scraped} and was not trusted over it")
        elif counted is None:
            invocations = scraped
            method = "delimited match over history and session logs"
        else:
            invocations = max(counted, scraped)
            method = (f"client skillUsage counter ({counted}); "
                      f"log scrape saw {scraped}")
        entry["usage"] = {
            "invocations": {"value": invocations, "basis": "measured",
                            "method": method},
            "loose_mentions": {"value": count_mentions(name, corpus) if name else 0,
                               "basis": "measured",
                               "method": "undelimited match, reported only to expose false positives"},
            "attribution_share": shares.get(name, {"value": None, "basis": "unavailable",
                                                   "note": "not present in the pasted usage report"}),
        }
        if near_miss:
            entry["usage"]["near_miss_counter"] = near_miss


# --------------------------------------------------------------------------
# Plugins: the only lever that reaches a plugin skill
# --------------------------------------------------------------------------

def load_plugin_counters(config_dir: Path) -> dict[str, dict]:
    """Read `pluginUsage`, the per-plugin counterpart of `skillUsage`.

    A plugin skill cannot be switched off on its own; the only lever is the
    whole plugin, which takes its hooks and its MCP servers with it. That trade
    used to be written out by hand. It should not be: the client already
    records how much each plugin is used, and on one installation the two
    answers were 100,628 invocations and 0. Those are not the same decision.
    """
    for candidate in (config_dir.parent / ".claude.json", config_dir / ".claude.json"):
        if not candidate.is_file():
            continue
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        usage = raw.get("pluginUsage")
        if isinstance(usage, dict):
            return usage
    return {}


def assess_plugins(entries: list[dict], plugin_counters: dict[str, dict],
                   startups: int | None = None) -> list[dict]:
    """Price the all-or-nothing choice for each enabled plugin.

    Reports, per plugin: what its skills cost at startup, how many of them are
    unused, what else disabling it would take away, and how often the client
    records the plugin being used. It states the trade; it never picks a side.
    """
    plugins: dict[str, dict] = {}
    for entry in entries:
        plugin = entry.get("plugin")
        if not plugin:
            continue
        row = plugins.setdefault(plugin, {
            "plugin": plugin, "skills": [], "unused_skills": [],
            "skill_cost": 0, "unused_cost": 0, "hooks": [], "mcp_servers": [],
        })
        if entry["kind"] == "skill":
            cost = (entry.get("prompt_cost") or {}).get("value") or 0
            row["skills"].append(entry["name"])
            if entry.get("loads_at_startup"):
                row["skill_cost"] += cost
            if (entry.get("usage", {}).get("invocations", {}).get("value") or 0) == 0:
                row["unused_skills"].append(entry["name"])
                if entry.get("loads_at_startup"):
                    row["unused_cost"] += cost
        elif entry["kind"] == "hook":
            row["hooks"].append(entry.get("event") or entry["name"])
        elif entry["kind"] == "mcp-server":
            row["mcp_servers"].append(entry["name"])

    results = []
    for name, row in plugins.items():
        counter = plugin_counters.get(name) or {}
        uses = counter.get("usageCount")
        uses = uses if isinstance(uses, int) else None
        losses = []
        if row["hooks"]:
            losses.append(f"{len(row['hooks'])} hook(s): {', '.join(sorted(set(row['hooks'])))}")
        if row["mcp_servers"]:
            losses.append(f"{len(row['mcp_servers'])} MCP server(s): {', '.join(row['mcp_servers'])}")

        if uses is None:
            verdict = ("no entry in the client pluginUsage counter"
                       + (f" across {startups} recorded startups" if startups else ""))
        elif uses == 0:
            verdict = "the client records zero uses of this plugin"
        else:
            verdict = f"the client records {uses:,} uses of this plugin"

        results.append({
            "plugin": name,
            "skill_count": len(row["skills"]),
            "unused_skill_count": len(row["unused_skills"]),
            "unused_skills": sorted(row["unused_skills"]),
            "startup_cost": {"value": row["skill_cost"], "basis": "estimated",
                             "method": "sum of its skills' description costs"},
            "recoverable_cost": {"value": row["unused_cost"], "basis": "estimated",
                                 "method": "sum of its unused skills' description costs"},
            "plugin_usage": {"value": uses,
                             "basis": "measured" if uses is not None else "unavailable",
                             "method": "client pluginUsage counter"},
            "also_lost": losses,
            "mechanism": "whole plugin only, via /plugin or enabledPlugins",
            "trade": (
                f"Disabling {name} recovers at most {row['skill_cost']} tokens "
                f"({row['unused_cost']} of it from skills nothing has invoked)"
                + (f" and costs {'; '.join(losses)}" if losses else " and costs nothing else")
                + f". {verdict}."
            ),
            "decision": "propose-only; never applied by this skill",
        })
    return sorted(results, key=lambda r: -r["startup_cost"]["value"])


# --------------------------------------------------------------------------
# Overlap
# --------------------------------------------------------------------------

def keywords(text: str) -> set[str]:
    words = re.findall(r"[a-z][a-z-]{2,}", (text or "").lower())
    return {w for w in words if w not in STOPWORDS}


def normalised_name(name: str) -> str:
    base = re.sub(r"^[\w-]+:", "", name or "").lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    for prefix in ("openspec-", "opsx-"):
        if base.startswith(prefix):
            base = base[len(prefix):]
    for suffix in ("-change", "-changes"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return base


def find_overlaps(entries: list[dict]) -> list[dict]:
    candidates = [e for e in entries if e["kind"] in ("skill", "command", "agent")]
    overlaps = []
    for i, left in enumerate(candidates):
        for right in candidates[i + 1:]:
            same_name = normalised_name(left["name"]) == normalised_name(right["name"])
            left_words, right_words = keywords(left.get("description")), keywords(right.get("description"))
            union = left_words | right_words
            similarity = len(left_words & right_words) / len(union) if union else 0.0
            if not same_name and similarity < OVERLAP_THRESHOLD:
                continue
            overlaps.append({
                "entries": [
                    {"name": left["name"], "kind": left["kind"], "source": left["source"]},
                    {"name": right["name"], "kind": right["kind"], "source": right["source"]},
                ],
                "same_normalised_name": same_name,
                "shared_keywords": sorted(left_words & right_words)[:8],
                "similarity": round(similarity, 2),
                "cross_level": left["source"] != right["source"],
                "reason": "same capability name" if same_name else "overlapping description",
            })
    return sorted(overlaps, key=lambda o: (not o["same_normalised_name"], -o["similarity"]))


# --------------------------------------------------------------------------
# Stack detection and coverage
# --------------------------------------------------------------------------

def _scan_manifest(path: Path, keys: tuple[str, ...], tags: set[str], evidence: list[str],
                   label: str) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return
    declared: dict = {}
    for key in keys:
        declared.update(data.get(key) or {})
    for dep, dep_tags in PACKAGE_HINTS.items():
        if dep in declared:
            tags.update(dep_tags)
            evidence.append(f"{label}:{dep}")


def stack_search_roots(project: Path, depth: int = 1) -> list[Path]:
    """The project root plus its immediate subdirectories.

    A monorepo keeps its manifests one level down (backend/, frontend/, apps/…),
    so a root-only scan would miss most of the stack.
    """
    roots = [project]
    if depth < 1:
        return roots
    skip = {"node_modules", "vendor", "dist", "build", "target", ".git", "storage"}
    try:
        for child in sorted(project.iterdir()):
            if child.is_dir() and not child.name.startswith(".") and child.name not in skip:
                roots.append(child)
    except OSError:
        pass
    return roots


def detect_stack(project: Path, depth: int = 1) -> dict:
    tags: set[str] = set()
    evidence: list[str] = []
    for root in stack_search_roots(project, depth):
        prefix = "" if root == project else f"{root.name}/"
        for marker, marker_tags in STACK_MARKERS.items():
            if (root / marker).exists():
                tags.update(marker_tags)
                evidence.append(f"{prefix}{marker}")
        package = root / "package.json"
        if package.exists():
            _scan_manifest(package, ("dependencies", "devDependencies"),
                           tags, evidence, f"{prefix}package.json")
        composer = root / "composer.json"
        if composer.exists():
            _scan_manifest(composer, ("require", "require-dev"),
                           tags, evidence, f"{prefix}composer.json")
    return {"tags": sorted(tags), "evidence": sorted(evidence), "basis": "measured",
            "method": f"marker files and declared dependencies, root plus {depth} level(s) down"}


def assess_coverage(entries: list[dict], stack: dict) -> dict:
    tags = set(stack["tags"])
    covered: dict[str, list[str]] = {tag: [] for tag in tags}
    unrelated = []

    for entry in entries:
        if entry["kind"] not in ("skill", "command", "agent"):
            continue
        words = keywords(entry.get("description")) | keywords(entry.get("name"))
        matched = sorted(tags & words)
        if matched:
            for tag in matched:
                covered[tag].append(entry["name"])
        else:
            unrelated.append({"name": entry["name"], "source": entry["source"]})

    return {
        "stack": stack,
        "covered": {tag: names for tag, names in covered.items() if names},
        "gaps": sorted(tag for tag, names in covered.items() if not names),
        "unrelated_to_stack": unrelated,
    }


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------

def classify(entries: list[dict], coverage: dict, overlaps: list[dict]) -> list[dict]:
    sole_cover = {
        names[0]: tag for tag, names in coverage["covered"].items() if len(names) == 1
    }
    overlapping = {e["name"] for o in overlaps for e in o["entries"]}
    unrelated = {u["name"] for u in coverage["unrelated_to_stack"]}

    results = []
    for entry in entries:
        if entry["kind"] not in ("skill", "command", "agent"):
            continue
        name = entry["name"]
        usage = entry.get("usage", {})
        invocations = usage.get("invocations", {}).get("value", 0) or 0
        share = usage.get("attribution_share", {}).get("value")
        evidence = [f"invocations={invocations} (measured)"]
        if share is not None:
            evidence.append(f"attribution={share}% (measured)")
        else:
            evidence.append("attribution unavailable")

        near_miss = usage.get("near_miss_counter")
        if near_miss:
            evidence.append(f"near-identical counter key '{near_miss['key']}' "
                            f"={near_miss.get('usage_count')} (measured)")

        if not entry.get("loads_at_startup"):
            action, reason = "keep", "already costs nothing at startup"
        elif near_miss and invocations == 0:
            # A rename leaves the history under the old key. That is a
            # conflicting signal, and the rule for a conflict is to keep the
            # entry and say why, never to resolve it quietly.
            action = "keep"
            reason = (f"conflicting signals: no counter under '{name}', but "
                      f"'{near_miss['key']}' is one near-identical name apart with "
                      f"{near_miss.get('usage_count')} uses. Confirm whether this entry "
                      f"was renamed before cutting it")
        elif name in sole_cover and invocations == 0:
            action = "keep"
            reason = (f"conflicting signals: no invocations recorded, but it is the only entry "
                      f"covering '{sole_cover[name]}' in the detected stack")
            evidence.append(f"sole coverage of {sole_cover[name]}")
        elif invocations > 0 and name in unrelated:
            action = "keep"
            reason = "used in practice even though it does not match the detected stack"
        elif invocations == 0 and name in unrelated:
            action = "suppress"
            reason = "no recorded invocation and unrelated to the detected stack"
        elif invocations == 0:
            action = "suppress"
            reason = "no recorded invocation across history and session logs"
        elif name in overlapping:
            action = "review-overlap"
            reason = "used, but overlaps another entry; decide which one to keep"
        else:
            action = "keep"
            reason = "used in practice"

        results.append({
            "name": name,
            "kind": entry["kind"],
            "source": entry["source"],
            "cost": entry.get("prompt_cost"),
            "action": action,
            "reason": reason,
            "evidence": evidence,
        })

    order = {"suppress": 0, "review-overlap": 1, "keep": 2}
    return sorted(results, key=lambda r: (order.get(r["action"], 3),
                                          -(r["cost"] or {}).get("value", 0)))


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

def self_test() -> int:
    failures = []

    corpus = ('{"display":"/doctor"}\n{"display":"/docs"}\n{"display":"/documake"}\n'
              '{"display":"/do"}\nSkill(do)\n')
    if count_invocations("do", corpus) != 2:
        failures.append(f"delimited match counted {count_invocations('do', corpus)} for 'do', expected 2")
    if count_mentions("do", corpus) < 5:
        failures.append("loose match should have produced the false positives it is there to expose")

    entries = [
        {"kind": "skill", "name": "release-plan", "source": "user-global",
         "description": "Analyse pending changes and generate a multi-MR plan.",
         "prompt_cost": {"value": 73, "basis": "estimated"}, "loads_at_startup": True},
        {"kind": "skill", "name": "packaging-pyqt5", "source": "user-global",
         "description": "Packages PyQt5 desktop apps for Linux and Windows installers.",
         "prompt_cost": {"value": 79, "basis": "estimated"}, "loads_at_startup": True},
        {"kind": "skill", "name": "openspec-apply-change", "source": "project",
         "description": "Implement tasks from an OpenSpec change.",
         "prompt_cost": {"value": 33, "basis": "estimated"}, "loads_at_startup": True},
        {"kind": "command", "name": "opsx:apply", "source": "project",
         "description": "Implement tasks from an OpenSpec change (Experimental)",
         "prompt_cost": {"value": 13, "basis": "estimated"}, "loads_at_startup": True},
        {"kind": "skill", "name": "docker-patterns", "source": "project",
         "description": "Docker and Docker Compose patterns for local development.",
         "prompt_cost": {"value": 35, "basis": "estimated"}, "loads_at_startup": True},
    ]
    history = '{"display":"/release-plan"}\n' * 212
    measure_usage(entries, history, None)
    if entries[0]["usage"]["invocations"]["value"] != 212:
        failures.append("an entry invoked 212 times was not counted")

    # The client's counter outranks a silent log corpus, and reaches an entry
    # whose display name carries a namespace. Getting this wrong is what made a
    # real run report "invocations=0" for an entry used 14 times.
    counters = {"opsx:apply": {"usageCount": 14, "lastUsedAt": 0}}
    measure_usage(entries, "", None, counters, 664)
    quiet = next(e for e in entries if e["name"] == "opsx:apply")
    if quiet["usage"]["invocations"]["value"] != 14:
        failures.append("the client usage counter was ignored")
    if "skillUsage" not in quiet["usage"]["invocations"]["method"]:
        failures.append("the counter was used without saying so in the method")
    if lookup_counter("OPSX: Apply", counters) != 14:
        failures.append("a display name did not reach its namespaced counter")

    # A namespace is never crossed. "do" borrowing "claude-mem:do"'s 99 uses
    # would have saved an unused skill on another entry's evidence.
    if lookup_counter("do", {"claude-mem:do": {"usageCount": 99}}) is not None:
        failures.append("a bare name matched a namespaced counter key")
    if lookup_counter("apply", {"opsx:apply": {"usageCount": 14}}) is not None:
        failures.append("a bare name matched a namespaced counter key")

    # Absence and zero are different answers. The client writes a key on first
    # use and never writes a zero, so "no key" is the never-invoked signal and
    # the report must say so in those words, with the horizon attached.
    absent = next(e for e in entries if e["name"] == "packaging-pyqt5")
    method = absent["usage"]["invocations"]["method"]
    if "no entry in the client skillUsage counter" not in method:
        failures.append(f"an absent counter was reported as a zero: {method}")
    if "664 recorded startups" not in method:
        failures.append("the counter's silence was reported without its horizon")

    # A renamed entry keeps its history under the stale key. Report it, never
    # count it, and never cut on the strength of the silence.
    renamed = [{"kind": "skill", "name": "karpathy-code-workflow", "source": "user-global",
                "description": "Apply a code-first agentic workflow.",
                "prompt_cost": {"value": 60, "basis": "estimated"}, "loads_at_startup": True}]
    stale = {"karpaty-code-workflow": {"usageCount": 36, "lastUsedAt": 0}}
    measure_usage(renamed, "", None, stale, 664)
    warning = renamed[0]["usage"].get("near_miss_counter")
    if not warning or warning["key"] != "karpaty-code-workflow":
        failures.append("a stale counter key from a rename was not reported")
    if renamed[0]["usage"]["invocations"]["value"] != 0:
        failures.append("a near-miss counter was counted as this entry's own usage")
    renamed_verdict = classify(renamed, {"covered": {}, "unrelated_to_stack": []}, [])[0]
    if renamed_verdict["action"] != "keep":
        failures.append("an entry with a stale counter key was proposed for removal")

    measure_usage(entries, history, None, None)

    # The all-or-nothing plugin trade is calculated, not narrated. A plugin the
    # client records 100,628 uses of is not the same decision as one at zero,
    # and the hooks and MCP servers that go with it must be named.
    plugin_entries = [
        {"kind": "skill", "name": "mem-search", "source": "plugin", "plugin": "memory@vendor",
         "description": "Search memory.", "prompt_cost": {"value": 200, "basis": "estimated"},
         "loads_at_startup": True, "usage": {"invocations": {"value": 0}}},
        {"kind": "skill", "name": "timeline", "source": "plugin", "plugin": "memory@vendor",
         "description": "Timeline report.", "prompt_cost": {"value": 140, "basis": "estimated"},
         "loads_at_startup": True, "usage": {"invocations": {"value": 9}}},
        {"kind": "hook", "name": "SessionStart", "event": "SessionStart",
         "source": "hook-plugin", "plugin": "memory@vendor"},
        {"kind": "mcp-server", "name": "mcp-search", "source": "mcp-plugin",
         "plugin": "memory@vendor"},
    ]
    priced = assess_plugins(plugin_entries,
                            {"memory@vendor": {"usageCount": 100628}}, 664)[0]
    if priced["startup_cost"]["value"] != 340:
        failures.append(f"plugin startup cost was {priced['startup_cost']['value']}, expected 340")
    if priced["recoverable_cost"]["value"] != 200:
        failures.append("the recoverable share was not limited to the unused skills")
    if priced["plugin_usage"]["value"] != 100628:
        failures.append("the pluginUsage counter was not read")
    if "SessionStart" not in priced["trade"] or "mcp-search" not in priced["trade"]:
        failures.append("the trade did not name what else disabling the plugin costs")
    if priced["decision"] != "propose-only; never applied by this skill":
        failures.append("a plugin block was not marked propose-only")
    silent = assess_plugins(plugin_entries, {}, 664)[0]
    if "no entry in the client pluginUsage counter" not in silent["trade"]:
        failures.append("a plugin with no counter was not reported as such")
    if "664" not in silent["trade"]:
        failures.append("the plugin counter's silence was reported without its horizon")

    # The audit must not read its own transcript. The session id is exact; the
    # time window is the fallback for when no id is available. A deep log must
    # still be reachable, because the walk is recursive like every other here.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        logs_config = Path(tmp)
        deep = logs_config / "projects" / "-proj" / "sess" / "subagents"
        deep.mkdir(parents=True)
        own = logs_config / "projects" / "-proj" / "7f3a-this-session.jsonl"
        own.write_text('{"display":"/release-plan"}\n' * 5, encoding="utf-8")
        (deep / "agent-1.jsonl").write_text('{"display":"/levantar"}\n', encoding="utf-8")
        stale = time.time() - 86400
        for path in (own, deep / "agent-1.jsonl"):
            os.utime(path, (stale, stale))
        corpus_all = load_history(logs_config)
        if count_invocations("levantar", corpus_all) != 1:
            failures.append("the recursive walk did not reach a subagent transcript")
        if count_invocations("release-plan", corpus_all) != 5:
            failures.append("the fixture session log was not read at all")
        corpus_excluded = load_history(logs_config, session_id="7f3a-this-session")
        if count_invocations("release-plan", corpus_excluded) != 0:
            failures.append("the audit's own session log was counted as evidence")
        if count_invocations("levantar", corpus_excluded) != 1:
            failures.append("excluding one session removed unrelated logs too")
        if load_history(logs_config, skip_seconds=10**9) != "":
            failures.append("the time-window fallback did not skip recent logs")

    overlaps = find_overlaps(entries)
    pair = {"openspec-apply-change", "opsx:apply"}
    if not any(pair == {e["name"] for e in o["entries"]} for o in overlaps):
        failures.append("the command and skill covering the same workflow were not paired")

    stack = {"tags": ["docker", "frontend"], "evidence": ["compose.yml"],
             "basis": "measured", "method": "test fixture"}
    coverage = assess_coverage(entries, stack)
    if "packaging-pyqt5" not in {u["name"] for u in coverage["unrelated_to_stack"]}:
        failures.append("a desktop packaging skill was not flagged as unrelated to a web stack")
    if "frontend" not in coverage["gaps"]:
        failures.append("an uncovered stack tag was not reported as a gap")

    classified = classify(entries, coverage, overlaps)
    by_name = {c["name"]: c for c in classified}
    if by_name["release-plan"]["action"] == "suppress":
        failures.append("an entry with 212 invocations was proposed for removal")
    if by_name["packaging-pyqt5"]["action"] != "suppress":
        failures.append("an unused entry unrelated to the stack was not proposed for removal")
    sole = by_name["docker-patterns"]
    if sole["action"] != "keep" or "conflicting signals" not in sole["reason"]:
        failures.append("an unused entry that solely covers a stack tag was not protected")

    for line in failures:
        print(f"FAIL {line}")
    if failures:
        return 1
    print("self-test: all checks passed")
    print(f"  'do' delimited / loose     : {count_invocations('do', corpus)} / {count_mentions('do', corpus)}")
    print(f"  namespace never crossed    : 'do' vs 'claude-mem:do' -> no match")
    print(f"  absent counter             : reported as absence, over 664 startups")
    print(f"  stale key from a rename    : reported, kept, never counted")
    print(f"  own session log            : excluded by id, recursive walk intact")
    print(f"  plugin trade priced        : {priced['trade']}")
    print(f"  overlaps found             : {len(overlaps)}")
    print(f"  unrelated to stack         : {[u['name'] for u in coverage['unrelated_to_stack']]}")
    print(f"  stack gaps                 : {coverage['gaps']}")
    print(f"  release-plan               : {by_name['release-plan']['action']}")
    print(f"  docker-patterns            : {sole['action']} — {sole['reason']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", help="inventory JSON produced by inventory.py")
    parser.add_argument("--usage", help="usage JSON produced by measured.py usage")
    parser.add_argument("--session-id", help="this session's id, so its own log is excluded "
                                             "exactly rather than by the time window")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.inventory:
        parser.error("--inventory is required")

    inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
    entries = inventory["entries"]
    config_dir = Path(inventory["paths"]["config_dir"])
    project = Path(inventory["paths"]["project_root"])

    attribution = json.loads(Path(args.usage).read_text(encoding="utf-8")) if args.usage else None
    startups = load_startup_count(config_dir)
    session = current_session_id(args.session_id)
    measure_usage(entries, load_history(config_dir, session_id=session), attribution,
                  load_usage_counters(config_dir), startups)
    overlaps = find_overlaps(entries)
    coverage = assess_coverage(entries, detect_stack(project))
    classified = classify(entries, coverage, overlaps)
    plugins = assess_plugins(entries, load_plugin_counters(config_dir), startups)

    report = {"coverage": coverage, "overlaps": overlaps,
              "plugins": plugins, "classification": classified}

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"stack: {', '.join(coverage['stack']['tags']) or 'none detected'} "
          f"({coverage['stack']['basis']}, from {', '.join(coverage['stack']['evidence'][:6])})")
    print(f"gaps: {', '.join(coverage['gaps']) or 'none'}")
    print(f"\noverlapping pairs: {len(overlaps)}")
    for overlap in overlaps[:10]:
        left, right = overlap["entries"]
        print(f"  {left['name']} ({left['source']}) ~ {right['name']} ({right['source']}) "
              f"— {overlap['reason']}, similarity {overlap['similarity']}")

    if plugins:
        print(f"\nplugins (all-or-nothing, propose-only): {len(plugins)}")
        for row in plugins:
            print(f"  {row['trade']}")

    print(f"\n{'action':<16}{'name':<34}{'cost':>6}  reason")
    for item in classified:
        cost = (item["cost"] or {}).get("value")
        print(f"{item['action']:<16}{item['name'][:33]:<34}{cost if cost is not None else '-':>6}  {item['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
