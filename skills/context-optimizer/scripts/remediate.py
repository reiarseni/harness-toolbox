#!/usr/bin/env python3
"""Apply approved context cuts safely, or refuse to.

Nothing here writes without an explicit --confirm. Before the first write of a
run it copies every file it is about to touch into a timestamped backup and
records, for each change, the exact command that undoes it.

Refusals are deliberate and final within a run:
  - protected entries are never edited, only reported;
  - nothing inside a plugin cache is ever written, because plugin updates
    overwrite it;
  - a versioned project file is left alone when the working tree is dirty;
  - a mechanism that has not been verified on this machine cannot be applied to
    more than one entry until a probe confirms it works;
  - logs are never deleted before usage has been measured.

Usage:
    python3 remediate.py plan   --inventory inv.json --classification cls.json
    python3 remediate.py probe  --entry NAME --mechanism M --inventory inv.json --confirm
    python3 remediate.py apply  --plan plan.json [--verified M,M] --confirm
    python3 remediate.py logs   --config DIR [--retention-days 30]
                                [--usage-report usage.json --confirm]
    python3 remediate.py --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

PROTECTED_KINDS = {"instructions", "hook", "mcp-server"}
PLUGIN_CACHE_MARKER = os.path.join("plugins", "cache")
# No default retention. Almost every installation has a different age profile:
# on the reference machine 30 days reclaimed 2.2 MB while 7 days reclaimed
# 0.93 GB. The user is shown what each window would free and picks one.
RETENTION_WINDOWS = (7, 14, 30, 90)

# Mechanism -> how it behaves. `verified_on` records the single client version
# the mechanism was actually measured against; anything else must pass a probe
# before it may be applied in bulk.
#
# A mechanism is verified against a client version, never in the abstract. What
# `skillOverrides` reaches was established on 2.1.278 and could change in any
# release; recording the version is what lets a later run notice that its
# evidence has expired and ask for a probe again, instead of applying a
# mechanism on the strength of somebody else's measurement.
MECHANISMS = {
    "disable-model-invocation": {
        "applies_to": {"user-global", "project"},
        "keeps_manual_invocation": True,
        "verified_on": "2.1.278",
        "note": "adds a frontmatter key to a file the user owns",
    },
    # Measured on Claude Code 2.1.278: skillOverrides does NOT reach plugin
    # skills, by either the bare or the qualified name. The client locks them
    # ("locked by plugin" in /skills) and routes them through /plugin. "plugin"
    # is therefore absent from applies_to, which makes every plugin skill
    # propose-only. See references/mechanisms.md.
    "skill-override-off": {
        "applies_to": {"claude-ai-synced", "built-in"},
        "keeps_manual_invocation": False,
        "verified_on": "2.1.278",
        "note": "sets skillOverrides in settings.json; the entry disappears entirely",
        # Writes to settings.json, never to the entry's own file — so the
        # plugin-cache guardrail must not be applied to its target.
        "writes_to_entry_file": False,
    },
    "unlink-agent": {
        "applies_to": {"user-global", "project"},
        "keeps_manual_invocation": False,
        "verified_on": "2.1.278",
        "note": "removes the symlink; the file in the source repository is untouched",
    },
}


def detect_client_version() -> str | None:
    """Ask the client what version it is, without assuming it is installed."""
    try:
        proc = subprocess.run(["claude", "--version"], capture_output=True,
                              text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"\d+\.\d+\.\d+", proc.stdout or "")
    return match.group(0) if match else None


def is_verified(mechanism: str, client_version: str | None) -> bool:
    """A mechanism counts as verified only on the version it was measured on.

    An unknown version is treated as unverified. That costs one probe and one
    restart; the alternative cost is a block applied on evidence gathered
    somewhere else, which is how a saving gets reported that never happened.
    """
    recorded = MECHANISMS[mechanism].get("verified_on")
    if not recorded or not client_version:
        return False
    return recorded == client_version


def verification_note(mechanism: str, client_version: str | None) -> str:
    recorded = MECHANISMS[mechanism].get("verified_on")
    if not recorded:
        return "never verified on any version; a probe is required"
    if not client_version:
        return (f"verified on Claude Code {recorded}, but this client's version could not be "
                f"read. Treated as unverified: probe one entry first.")
    if recorded == client_version:
        return f"verified on Claude Code {recorded}, which is the version running here"
    return (f"verified on Claude Code {recorded}, but this client is {client_version}. "
            f"The evidence has expired: probe one entry and confirm before applying the rest.")


# Why a source has no mechanism, in words the user can act on.
NO_MECHANISM_REASON = {
    "plugin": (
        "plugin skills cannot be disabled individually: the client locks them "
        "('locked by plugin' in /skills) and skillOverrides does not reach them, "
        "by either the bare or the qualified name. The only lever is the whole "
        "plugin, via /plugin or enabledPlugins — which also removes its hooks "
        "and MCP servers. Propose that trade explicitly; never apply it silently."
    ),
}


class Refusal(Exception):
    """Raised when a guardrail stops an operation. Never caught internally."""


# --------------------------------------------------------------------------
# Guardrails
# --------------------------------------------------------------------------

def assert_not_plugin_cache(path: Path) -> None:
    if PLUGIN_CACHE_MARKER in str(path):
        raise Refusal(f"refusing to write inside a plugin cache: {path}")


def assert_not_protected(entry: dict) -> None:
    if entry.get("kind") in PROTECTED_KINDS:
        raise Refusal(
            f"{entry.get('name')} is a {entry.get('kind')}: protected, propose only"
        )


def git_state(path: Path) -> dict:
    """Report whether `path` sits in a git repository and whether it is dirty.

    The path is resolved first: a skill directory is often a symlink pointing
    back inside the repository, and git refuses to look up a path that runs
    through a symlink. Editing the target still dirties the repository, so the
    resolved path is the one that must be checked.
    """
    path = path.resolve(strict=False)
    directory = path if path.is_dir() else path.parent
    def run(*args: str) -> tuple[int, str]:
        try:
            proc = subprocess.run(["git", *args], cwd=str(directory), capture_output=True,
                                  text=True, timeout=20, check=False)
            return proc.returncode, proc.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return 1, ""

    code, _ = run("rev-parse", "--is-inside-work-tree")
    if code != 0:
        return {"tracked": False, "in_repo": False, "dirty": False}
    _, porcelain = run("status", "--porcelain")
    code, _ = run("ls-files", "--error-unmatch", str(path))
    _, remote = run("remote")
    return {
        "in_repo": True,
        "tracked": code == 0,
        "dirty": bool(porcelain.strip()),
        "dirty_file_count": len([l for l in porcelain.splitlines() if l.strip()]),
        "has_remote": bool(remote.strip()),
    }


def assert_repo_safe(path: Path) -> None:
    """A tracked file in a dirty repository is reported, never edited.

    Aborting beats stashing: it removes the class of failure instead of
    managing it.
    """
    state = git_state(path)
    if state["in_repo"] and state["tracked"] and state["dirty"]:
        raise Refusal(
            f"{path} is tracked in a repository with {state['dirty_file_count']} "
            f"uncommitted change(s). Reporting the proposed edit instead; "
            f"no branch created, no stash taken."
        )


# --------------------------------------------------------------------------
# Backup and reversal manifest
# --------------------------------------------------------------------------

class Backup:
    def __init__(self, root: Path) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.entries: list[dict] = []
        # The stamp is per second, and approval is taken one block at a time,
        # so two applies land in the same second routinely. A bare mkdir then
        # refused the second block with "cannot create the backup directory" —
        # a refusal that says nothing was modified and is true, but stops a run
        # that had done nothing wrong. Never reuse a directory: each apply keeps
        # its own manifest, and merging them would make an undo ambiguous.
        base = root / f"context-optimizer-backup-{stamp}"
        candidate, suffix = base, 1
        while True:
            try:
                candidate.mkdir(parents=True, exist_ok=False)
                self.dir = candidate
                return
            except FileExistsError:
                candidate = base.with_name(f"{base.name}-{suffix}")
                suffix += 1
                if suffix > 100:
                    raise Refusal(
                        f"cannot find an unused backup directory beside {base}; "
                        f"nothing was modified"
                    )
            except OSError as exc:
                raise Refusal(
                    f"cannot create the backup directory ({exc}); nothing was modified")

    def save(self, path: Path) -> Path | None:
        if not path.exists() and not path.is_symlink():
            return None
        destination = self.dir / path.name
        counter = 1
        while destination.exists():
            destination = self.dir / f"{path.stem}.{counter}{path.suffix}"
            counter += 1
        try:
            if path.is_symlink():
                destination.symlink_to(os.readlink(path))
            else:
                shutil.copy2(path, destination)
        except OSError as exc:
            raise Refusal(f"cannot back up {path} ({exc}); nothing was modified")
        return destination

    def record(self, *, target: Path, mechanism: str, copy: Path | None, undo: str) -> None:
        self.entries.append({
            "target": str(target),
            "mechanism": mechanism,
            "backup_copy": str(copy) if copy else None,
            "undo_command": undo,
        })

    def write_manifest(self) -> Path:
        manifest = self.dir / "reversal-manifest.json"
        manifest.write_text(json.dumps({
            "created": datetime.now().isoformat(timespec="seconds"),
            "backup_dir": str(self.dir),
            "changes": self.entries,
            "how_to_revert": "Run every undo_command in order, then restart Claude Code.",
        }, indent=2), encoding="utf-8")
        return manifest


# --------------------------------------------------------------------------
# Mechanisms
# --------------------------------------------------------------------------

def apply_disable_model_invocation(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if re.search(r"^disable-model-invocation:", text, re.M):
        return "already set"
    match = re.match(r"\A(---\r?\n)(.*?)(\r?\n---)", text, re.S)
    if not match:
        raise Refusal(f"{path} has no frontmatter block to extend")
    updated = (match.group(1) + match.group(2) + "\ndisable-model-invocation: true"
               + match.group(3) + text[match.end():])
    path.write_text(updated, encoding="utf-8")
    return "added disable-model-invocation: true"


def apply_skill_override(settings_path: Path, name: str) -> str:
    data = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    overrides = data.setdefault("skillOverrides", {})
    if overrides.get(name) == "off":
        return "already off"
    overrides[name] = "off"
    settings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return f'set skillOverrides["{name}"] = "off"'


def apply_unlink(path: Path, archive_dir: Path) -> str:
    """Move an entry out of the active directory without ever overwriting."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    destination = archive_dir / path.name
    if destination.exists() or destination.is_symlink():
        raise Refusal(
            f"{destination} already exists; refusing to overwrite it. "
            f"Resolve the name collision first."
        )
    if path.is_symlink():
        path.unlink()
        return f"removed symlink {path} (source file untouched)"
    shutil.move(str(path), str(destination))
    return f"moved {path} to {destination}"


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------

