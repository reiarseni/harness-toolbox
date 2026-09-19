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
            for skill_md in sorted(synced.glob("*/*/SKILL.md")):
                entries.append(skill_entry(skill_md, "claude-ai-synced"))

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="configuration directory (detected when omitted)")
    parser.add_argument("--project", help="project root (detected when omitted)")
    parser.add_argument("--measure-hooks", action="store_true",
                        help="execute context-injecting hooks to measure their real output")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = parser.parse_args(argv)

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
