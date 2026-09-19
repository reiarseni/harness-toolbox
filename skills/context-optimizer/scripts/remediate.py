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

# Mechanism -> how it behaves. "verified" means confirmed to work on this
# machine; anything else must pass a probe before it may be applied in bulk.
MECHANISMS = {
    "disable-model-invocation": {
        "applies_to": {"user-global", "project"},
        "keeps_manual_invocation": True,
        "verified": True,
        "note": "adds a frontmatter key to a file the user owns",
    },
    "skill-override-off": {
        "applies_to": {"claude-ai-synced", "plugin", "built-in"},
        "keeps_manual_invocation": False,
        "verified": False,
        "note": "sets skillOverrides in settings.json; the entry disappears entirely",
    },
    "unlink-agent": {
        "applies_to": {"user-global", "project"},
        "keeps_manual_invocation": False,
        "verified": True,
        "note": "removes the symlink; the file in the source repository is untouched",
    },
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
        self.dir = root / f"context-optimizer-backup-{stamp}"
        self.entries: list[dict] = []
        try:
            self.dir.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise Refusal(f"cannot create the backup directory ({exc}); nothing was modified")

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


def build_plan(inventory: dict, classification: list[dict]) -> dict:
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

        mechanism = choose_mechanism(entry)
        if mechanism is None:
            proposals.append({"name": item["name"],
                              "reason": f"no mechanism covers source '{entry['source']}'"})
            continue
        spec = MECHANISMS[mechanism]
        actions.append({
            "name": item["name"],
            "kind": entry["kind"],
            "source": entry["source"],
            "target": entry.get("real_path") or entry.get("link_path"),
            "mechanism": mechanism,
            "mechanism_verified": spec["verified"],
            "loses_manual_invocation": not spec["keeps_manual_invocation"],
            "warning": (None if spec["keeps_manual_invocation"] else
                        "All-or-nothing: once applied you cannot invoke this entry manually either."),
            "saving": entry.get("prompt_cost"),
            "evidence": item.get("evidence", []),
        })

    blocks: dict[str, list[dict]] = {}
    for action in actions:
        blocks.setdefault(action["source"], []).append(action)
    for entries in blocks.values():
        entries.sort(key=lambda a: -((a["saving"] or {}).get("value") or 0))

    return {
        "blocks": [
            {
                "block": source,
                "mechanism": entries[0]["mechanism"],
                "mechanism_verified": entries[0]["mechanism_verified"],
                "entry_count": len(entries),
                "estimated_saving": {
                    "value": sum((e["saving"] or {}).get("value") or 0 for e in entries),
                    "basis": "estimated", "method": "sum of description costs",
                },
                "entries": entries,
            }
            for source, entries in sorted(
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
               confirm: bool) -> dict:
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
        spec = MECHANISMS[mechanism]
        count = sum(1 for a in selected if a["mechanism"] == mechanism)
        if not spec["verified"] and mechanism not in verified and count > 1:
            raise Refusal(
                f"mechanism '{mechanism}' is not verified on this machine and {count} entries "
                f"would use it. Run a probe on a single entry, restart, confirm the saving, "
                f"then re-run with --verified {mechanism}."
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
            assert_not_plugin_cache(target)
            if action["mechanism"] == "disable-model-invocation":
                assert_repo_safe(target)
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

def survey_logs(config: Path, retention_days: int) -> dict:
    projects = config / "projects"
    cutoff = time.time() - retention_days * 86400
    old, total_bytes, old_bytes = [], 0, 0
    if projects.is_dir():
        # Recursive: subagent logs live one level deeper than session logs.
        for log in projects.rglob("*.jsonl"):
            try:
                stat = log.stat()
            except OSError:
                continue
            total_bytes += stat.st_size
            if stat.st_mtime < cutoff:
                old.append(log)
                old_bytes += stat.st_size
    return {
        "retention_days": retention_days,
        "cutoff": (datetime.now() - timedelta(days=retention_days)).date().isoformat(),
        "total_bytes": {"value": total_bytes, "basis": "measured", "method": "stat"},
        "reclaimable_bytes": {"value": old_bytes, "basis": "measured", "method": "stat"},
        "file_count": {"value": len(old), "basis": "measured", "method": "stat"},
        "consequence": "Sessions older than the retention window can no longer be resumed.",
        "files": [str(p) for p in old],
    }


def survey_retention_options(config: Path, windows: tuple[int, ...] = RETENTION_WINDOWS) -> dict:
    """Show what each retention window would free, so the choice is informed."""
    options = []
    for days in windows:
        survey = survey_logs(config, days)
        options.append({
            "retention_days": days,
            "cutoff": survey["cutoff"],
            "file_count": survey["file_count"],
            "reclaimable_bytes": survey["reclaimable_bytes"],
        })
    total = survey_logs(config, 0)["total_bytes"]
    return {
        "total_bytes": total,
        "options": options,
        "consequence": "Sessions older than the chosen window can no longer be resumed.",
        "note": "No window is applied by default; the caller must choose one explicitly.",
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
        try:
            apply_plan(plan, config, approved={"claude-ai-synced"}, verified=set(), confirm=True)
            failures.append("an unverified mechanism was applied in bulk")
        except Refusal as refusal:
            if "probe" not in str(refusal):
                failures.append("the bulk refusal did not point at the probe")

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

        # The all-or-nothing warning is attached where it applies.
        inventory = {"entries": [{"name": "s", "kind": "skill", "source": "claude-ai-synced",
                                  "real_path": str(skill_md),
                                  "prompt_cost": {"value": 100, "basis": "estimated"}}]}
        built = build_plan(inventory, [{"name": "s", "action": "suppress", "evidence": []}])
        action = built["blocks"][0]["entries"][0]
        if not action["loses_manual_invocation"] or not action["warning"]:
            failures.append("the all-or-nothing warning was missing")

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
    print("  unverified mechanism in bulk     -> refused, probe required")
    print("  unapproved block                 -> not applied")
    print("  log deletion without usage data  -> refused")
    print("  all-or-nothing warning           -> present")
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
    parser.add_argument("--retention-days", type=int, default=None,
                        help="required to delete; omit to see what each window would free")
    parser.add_argument("--usage-report")
    parser.add_argument("--confirm", action="store_true", help="actually write; default is dry run")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    try:
        if args.command == "plan":
            inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
            classification = json.loads(Path(args.classification).read_text(encoding="utf-8"))
            if isinstance(classification, dict):
                classification = classification.get("classification", [])
            print(json.dumps(build_plan(inventory, classification), indent=2))
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
                                verified=set(), confirm=args.confirm)
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
                                        verified=verified, confirm=args.confirm), indent=2))
        elif args.command == "logs":
            config = Path(args.config) if args.config else Path.home() / ".claude"
            if args.retention_days is None:
                if args.confirm:
                    raise Refusal(
                        "no retention window chosen. Review the options below and pass "
                        "--retention-days explicitly; there is no default."
                    )
                print(json.dumps(survey_retention_options(config), indent=2))
                return 0
            survey = survey_logs(config, args.retention_days)
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