def choose_mechanism(entry: dict) -> str | None:
    source = entry.get("source", "")
    if entry.get("kind") == "agent":
        return "unlink-agent"
    for name, spec in MECHANISMS.items():
        if name == "unlink-agent":
            continue
        if source in spec["applies_to"]:
            return name
    return None


def build_plan(inventory: dict, classification: list[dict],
               client_version: str | None = None) -> dict:
    by_name = {e["name"]: e for e in inventory["entries"]}
    actions, proposals = [], []

    for item in classification:
        if item["action"] not in ("suppress",):
            continue
        entry = by_name.get(item["name"])
        if entry is None:
            continue
        try:
            assert_not_protected(entry)
        except Refusal as refusal:
            proposals.append({"name": item["name"], "reason": str(refusal)})
            continue

        # The client's own listing outranks every inference here. When it says
        # an entry is locked, no mechanism reaches it, whatever its source
        # looks like on disk. Planning it anyway is how a block worth thousands
        # of tokens gets presented, approved, and found inapplicable two
        # restarts later.
        if entry.get("locked_by_client"):
            proposals.append({
                "name": item["name"],
                "reason": ("the client's skill listing reports this entry as locked, so no "
                           "mechanism here can switch it off individually. Its only lever is "
                           "whatever owns it — for a plugin skill, the whole plugin."),
            })
            continue

        mechanism = choose_mechanism(entry)
        if mechanism is None:
            proposals.append({"name": item["name"],
                              "reason": NO_MECHANISM_REASON.get(
                                  entry["source"],
                                  f"no mechanism covers source '{entry['source']}'")})
            continue
        spec = MECHANISMS[mechanism]
        actions.append({
            "name": item["name"],
            "kind": entry["kind"],
            "source": entry["source"],
            "target": entry.get("real_path") or entry.get("link_path"),
            "mechanism": mechanism,
            "mechanism_verified": is_verified(mechanism, client_version),
            "mechanism_verification": verification_note(mechanism, client_version),
            "loses_manual_invocation": not spec["keeps_manual_invocation"],
            "warning": (None if spec["keeps_manual_invocation"] else
                        "All-or-nothing: once applied you cannot invoke this entry manually either."),
            "saving": entry.get("prompt_cost"),
            "evidence": item.get("evidence", []),
        })

    # Group by source *and* mechanism. Grouping by source alone put a skill
    # cut with disable-model-invocation in the same block as an agent archived
    # with unlink-agent, and the block header — taken from the first entry —
    # then announced a reversible mechanism that keeps manual invocation for a
    # block containing one that does neither. Approval is taken per block, so
    # that header is the sentence the user says yes to. It has to be true of
    # every entry under it.
    blocks: dict[tuple[str, str], list[dict]] = {}
    for action in actions:
        blocks.setdefault((action["source"], action["mechanism"]), []).append(action)
    for entries in blocks.values():
        entries.sort(key=lambda a: -((a["saving"] or {}).get("value") or 0))

    return {
        "blocks": [
            {
                "block": f"{source}/{mechanism}",
                "source": source,
                "mechanism": mechanism,
                "mechanism_verified": entries[0]["mechanism_verified"],
                "mechanism_verification": entries[0]["mechanism_verification"],
                "loses_manual_invocation": entries[0]["loses_manual_invocation"],
                "warning": entries[0]["warning"],
                "entry_count": len(entries),
                "estimated_saving": {
                    "value": sum((e["saving"] or {}).get("value") or 0 for e in entries),
                    "basis": "estimated", "method": "sum of description costs",
                },
                "entries": entries,
            }
            for (source, mechanism), entries in sorted(
                blocks.items(),
                key=lambda kv: -sum((e["saving"] or {}).get("value") or 0 for e in kv[1]),
            )
        ],
        "propose_only": proposals,
    }


