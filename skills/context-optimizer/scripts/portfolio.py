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
import json
import re
import sys
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


def load_history(config_dir: Path, log_limit: int = 40) -> str:
    """Read the prompt history plus the most recent session logs."""
    parts = []
    history = config_dir / "history.jsonl"
    if history.exists():
        parts.append(history.read_text(encoding="utf-8", errors="replace"))
    projects = config_dir / "projects"
    if projects.is_dir():
        logs = sorted(projects.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        for log in logs[:log_limit]:
            try:
                parts.append(log.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    return "\n".join(parts)


def measure_usage(entries: list[dict], corpus: str, attribution: dict | None) -> None:
    """Attach usage evidence to each entry, in place."""
    shares = {}
    if attribution:
        for item in attribution.get("entries", []):
            shares[item["name"]] = item["share_percent"]

    for entry in entries:
        name = entry.get("name", "")
        if entry["kind"] not in ("skill", "command", "agent"):
            continue
        invocations = count_invocations(name, corpus) if name else 0
        entry["usage"] = {
            "invocations": {"value": invocations, "basis": "measured",
                            "method": "delimited match over history and session logs"},
            "loose_mentions": {"value": count_mentions(name, corpus) if name else 0,
                               "basis": "measured",
                               "method": "undelimited match, reported only to expose false positives"},
            "attribution_share": shares.get(name, {"value": None, "basis": "unavailable",
                                                   "note": "not present in the pasted usage report"}),
        }


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

        if not entry.get("loads_at_startup"):
            action, reason = "keep", "already costs nothing at startup"
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
    measure_usage(entries, load_history(config_dir), attribution)
    overlaps = find_overlaps(entries)
    coverage = assess_coverage(entries, detect_stack(project))
    classified = classify(entries, coverage, overlaps)

    report = {"coverage": coverage, "overlaps": overlaps, "classification": classified}

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

    print(f"\n{'action':<16}{'name':<34}{'cost':>6}  reason")
    for item in classified:
        cost = (item["cost"] or {}).get("value")
        print(f"{item['action']:<16}{item['name'][:33]:<34}{cost if cost is not None else '-':>6}  {item['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
