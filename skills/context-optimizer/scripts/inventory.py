#!/usr/bin/env python3
"""Inventory every source that consumes Claude Code startup context.

Walks the five skill sources, agents, commands, MCP servers, CLAUDE.md chains and
hooks, then reports each entry with its origin, real path and cost.

Every numeric field carries a "basis" of either "measured" or "estimated" so the
caller can never mistake one for the other. Byte-derived figures are always
"estimated": the divide-by-four heuristic underestimates non-English text and
text containing code.

Paths are detected, never hardcoded. Symlinks are resolved to their real target.

Usage:
    python3 inventory.py [--project DIR] [--config DIR] [--measure-hooks] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

CHARS_PER_TOKEN = 4
FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.S)
IMPORT_LINE = re.compile(r"^@([^\s]+)\s*$", re.M)
HOOK_EVENTS_THAT_INJECT = ("SessionStart", "UserPromptSubmit")


# --------------------------------------------------------------------------
# Path detection
# --------------------------------------------------------------------------

def detect_config_dir(override: str | None = None) -> Path | None:
    """Locate the Claude Code configuration directory without assuming a path."""
    for candidate in (override, os.environ.get("CLAUDE_CONFIG_DIR")):
        if candidate:
            path = Path(candidate).expanduser()
            return path if path.is_dir() else None
    default = Path.home() / ".claude"
    return default if default.is_dir() else None


def detect_project_root(start: str | None = None) -> Path:
    """Walk up from `start` looking for a project marker, else return `start`."""
    current = Path(start).expanduser().resolve() if start else Path.cwd()
    for directory in (current, *current.parents):
        if (directory / ".git").exists() or (directory / ".claude").is_dir():
            return directory
    return current


def real_path(path: Path) -> Path:
    """Resolve a symlink to the file that actually holds the content."""
    try:
        return path.resolve(strict=False)
    except OSError:
        return path


# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------

def estimate_tokens(char_count: int) -> dict:
    return {
        "value": char_count // CHARS_PER_TOKEN,
        "basis": "estimated",
        "method": f"chars/{CHARS_PER_TOKEN}",
    }


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}


def parse_frontmatter(text: str) -> dict:
    """Extract the keys this tool needs. Deliberately not a full YAML parser."""
    match = FRONTMATTER.search(text)
    if not match:
        return {}
    block = match.group(1)
    out: dict = {"_raw": block}
    name = re.search(r"^name:\s*(.+)$", block, re.M)
    if name:
        out["name"] = name.group(1).strip()
    # description may wrap until the next top-level key
    desc = re.search(r"^description:\s*(.*?)(?=^[A-Za-z_-]+:|\Z)", block, re.M | re.S)
    if desc:
        out["description"] = " ".join(desc.group(1).split())
    out["disable_model_invocation"] = bool(
        re.search(r"^disable-model-invocation:\s*true\s*$", block, re.M | re.I)
    )
    return out


def skill_entry(skill_md: Path, source: str, *, plugin: str | None = None) -> dict:
    target = real_path(skill_md)
    text = read_text(target)
    meta = parse_frontmatter(text)
    description = meta.get("description", "")
    name = meta.get("name") or skill_md.parent.name
    entry = {
        "kind": "skill",
        "name": name,
        "source": source,
        "link_path": str(skill_md),
        "real_path": str(target),
        "is_symlink": skill_md.is_symlink() or skill_md.parent.is_symlink(),
        "description": description,
        "description_chars": len(description),
        "body_chars": len(text),
        "prompt_cost": estimate_tokens(len(description)),
        "body_cost": estimate_tokens(len(text)),
        "user_invocable_only": meta.get("disable_model_invocation", False),
    }
    if plugin:
        entry["plugin"] = plugin
    return entry


# --------------------------------------------------------------------------
# Skills, commands, agents
# --------------------------------------------------------------------------

def newest_version_dir(plugin_dir: Path) -> Path | None:
    versions = [d for d in plugin_dir.iterdir() if d.is_dir()] if plugin_dir.is_dir() else []
    if not versions:
        return None
    return max(versions, key=lambda d: d.stat().st_mtime)


def enabled_plugins(config: Path) -> dict:
    settings = read_json(config / "settings.json")
    return {k: v for k, v in (settings.get("enabledPlugins") or {}).items() if v}


def collect_skills(config: Path, project: Path) -> list[dict]:
    entries: list[dict] = []
    skills_root = config / "skills"

    if skills_root.is_dir():
        for child in sorted(skills_root.iterdir()):
            if child.name == "synced":
                continue
            skill_md = child / "SKILL.md"
            if skill_md.exists():
                entries.append(skill_entry(skill_md, "user-global"))
        synced = skills_root / "synced"
        if synced.is_dir():
            # Synced skills are filed under one bucket per account or
            # organisation, and the same skill is commonly present in several
            # of them, byte for byte. The client registers the name once; a
            # naive walk counts it once per bucket. On a live installation that
            # turned 12 synced skills into 24 and would have doubled the
            # estimated saving for the whole block.
            seen: dict[str, dict] = {}
            for skill_md in sorted(synced.glob("*/*/SKILL.md")):
                entry = skill_entry(skill_md, "claude-ai-synced")
                first = seen.get(entry["name"])
                if first is None:
                    entry["duplicate_copies"] = []
                    seen[entry["name"]] = entry
                    entries.append(entry)
                else:
                    first["duplicate_copies"].append(str(skill_md))

    for plugin_id in enabled_plugins(config):
        plugin_name, _, marketplace = plugin_id.partition("@")
        plugin_dir = config / "plugins" / "cache" / marketplace / plugin_name
        version = newest_version_dir(plugin_dir)
        if not version:
            continue
        for skill_md in sorted(version.glob("skills/*/SKILL.md")):
            entries.append(skill_entry(skill_md, "plugin", plugin=plugin_id))

    for skill_md in sorted((project / ".claude" / "skills").glob("*/SKILL.md")):
        entries.append(skill_entry(skill_md, "project"))

    return entries


def collect_markdown_entries(root: Path, kind: str, source: str) -> list[dict]:
    if not root.is_dir():
        return []
    entries = []
    for path in sorted(root.rglob("*.md")):
        target = real_path(path)
        text = read_text(target)
        meta = parse_frontmatter(text)
        description = meta.get("description", "")
        entries.append({
            "kind": kind,
            "name": meta.get("name") or path.stem,
            "source": source,
            "link_path": str(path),
            "real_path": str(target),
            "is_symlink": path.is_symlink(),
            "description": description,
            "description_chars": len(description),
            "body_chars": len(text),
            "prompt_cost": estimate_tokens(len(description)),
            "body_cost": estimate_tokens(len(text)),
            "user_invocable_only": meta.get("disable_model_invocation", False),
        })
    return entries


# --------------------------------------------------------------------------
# MCP servers
# --------------------------------------------------------------------------

def collect_mcp(config: Path, project: Path) -> list[dict]:
    """Read user scope, project scope and plugin-provided MCP servers.

    Project-scope servers may live inside the projects entry of the root config
    file with no .mcp.json present anywhere, so both locations are checked.
    """
    entries: list[dict] = []
    root_config = config.parent / ".claude.json"
    data = read_json(root_config)

    for name, spec in (data.get("mcpServers") or {}).items():
        entries.append(_mcp_entry(name, spec, "user", str(root_config)))

    project_key = str(project)
    project_entry = (data.get("projects") or {}).get(project_key, {})
    for name, spec in (project_entry.get("mcpServers") or {}).items():
        entries.append(_mcp_entry(name, spec, "project", f"{root_config} → projects[{project_key}]"))

    mcp_file = project / ".mcp.json"
    if mcp_file.exists():
        for name, spec in (read_json(mcp_file).get("mcpServers") or {}).items():
            entries.append(_mcp_entry(name, spec, "project", str(mcp_file)))

    for plugin_id in enabled_plugins(config):
        plugin_name, _, marketplace = plugin_id.partition("@")
        version = newest_version_dir(config / "plugins" / "cache" / marketplace / plugin_name)
        if not version:
            continue
        for name, spec in (read_json(version / ".mcp.json").get("mcpServers") or {}).items():
            entry = _mcp_entry(name, spec, "plugin", str(version / ".mcp.json"))
            entry["plugin"] = plugin_id
            entries.append(entry)

    return entries


def _mcp_entry(name: str, spec: dict, scope: str, declared_in: str) -> dict:
    return {
        "kind": "mcp-server",
        "name": name,
        "source": f"mcp-{scope}",
        "transport": spec.get("type", "stdio"),
        "command": spec.get("command"),
        "declared_in": declared_in,
        "tool_count": {"value": None, "basis": "unavailable",
                       "note": "tool counts and schema size come from the pasted context breakdown"},
        "prompt_cost": {"value": None, "basis": "unavailable",
                        "note": "schemas are deferred; only names load at startup"},
    }


# --------------------------------------------------------------------------
# CLAUDE.md chains and hooks
# --------------------------------------------------------------------------

def collect_claude_md(config: Path, project: Path) -> list[dict]:
    """Measure instruction files and follow their @import chains."""
    seen: set[Path] = set()
    entries: list[dict] = []

    def walk(path: Path, source: str, imported_by: str | None = None) -> None:
        target = real_path(path)
        if not target.exists() or target in seen:
            return
        seen.add(target)
        text = read_text(target)
        entries.append({
            "kind": "instructions",
            "name": str(target.name),
            "source": source,
            "real_path": str(target),
            "imported_by": imported_by,
            "body_chars": len(text),
            "prompt_cost": estimate_tokens(len(text)),
        })
        for raw in IMPORT_LINE.findall(text):
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute():
                candidate = target.parent / candidate
            walk(candidate, source, imported_by=str(target))

    walk(config / "CLAUDE.md", "instructions-global")
    walk(project / "CLAUDE.md", "instructions-project")
    return entries


def collect_hooks(config: Path, *, measure: bool = False, project: Path | None = None) -> list[dict]:
    """List hooks, and optionally measure what the injecting ones actually emit."""
    entries: list[dict] = []
    sources = [(config / "settings.json", "settings", None)]
    for plugin_id in enabled_plugins(config):
        plugin_name, _, marketplace = plugin_id.partition("@")
        version = newest_version_dir(config / "plugins" / "cache" / marketplace / plugin_name)
        if version:
            sources.append((version / "hooks" / "hooks.json", "plugin", plugin_id))

    for path, kind, plugin_id in sources:
        hooks = (read_json(path).get("hooks") or {})
        for event, matchers in hooks.items():
            for matcher in matchers or []:
                for hook in matcher.get("hooks") or []:
                    entry = {
                        "kind": "hook",
                        "name": f"{event}",
                        "source": f"hook-{kind}",
                        "event": event,
                        "declared_in": str(path),
                        "injects_context": event in HOOK_EVENTS_THAT_INJECT,
                        "protected": True,
                        "output_chars": {"value": None, "basis": "unavailable"},
                        "prompt_cost": {"value": None, "basis": "unavailable"},
                    }
                    if plugin_id:
                        entry["plugin"] = plugin_id
                    if measure and entry["injects_context"] and hook.get("command"):
                        measured = measure_hook_output(hook["command"], project or Path.cwd(), event)
                        entry["output_chars"] = measured["output_chars"]
                        entry["prompt_cost"] = measured["prompt_cost"]
                    entries.append(entry)
    return entries


def measure_hook_output(command: str, project: Path, event: str) -> dict:
    """Run a context-injecting hook and measure the text it hands to the model.

    Opt-in only: hooks are third-party programs and may start background
    services. Nothing is written back; the output is measured and discarded.
    """
    payload = json.dumps({
        "cwd": str(project),
        "hook_event_name": event,
        "source": "startup",
        "session_id": "context-optimizer-measurement",
    })
    try:
        result = subprocess.run(
            ["bash", "-c", command],
            input=payload, capture_output=True, text=True, timeout=60,
            cwd=str(project), check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "output_chars": {"value": None, "basis": "unavailable", "note": str(exc)},
            "prompt_cost": {"value": None, "basis": "unavailable"},
        }

    injected = ""
    try:
        parsed = json.loads(result.stdout)
        injected = (parsed.get("hookSpecificOutput") or {}).get("additionalContext", "")
    except ValueError:
        injected = result.stdout

    return {
        "output_chars": {"value": len(injected), "basis": "measured",
                         "method": "hook executed, additionalContext measured"},
        "prompt_cost": estimate_tokens(len(injected)),
    }


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def build_inventory(config: Path, project: Path, *, measure_hooks: bool = False) -> dict:
    settings = read_json(config / "settings.json")
    overrides = settings.get("skillOverrides") or {}

    entries: list[dict] = []
    entries += collect_skills(config, project)
    entries += collect_markdown_entries(config / "commands", "command", "user-global")
    entries += collect_markdown_entries(project / ".claude" / "commands", "command", "project")
    entries += collect_markdown_entries(config / "agents", "agent", "user-global")
    entries += collect_markdown_entries(project / ".claude" / "agents", "agent", "project")
    entries += collect_mcp(config, project)
    entries += collect_claude_md(config, project)
    entries += collect_hooks(config, measure=measure_hooks, project=project)

    for entry in entries:
        name = entry.get("name", "")
        entry["disabled_by_override"] = overrides.get(name) == "off"
        entry["loads_at_startup"] = not (
            entry.get("user_invocable_only") or entry["disabled_by_override"]
        )

    estimated_prompt = sum(
        e["prompt_cost"]["value"] for e in entries
        if e.get("loads_at_startup") and isinstance(e.get("prompt_cost", {}).get("value"), int)
    )

    return {
        "paths": {
            "config_dir": str(config),
            "project_root": str(project),
            "detected": True,
        },
        "entries": entries,
        # Carried through so a later stage can tell whether a built-in skill
        # seen only in the pasted listing is already switched off.
        "skill_overrides": overrides,
        "totals": {
            "entry_count": len(entries),
            "startup_prompt_cost": {
                "value": estimated_prompt,
                "basis": "estimated",
                "method": f"sum of description chars/{CHARS_PER_TOKEN} for entries that load at startup",
            },
        },
        "not_enumerable_from_disk": [
            "built-in skills and built-in tools: they ship with the client and only appear "
            "in the pasted context breakdown",
            "MCP tool schema sizes: deferred at startup, only visible in the pasted breakdown",
        ],
    }


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

def self_test() -> int:
    """Build a throwaway installation on disk and inventory it.

    The two things worth pinning down are the ones a caller acts on: whether an
    entry loads at startup, and where its content really lives. A skill
    directory is usually a symlink into a repository, and the cost, the git
    state and every later edit belong to the target, not to the link.
    """
    import tempfile

    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = root / "config"
        project = root / "project"
        (config / "skills").mkdir(parents=True)
        project.mkdir()

        def write_skill(directory: Path, name: str, *, quiet: bool = False) -> None:
            directory.mkdir(parents=True, exist_ok=True)
            quiet_key = "disable-model-invocation: true\n" if quiet else ""
            (directory / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: Does {name} things.\n{quiet_key}---\n\nbody\n",
                encoding="utf-8")

        # The same synced skill filed under two buckets is one skill, not two.
        for bucket in ("bucket-a", "bucket-b"):
            write_skill(config / "skills" / "synced" / bucket / "shared", "shared")

        write_skill(config / "skills" / "normal", "normal")
        write_skill(config / "skills" / "quiet", "quiet", quiet=True)
        write_skill(config / "skills" / "overridden", "overridden")

        # A skill that lives in a repository and is linked into the config dir.
        external = root / "repo" / "skills" / "linked"
        write_skill(external, "linked")
        (config / "skills" / "linked").symlink_to(external, target_is_directory=True)

        (config / "settings.json").write_text(
            json.dumps({"skillOverrides": {"overridden": "off"}}), encoding="utf-8")

        (config / "CLAUDE.md").write_text("# Global\n\n@RTK.md\n", encoding="utf-8")
        (config / "RTK.md").write_text("# RTK\n\nimported content\n", encoding="utf-8")

        inventory = build_inventory(config, project)
        by_name = {e["name"]: e for e in inventory["entries"]}

        for name in ("normal", "quiet", "overridden", "linked"):
            if name not in by_name:
                failures.append(f"the fixture skill '{name}' was not found")
        if failures:
            for line in failures:
                print(f"FAIL {line}")
            return 1

        # disable-model-invocation keeps an entry off the startup prompt.
        if not by_name["normal"]["loads_at_startup"]:
            failures.append("an ordinary skill was reported as not loading at startup")
        if by_name["quiet"]["loads_at_startup"]:
            failures.append("disable-model-invocation did not take the entry off startup")
        if not by_name["quiet"]["user_invocable_only"]:
            failures.append("the disable-model-invocation frontmatter key was not parsed")

        # skillOverrides has the same effect, by a different route.
        if not by_name["overridden"]["disabled_by_override"]:
            failures.append("a skillOverrides entry was not detected")
        if by_name["overridden"]["loads_at_startup"]:
            failures.append("an overridden skill was still counted as loading")

        # One synced skill across two buckets counts once, and the duplicate is
        # recorded rather than dropped silently. Counting it twice doubled a
        # whole block's estimated saving on a live installation.
        shared = [e for e in inventory["entries"] if e["name"] == "shared"]
        if len(shared) != 1:
            failures.append(f"a synced skill in two buckets produced {len(shared)} entries")
        elif len(shared[0].get("duplicate_copies", [])) != 1:
            failures.append("the duplicate synced copy was not recorded")

        # A symlink is resolved to the file that actually holds the content.
        linked = by_name["linked"]
        if not linked["is_symlink"]:
            failures.append("a linked skill was not flagged as a symlink")
        if Path(linked["real_path"]) != (external / "SKILL.md").resolve():
            failures.append(f"the symlink was not resolved: {linked['real_path']}")
        if Path(linked["link_path"]) == Path(linked["real_path"]):
            failures.append("the link path and the real path were not kept apart")

        # An @import chain is followed, and each file is measured once.
        instructions = [e for e in inventory["entries"] if e["kind"] == "instructions"]
        names = {e["name"] for e in instructions}
        if names != {"CLAUDE.md", "RTK.md"}:
            failures.append(f"the import chain resolved to {sorted(names)}")
        else:
            imported = next(e for e in instructions if e["name"] == "RTK.md")
            if imported["imported_by"] != str((config / "CLAUDE.md").resolve()):
                failures.append("the imported file does not record what imported it")

        # The total counts only what actually loads.
        total = inventory["totals"]["startup_prompt_cost"]
        excluded = (by_name["quiet"]["prompt_cost"]["value"]
                    + by_name["overridden"]["prompt_cost"]["value"])
        recomputed = sum(
            e["prompt_cost"]["value"] for e in inventory["entries"]
            if isinstance(e.get("prompt_cost", {}).get("value"), int)
        )
        if total["value"] != recomputed - excluded:
            failures.append("the startup total did not exclude the entries that do not load")
        if total["basis"] != "estimated":
            failures.append("a byte-derived total was not labelled estimated")

    for line in failures:
        print(f"FAIL {line}")
    if failures:
        return 1
    print("self-test: all checks passed")
    print("  synced skill in two buckets   -> counted once, duplicate recorded")
    print("  disable-model-invocation      -> entry off the startup prompt")
    print("  skillOverrides off            -> entry off the startup prompt")
    print("  symlinked skill               -> resolved to its real file, link path kept")
    print("  CLAUDE.md @import chain       -> followed, importer recorded")
    print("  startup total                 -> excludes what does not load, labelled estimated")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="configuration directory (detected when omitted)")
    parser.add_argument("--project", help="project root (detected when omitted)")
    parser.add_argument("--measure-hooks", action="store_true",
                        help="execute context-injecting hooks to measure their real output")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    parser.add_argument("--self-test", action="store_true", help="run the built-in fixtures")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    config = detect_config_dir(args.config)
    if config is None:
        print("No Claude Code configuration directory found.", file=sys.stderr)
        return 1
    project = detect_project_root(args.project)

    inventory = build_inventory(config, project, measure_hooks=args.measure_hooks)

    if args.json:
        print(json.dumps(inventory, indent=2))
        return 0

    print(f"config: {inventory['paths']['config_dir']}")
    print(f"project: {inventory['paths']['project_root']}")
    print(f"{'kind':<13}{'source':<20}{'name':<34}{'cost':>8}  basis")
    for entry in inventory["entries"]:
        cost = entry.get("prompt_cost", {})
        value = cost.get("value")
        shown = "-" if value is None else str(value)
        flag = "" if entry.get("loads_at_startup") else "  (not loaded)"
        print(f"{entry['kind']:<13}{entry['source']:<20}{entry['name'][:33]:<34}"
              f"{shown:>8}  {cost.get('basis')}{flag}")
    total = inventory["totals"]["startup_prompt_cost"]
    print(f"\ntotal loaded at startup: {total['value']} tokens ({total['basis']}, {total['method']})")
    for note in inventory["not_enumerable_from_disk"]:
        print(f"note: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