# --------------------------------------------------------------------------
# Applying
# --------------------------------------------------------------------------

def apply_plan(plan: dict, config: Path, *, approved: set[str], verified: set[str],
               confirm: bool, client_version: str | None = None) -> dict:
    selected = [
        action
        for block in plan["blocks"] if block["block"] in approved
        for action in block["entries"]
        if action["name"] not in plan.get("excluded", [])
    ]
    if not selected:
        return {"applied": [], "refused": [], "note": "nothing approved"}

    # A mechanism nobody has confirmed on this machine gets exactly one probe.
    for mechanism in {a["mechanism"] for a in selected}:
        count = sum(1 for a in selected if a["mechanism"] == mechanism)
        if not is_verified(mechanism, client_version) and mechanism not in verified and count > 1:
            raise Refusal(
                f"mechanism '{mechanism}' is not verified on this machine and {count} entries "
                f"would use it ({verification_note(mechanism, client_version)}). Run a probe "
                f"on a single entry, restart, confirm the saving, then re-run with "
                f"--verified {mechanism}."
            )

    if not confirm:
        return {"applied": [], "refused": [], "dry_run": True,
                "would_apply": [a["name"] for a in selected]}

    backup = Backup(config)
    applied, refused = [], []
    settings_path = config / "settings.json"

    for action in selected:
        target = Path(action["target"]) if action["target"] else settings_path
        try:
            # Guard the file this mechanism actually writes to, not the entry's
            # own file. Checking the target unconditionally used to refuse every
            # skill-override-off on a plugin skill — whose write goes to
            # settings.json — with "refusing to write inside a plugin cache",
            # killing a whole approved block over a file nobody was touching.
            #
            # Both guardrails belong here, for *every* mechanism that touches the
            # entry's own file. assert_repo_safe used to sit inside the
            # disable-model-invocation branch alone, which left unlink-agent
            # free to shutil.move a tracked agent out of a dirty repository —
            # exactly the loss the guardrail exists to prevent.
            if MECHANISMS[action["mechanism"]].get("writes_to_entry_file", True):
                assert_not_plugin_cache(target)
                assert_repo_safe(target)
            if action["mechanism"] == "disable-model-invocation":
                copy = backup.save(target)
                result = apply_disable_model_invocation(target)
                undo = (f"cp '{copy}' '{target}'" if copy else
                        f"remove the disable-model-invocation line from '{target}'")
            elif action["mechanism"] == "skill-override-off":
                copy = backup.save(settings_path)
                result = apply_skill_override(settings_path, action["name"])
                undo = f"cp '{copy}' '{settings_path}'"
            elif action["mechanism"] == "unlink-agent":
                copy = backup.save(target)
                result = apply_unlink(target, config / "agents.archive")
                undo = (f"cp -a '{copy}' '{target}'" if copy else f"restore '{target}' by hand")
            else:
                raise Refusal(f"unknown mechanism {action['mechanism']}")
            backup.record(target=target, mechanism=action["mechanism"], copy=copy, undo=undo)
            applied.append({"name": action["name"], "result": result})
        except Refusal as refusal:
            refused.append({"name": action["name"], "reason": str(refusal)})

    manifest = backup.write_manifest()
    return {"applied": applied, "refused": refused,
            "backup_dir": str(backup.dir), "manifest": str(manifest)}


# --------------------------------------------------------------------------
# Logs
# --------------------------------------------------------------------------

def classify_log(relative: Path) -> str:
    """Say what a log *is*, from its path alone.

    A date cutoff cannot tell a transcript nobody will ever reopen from a
    session worth resuming, and the two are not mixed evenly. Two shapes are
    machine-generated and recognisable without opening anything:

    - a subagent transcript, which sits in a `subagents/` directory under its
      parent session, or is named `agent-<hash>.jsonl`;
    - a tool's own scratch directory. The project directory name is the project
      path with its separators flattened, so a dot-directory shows up as a
      doubled dash: `-home-rei--claude-mem-observer-sessions` is
      `~/.claude-mem/observer-sessions`, which is not a project at all.

    Everything else is treated as a real session, because the cost of guessing
    wrong in that direction is losing the user's history.
    """
    parts = relative.parts
    if "subagents" in parts[1:] or relative.name.startswith("agent-"):
        return "subagent-transcript"
    if parts and "--" in parts[0]:
        return "tool-directory"
    return "session"


def scan_logs(config: Path) -> list[dict]:
    """Walk the log tree exactly once.

    Every survey below reads this list. Costing each retention window used to
    mean a fresh walk per window plus one for the breakdown — six passes over a
    tree that reached 10,546 files on a real installation.
    """
    projects = config / "projects"
    if not projects.is_dir():
        return []
    scanned = []
    for log in projects.rglob("*.jsonl"):
        try:
            stat = log.stat()
        except OSError:
            continue
        relative = log.relative_to(projects)
        scanned.append({
            "path": str(log),
            "mtime": stat.st_mtime,
            "bytes": stat.st_size,
            "project": relative.parts[0] if relative.parts else "",
            "category": classify_log(relative),
        })
    return scanned


def survey_logs(config: Path, retention_days: int, scan: list[dict] | None = None) -> dict:
    scan = scan_logs(config) if scan is None else scan
    cutoff = time.time() - retention_days * 86400
    old = [row for row in scan if row["mtime"] < cutoff]
    by_category: dict[str, dict] = {}
    for row in old:
        bucket = by_category.setdefault(row["category"], {"file_count": 0, "bytes": 0})
        bucket["file_count"] += 1
        bucket["bytes"] += row["bytes"]
    return {
        "retention_days": retention_days,
        "cutoff": (datetime.now() - timedelta(days=retention_days)).date().isoformat(),
        "total_bytes": {"value": sum(r["bytes"] for r in scan),
                        "basis": "measured", "method": "stat"},
        "reclaimable_bytes": {"value": sum(r["bytes"] for r in old),
                              "basis": "measured", "method": "stat"},
        "file_count": {"value": len(old), "basis": "measured", "method": "stat"},
        "by_category": by_category,
        "consequence": "Sessions older than the retention window can no longer be resumed.",
        "files": [r["path"] for r in old],
    }


def survey_machine_generated(scan: list[dict]) -> dict:
    """Cost the cleanup that destroys none of the user's history.

    The right recommendation is usually not a date window at all. On one
    installation the 7-day window covered 10,546 files, of which 10,257 — 97
    percent, 487 MB — were subagent transcripts written by a memory plugin,
    while the remaining 289 were the user's own sessions and included every
    session of two recent projects. Deleting only the machine-generated files
    freed most of the space and cost nothing.
    """
    targets = [r for r in scan if r["category"] != "session"]
    return {
        "file_count": {"value": len(targets), "basis": "measured", "method": "stat"},
        "reclaimable_bytes": {"value": sum(r["bytes"] for r in targets),
                              "basis": "measured", "method": "stat"},
        "categories": sorted({r["category"] for r in targets}),
        "share_of_all_logs_percent": (
            round(100 * len(targets) / len(scan), 1) if scan else 0.0
        ),
        "consequence": "No user session is touched; these transcripts cannot be resumed anyway.",
        "files": [r["path"] for r in targets],
    }


def survey_by_project(retention_days: int, scan: list[dict]) -> list[dict]:
    """Break a window down per project directory, saying what each one holds."""
    cutoff = time.time() - retention_days * 86400
    per: dict[str, dict] = {}
    for row in scan:
        if row["mtime"] >= cutoff:
            continue
        entry = per.setdefault(row["project"], {
            "project": row["project"], "file_count": 0, "bytes": 0,
            "session_files": 0, "machine_generated_files": 0,
        })
        entry["file_count"] += 1
        entry["bytes"] += row["bytes"]
        if row["category"] == "session":
            entry["session_files"] += 1
        else:
            entry["machine_generated_files"] += 1
    for entry in per.values():
        entry["mostly_machine_generated"] = (
            entry["machine_generated_files"] > entry["session_files"]
        )
    return sorted(per.values(), key=lambda r: -r["bytes"])


def survey_retention_options(config: Path, windows: tuple[int, ...] = RETENTION_WINDOWS,
                             scan: list[dict] | None = None) -> dict:
    """Show what each retention window would free, so the choice is informed."""
    scan = scan_logs(config) if scan is None else scan
    options = []
    for days in windows:
        survey = survey_logs(config, days, scan)
        options.append({
            "retention_days": days,
            "cutoff": survey["cutoff"],
            "file_count": survey["file_count"],
            "reclaimable_bytes": survey["reclaimable_bytes"],
            "by_category": survey["by_category"],
        })
    narrowest = min(windows) if windows else 0
    targeted = survey_machine_generated(scan)
    return {
        "total_bytes": {"value": sum(r["bytes"] for r in scan),
                        "basis": "measured", "method": "stat"},
        "options": options,
        "machine_generated_only": {k: v for k, v in targeted.items() if k != "files"},
        "by_project": {
            "retention_days": narrowest,
            "rows": survey_by_project(narrowest, scan),
            "why": "A date cutoff cannot tell a machine-generated transcript from "
                   "a session worth resuming. Show this breakdown before asking: "
                   "one directory often holds most of the space and none of the value.",
        },
        "widest_window_days": max(windows) if windows else 0,
        "consequence": "Sessions older than the chosen window can no longer be resumed.",
        "note": ("No window is applied by default; the caller must choose one explicitly. "
                 "Compare every window against machine_generated_only first: when that "
                 "recovers most of the space, recommend it instead of a window."),
        "no_backup": ("Log deletion is the one step with no entry in the reversal manifest. "
                      "Say so before it runs, not after."),
    }


def clean_logs(survey: dict, *, usage_report: Path | None, confirm: bool) -> dict:
    if usage_report is None or not usage_report.exists():
        raise Refusal(
            "usage must be measured before any log is deleted: these logs are the usage "
            "signal. Produce the usage report first, then pass --usage-report."
        )
    if not confirm:
        return {"deleted": 0, "dry_run": True,
                "would_delete": survey["file_count"]["value"]}
    deleted = 0
    for path in survey["files"]:
        try:
            Path(path).unlink()
            deleted += 1
        except OSError:
            continue
    return {"deleted": deleted, "freed_bytes": survey["reclaimable_bytes"]["value"]}


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

def self_test() -> int:  # noqa: C901 - a flat list of guardrail checks reads better
    import tempfile
    failures = []

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        # Backup, edit and reversal manifest.
        config = root / "config"
        config.mkdir()
        skill_dir = config / "skills" / "demo"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("---\nname: demo\ndescription: d\n---\n\nbody\n", encoding="utf-8")
        original = skill_md.read_text(encoding="utf-8")

        backup = Backup(config)
        copy = backup.save(skill_md)
        apply_disable_model_invocation(skill_md)
        backup.record(target=skill_md, mechanism="disable-model-invocation",
                      copy=copy, undo=f"cp '{copy}' '{skill_md}'")
        manifest_path = backup.write_manifest()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if "disable-model-invocation: true" not in skill_md.read_text(encoding="utf-8"):
            failures.append("the frontmatter key was not added")
        if not manifest["changes"][0]["undo_command"].startswith("cp "):
            failures.append("the manifest carries no undo command")
        shutil.copy2(copy, skill_md)
        if skill_md.read_text(encoding="utf-8") != original:
            failures.append("the undo command did not restore the original file")

        # Two applies in the same second each get their own backup directory.
        # Approval is taken one block at a time, so this is the normal case,
        # not an edge one; a bare mkdir refused the second block outright.
        same_second = [Backup(config) for _ in range(3)]
        if len({b.dir for b in same_second}) != 3:
            failures.append("two backups in the same second shared a directory")
        for b in same_second:
            if not b.dir.is_dir():
                failures.append(f"a backup directory was not created: {b.dir}")

        # A backup that cannot be created stops everything.
        locked = root / "locked"
        locked.mkdir(mode=0o500)
        try:
            Backup(locked)
            failures.append("a backup into an unwritable directory was allowed")
        except Refusal:
            pass
        finally:
            locked.chmod(0o700)

        # Plugin cache is never written.
        try:
            assert_not_plugin_cache(root / "plugins" / "cache" / "x" / "SKILL.md")
            failures.append("a write inside a plugin cache was allowed")
        except Refusal:
            pass

        # Protected kinds are proposal-only.
        for kind in ("instructions", "hook", "mcp-server"):
            try:
                assert_not_protected({"kind": kind, "name": kind})
                failures.append(f"a {kind} entry was not protected")
            except Refusal:
                pass

        # Archiving never overwrites.
        archive = root / "agents.archive"
        archive.mkdir()
        (archive / "dup.md").write_text("existing", encoding="utf-8")
        victim = root / "dup.md"
        victim.write_text("new", encoding="utf-8")
        try:
            apply_unlink(victim, archive)
            failures.append("archiving overwrote an existing entry")
        except Refusal:
            if (archive / "dup.md").read_text(encoding="utf-8") != "existing":
                failures.append("the existing archived entry was damaged")

        # A tracked agent in a dirty repository is reported, never moved.
        # unlink-agent used to skip this check entirely: the file was archived
        # out of the working tree and the user's uncommitted work went with it.
        repo = root / "repo"
        (repo / ".claude" / "agents").mkdir(parents=True)
        agent_md = repo / ".claude" / "agents" / "reviewer.md"
        agent_md.write_text("---\nname: reviewer\ndescription: d\n---\n", encoding="utf-8")
        def git(*args: str) -> int:
            return subprocess.run(
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                cwd=str(repo), capture_output=True, text=True, check=False,
            ).returncode
        if git("init", "-q") == 0:
            git("add", "-A")
            git("commit", "-qm", "seed")
            (repo / "uncommitted.txt").write_text("work in progress", encoding="utf-8")
            agent_plan = {"blocks": [{
                "block": "project", "mechanism": "unlink-agent",
                "mechanism_verified": True, "entry_count": 1,
                "estimated_saving": {"value": 50, "basis": "estimated"},
                "entries": [{"name": "reviewer", "mechanism": "unlink-agent",
                             "target": str(agent_md), "saving": {"value": 50}}],
            }]}
            agent_config = root / "config-agent"
            agent_config.mkdir()
            result = apply_plan(agent_plan, agent_config, approved={"project"},
                                verified=set(), confirm=True)
            if result["applied"]:
                failures.append("a tracked agent was archived out of a dirty repository")
            if not any("uncommitted change" in r["reason"] for r in result["refused"]):
                failures.append("the dirty-repository refusal did not reach unlink-agent")
            if not agent_md.exists():
                failures.append("the agent file was removed despite the refusal")
        else:
            print("note: git unavailable, the dirty-repository check was skipped")

        # Bulk use of an unverified mechanism is refused.
        plan = {"blocks": [{
            "block": "claude-ai-synced", "mechanism": "skill-override-off",
            "mechanism_verified": False, "entry_count": 2,
            "estimated_saving": {"value": 400, "basis": "estimated"},
            "entries": [
                {"name": "a", "mechanism": "skill-override-off", "target": None,
                 "saving": {"value": 200}},
                {"name": "b", "mechanism": "skill-override-off", "target": None,
                 "saving": {"value": 200}},
            ],
        }]}
        # A client version that does not match the one the mechanism was
        # measured on makes it unverified again. No flag is flipped here: the
        # mismatch is the whole point.
        recorded = MECHANISMS["skill-override-off"]["verified_on"]
        try:
            apply_plan(plan, config, approved={"claude-ai-synced"},
                       verified=set(), confirm=True, client_version="9.9.9")
            failures.append("a mechanism was applied in bulk on an unmeasured client version")
        except Refusal as refusal:
            if "probe" not in str(refusal):
                failures.append("the bulk refusal did not point at the probe")
            if "9.9.9" not in str(refusal):
                failures.append("the refusal did not name the version it was measured against")

        # An unknown version is treated as unverified, not as verified.
        if is_verified("skill-override-off", None):
            failures.append("an unreadable client version was treated as verified")
        if not is_verified("skill-override-off", recorded):
            failures.append("the version the mechanism was measured on was rejected")
        if recorded not in verification_note("skill-override-off", recorded):
            failures.append("the verification note did not name the version")

        # Nothing is applied without approval, and an unapproved block stays untouched.
        result = apply_plan(plan, config, approved=set(), verified=set(), confirm=True)
        if result["applied"]:
            failures.append("an unapproved block was applied")

        # Logs are never deleted before usage is measured.
        try:
            clean_logs({"files": [], "file_count": {"value": 0},
                        "reclaimable_bytes": {"value": 0}},
                       usage_report=None, confirm=True)
            failures.append("logs were deleted without a usage measurement")
        except Refusal as refusal:
            if "before any log is deleted" not in str(refusal):
                failures.append("the log refusal gave the wrong reason")

        # A log is classified by what it is, not by how old it is. Getting this
        # wrong is what made "7 days, 879 MB" look like a good offer when
        # 97 percent of it was machine-generated and the rest was real history.
        shapes = {
            "-home-proj/abc/session.jsonl": "session",
            "-home-proj/abc/subagents/agent-deadbeef.jsonl": "subagent-transcript",
            "-home-proj/agent-deadbeef.jsonl": "subagent-transcript",
            "-home-rei--claude-mem-observer-sessions/x.jsonl": "tool-directory",
            "-home-proj/xyz.jsonl": "session",
        }
        for relative, expected in shapes.items():
            got = classify_log(Path(relative))
            if got != expected:
                failures.append(f"classify_log('{relative}') = {got}, expected {expected}")

        # One walk feeds every survey, and the targeted cleanup spares sessions.
        logs_config = root / "logs-config"
        projects_dir = logs_config / "projects"
        (projects_dir / "-home-proj" / "abc" / "subagents").mkdir(parents=True)
        (projects_dir / "-home-rei--tool-scratch").mkdir(parents=True)
        (projects_dir / "-home-proj" / "abc" / "session.jsonl").write_text("s" * 100)
        (projects_dir / "-home-proj" / "abc" / "subagents" / "agent-1.jsonl").write_text("a" * 500)
        (projects_dir / "-home-rei--tool-scratch" / "t.jsonl").write_text("t" * 400)
        scan = scan_logs(logs_config)
        if len(scan) != 3:
            failures.append(f"the single scan found {len(scan)} logs, expected 3")
        targeted = survey_machine_generated(scan)
        if targeted["file_count"]["value"] != 2 or targeted["reclaimable_bytes"]["value"] != 900:
            failures.append("the machine-generated survey did not match the fixture")
        if any("session.jsonl" in f for f in targeted["files"]):
            failures.append("a real session was caught by the machine-generated cleanup")
        rows = {r["project"]: r for r in survey_by_project(0, scan)}
        if not rows["-home-rei--tool-scratch"]["mostly_machine_generated"]:
            failures.append("a tool directory was not flagged as machine-generated")
        if rows["-home-proj"]["session_files"] != 1:
            failures.append("the per-project breakdown miscounted real sessions")
        options = survey_retention_options(logs_config, windows=(7, 30), scan=scan)
        if options["machine_generated_only"]["reclaimable_bytes"]["value"] != 900:
            failures.append("the targeted option was missing from the retention survey")
        if "no entry in the reversal manifest" not in options["no_backup"]:
            failures.append("the retention survey did not say log deletion is unbacked")

        # The all-or-nothing warning is attached where it applies.
        inventory = {"entries": [{"name": "s", "kind": "skill", "source": "claude-ai-synced",
                                  "real_path": str(skill_md),
                                  "prompt_cost": {"value": 100, "basis": "estimated"}}]}
        built = build_plan(inventory, [{"name": "s", "action": "suppress", "evidence": []}])
        action = built["blocks"][0]["entries"][0]
        if not action["loses_manual_invocation"] or not action["warning"]:
            failures.append("the all-or-nothing warning was missing")

        # A plugin skill is propose-only: measured on Claude Code 2.1.278,
        # skillOverrides does not reach it by any name form. Planning one as an
        # action would resurrect a block that cost two restarts to disprove.
        plugin_inv = {"entries": [{
            "name": "p", "kind": "skill", "source": "plugin",
            "real_path": str(root / "plugins" / "cache" / "m" / "p" / "1" / "SKILL.md"),
            "prompt_cost": {"value": 100, "basis": "estimated"}}]}
        built = build_plan(plugin_inv, [{"name": "p", "action": "suppress", "evidence": []}])
        if built["blocks"]:
            failures.append("a plugin skill was planned as an action")
        elif "locked by plugin" not in str(built["propose_only"]):
            failures.append("the plugin proposal did not explain why it is locked")

        # A block is homogeneous in its mechanism, because its header is the
        # sentence approval is taken on. A user-global skill and a user-global
        # agent share a source but not a mechanism, and grouping them together
        # made the header promise "keeps manual invocation" over an entry that
        # would have been archived.
        mixed_inv = {"entries": [
            {"name": "sk", "kind": "skill", "source": "user-global",
             "real_path": str(skill_md), "prompt_cost": {"value": 200}},
            {"name": "ag", "kind": "agent", "source": "user-global",
             "real_path": str(root / "ag.md"), "prompt_cost": {"value": 30}},
        ]}
        built = build_plan(mixed_inv, [{"name": "sk", "action": "suppress", "evidence": []},
                                       {"name": "ag", "action": "suppress", "evidence": []}])
        for block in built["blocks"]:
            mechanisms = {e["mechanism"] for e in block["entries"]}
            if len(mechanisms) != 1:
                failures.append(f"a block mixed mechanisms: {mechanisms}")
            if block["mechanism"] not in mechanisms:
                failures.append("a block header named a mechanism none of its entries use")
            losses = {e["loses_manual_invocation"] for e in block["entries"]}
            if block["loses_manual_invocation"] not in losses or len(losses) != 1:
                failures.append("a block header misstated whether manual invocation is lost")
        if len(built["blocks"]) != 2:
            failures.append("a skill and an agent were not separated into their own blocks")

        # An entry the client reports as locked never becomes an action, even
        # when its source would otherwise have a working mechanism.
        locked_inv = {"entries": [{
            "name": "r", "kind": "skill", "source": "claude-ai-synced",
            "locked_by_client": True, "real_path": str(skill_md),
            "prompt_cost": {"value": 900, "basis": "estimated"}}]}
        built = build_plan(locked_inv, [{"name": "r", "action": "suppress", "evidence": []}])
        if built["blocks"]:
            failures.append("an entry the client reports as locked was planned as an action")
        elif "locked" not in str(built["propose_only"]):
            failures.append("the locked proposal did not say why it cannot be applied")

        # A built-in skill exists nowhere on disk, so it arrives with no path.
        # skillOverrides still reaches it, and the write lands in settings.json.
        builtin_inv = {"entries": [{
            "name": "pdf", "kind": "skill", "source": "built-in", "real_path": None,
            "link_path": None, "prompt_cost": {"value": 420, "basis": "measured"}}]}
        built = build_plan(builtin_inv, [{"name": "pdf", "action": "suppress", "evidence": []}])
        if not built["blocks"]:
            failures.append("a built-in skill produced no action, so its mechanism is unreachable")
        elif built["blocks"][0]["entries"][0]["mechanism"] != "skill-override-off":
            failures.append("a built-in skill was not routed to skillOverrides")

        # The plugin-cache guardrail must not fire for a mechanism that writes
        # to settings.json. It used to, and it killed an approved block.
        synced = {"entries": [{
            "name": "q", "kind": "skill", "source": "claude-ai-synced",
            "real_path": str(root / "plugins" / "cache" / "m" / "q" / "1" / "SKILL.md"),
            "prompt_cost": {"value": 100, "basis": "estimated"}}]}
        built = build_plan(synced, [{"name": "q", "action": "suppress", "evidence": []}])
        # Its own config dir: backup directories are named by the second, and an
        # earlier apply in this test already claimed one.
        fresh = root / "config2"
        fresh.mkdir()
        (fresh / "settings.json").write_text("{}", encoding="utf-8")
        result = apply_plan(built, fresh, approved={"claude-ai-synced"},
                            verified={"skill-override-off"}, confirm=True)
        if result["refused"]:
            failures.append("the plugin-cache guardrail fired on a settings.json write")

    for line in failures:
        print(f"FAIL {line}")
    if failures:
        return 1
    print("self-test: all checks passed")
    print("  backup + undo restored the original file")
    print("  unwritable backup directory      -> refused")
    print("  write inside a plugin cache      -> refused")
    print("  protected kinds                  -> refused (instructions, hook, mcp-server)")
    print("  archive over an existing entry   -> refused, existing entry intact")
    print("  agent tracked in a dirty repo    -> refused, file left in place")
    print("  mechanism on another version     -> unverified again, probe required")
    print("  unreadable client version        -> treated as unverified")
    print("  unapproved block                 -> not applied")
    print("  log deletion without usage data  -> refused")
    print("  log classification               -> session / subagent / tool directory")
    print("  targeted cleanup                 -> spares every real session, one walk")
    print("  all-or-nothing warning           -> present")
    print("  plugin skill                     -> propose-only, locked by plugin")
    print("  block grouping                   -> one mechanism per block, header true")
    print("  entry locked in /skills          -> propose-only, never planned")
    print("  built-in skill                   -> routed to skillOverrides")
    print("  settings.json write from a cache -> allowed, guardrail not misfired")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=["plan", "probe", "apply", "logs"])
    parser.add_argument("--inventory")
    parser.add_argument("--classification")
    parser.add_argument("--plan")
    parser.add_argument("--config")
    parser.add_argument("--entry")
    parser.add_argument("--mechanism")
    parser.add_argument("--approve", default="", help="comma-separated block names")
    parser.add_argument("--exclude", default="", help="comma-separated entry names to skip")
    parser.add_argument("--verified", default="", help="comma-separated verified mechanisms")
    parser.add_argument("--client-version",
                        help="override the detected Claude Code version; a mechanism counts "
                             "as verified only on the version it was measured against")
    parser.add_argument("--retention-days", type=int, default=None,
                        help="required to delete; omit to see what each window would free")
    parser.add_argument("--machine-generated-only", action="store_true",
                        help="delete only subagent transcripts and tool directories, "
                             "at any age; no user session is touched")
    parser.add_argument("--usage-report")
    parser.add_argument("--confirm", action="store_true", help="actually write; default is dry run")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    client_version = args.client_version or detect_client_version()

    try:
        if args.command == "plan":
            inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
            classification = json.loads(Path(args.classification).read_text(encoding="utf-8"))
            if isinstance(classification, dict):
                classification = classification.get("classification", [])
            plan = build_plan(inventory, classification, client_version)
            plan["client_version"] = {
                "value": client_version,
                "basis": "measured" if client_version else "unavailable",
                "method": "claude --version",
            }
            print(json.dumps(plan, indent=2))
        elif args.command == "probe":
            inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
            entry = next(e for e in inventory["entries"] if e["name"] == args.entry)
            plan = {"blocks": [{
                "block": entry["source"], "mechanism": args.mechanism,
                "mechanism_verified": False, "entry_count": 1,
                "estimated_saving": entry.get("prompt_cost", {}),
                "entries": [{"name": entry["name"], "mechanism": args.mechanism,
                             "target": entry.get("real_path"),
                             "saving": entry.get("prompt_cost")}],
            }]}
            config = Path(args.config or inventory["paths"]["config_dir"])
            result = apply_plan(plan, config, approved={entry["source"]},
                                verified=set(), confirm=args.confirm,
                                client_version=client_version)
            result["next_step"] = ("Restart Claude Code, run the context breakdown again and "
                                   "confirm the saving before applying the rest.")
            print(json.dumps(result, indent=2))
        elif args.command == "apply":
            plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
            plan["excluded"] = [n for n in args.exclude.split(",") if n]
            config = Path(args.config) if args.config else Path.home() / ".claude"
            approved = {b for b in args.approve.split(",") if b}
            verified = {m for m in args.verified.split(",") if m}
            print(json.dumps(apply_plan(plan, config, approved=approved,
                                        verified=verified, confirm=args.confirm,
                                        client_version=client_version), indent=2))
        elif args.command == "logs":
            config = Path(args.config) if args.config else Path.home() / ".claude"
            scan = scan_logs(config)
            if args.machine_generated_only:
                # No date cutoff: what this deletes is defined by what the file
                # is, not by how old it is, so no user session can be caught.
                survey = survey_machine_generated(scan)
                survey["reclaimable_bytes"] = survey["reclaimable_bytes"]
                report = {k: v for k, v in survey.items() if k != "files"}
                report["cleanup"] = clean_logs(
                    survey,
                    usage_report=Path(args.usage_report) if args.usage_report else None,
                    confirm=args.confirm,
                )
                print(json.dumps(report, indent=2))
                return 0
            if args.retention_days is None:
                if args.confirm:
                    raise Refusal(
                        "no retention window chosen. Review the options below and pass "
                        "--retention-days explicitly; there is no default."
                    )
                print(json.dumps(survey_retention_options(config, scan=scan), indent=2))
                return 0
            survey = survey_logs(config, args.retention_days, scan)
            report = {k: v for k, v in survey.items() if k != "files"}
            if args.usage_report or args.confirm:
                report["cleanup"] = clean_logs(
                    survey,
                    usage_report=Path(args.usage_report) if args.usage_report else None,
                    confirm=args.confirm,
                )
            print(json.dumps(report, indent=2))
        else:
            parser.print_help()
            return 1
    except Refusal as refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
