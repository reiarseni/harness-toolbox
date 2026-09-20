# Safety

This skill edits a working configuration. Everything below is a refusal
condition, not a preference. A refusal stands for the whole run.

## Backup before the first write

Before touching anything, copy every file that will be modified into a
timestamped directory and write a reversal manifest next to the copies. The
manifest holds, for each change, the target, the mechanism, the backup copy and
**the exact command that undoes it**.

If the backup cannot be created — permissions, disk, anything — nothing is
modified. Not "modify and warn". Nothing.

A manifest that says "restore from backup" is not a manifest. It must contain
the command, because the person reading it will be someone who has already lost
something once.

*Covers: "a normal application", "the backup fails", "the user wants to revert".*

## Protected: propose, never edit

Three things are measured and reported, and never written:

- **CLAUDE.md content.** It is often the largest controllable entry, and it
  carries the user's own operating rules. Propose which sections to move and
  what that would save. Do not rewrite it.
- **Hooks in settings.json.** A broken hook breaks every Bash command or every
  file write. Measure what an injecting hook actually emits — by running it and
  discarding the output — and report that. Do not edit the configuration.
- **MCP servers.** Their schemas are deferred, so they cost almost nothing at
  startup. The saving does not justify breaking a working integration.

Editing `settings.json` to add a `skillOverrides` key is allowed. Editing the
`hooks` key of the same file is not. The protection is per key, not per file.

*Covers: "an instruction file is the largest consumer", "a hook injects text into every session".*

## Never inside a plugin cache

Any path containing `plugins/cache` is refused. Plugin updates overwrite that
directory, so an edit there is silently lost. Use `skillOverrides` instead.

*Covers: "an entry provided by a plugin".*

## Versioned project files

Check the working tree before touching a tracked file. Resolve symlinks first:
a project skill directory is often a symlink pointing back inside the
repository, and git refuses to look up a path that runs through one. Editing
the target still dirties the repository.

- **Clean tree** — create a branch, apply there, record it in the manifest.
- **Dirty tree** — abort. Do not stash. Do not create a branch. Report the
  proposed edit and let the user apply it through their own workflow.

Aborting is deliberate. Stashing manages the failure mode that costs people a
day of work; aborting removes it. The cost is that a project cut cannot be
applied while work is in progress, which is most of the time. Accept that.

*Covers: "a dirty working tree", "a clean working tree", "an unversioned project file".*

## Approval

Present the whole plan ranked by saving, then take approval one block at a
time, allowing individual entries to be excluded from a block.

Silence is not approval. An ambiguous answer is not approval. Neither is the
user moving on to another subject. Apply what was approved and nothing else.

Warn **before** asking, not after, when a mechanism also removes the ability to
invoke an entry by name.

*Covers: "partial approval of a block", "an ambiguous answer", "an all-or-nothing mechanism".*

## A guardrail guards the file that gets written

A refusal is only as good as its target. Check the path the mechanism **writes
to**, never the path the entry lives at. They are the same for a frontmatter
edit and different for everything else — a whole approved block was once refused
over a file nobody was touching (`case-studies.md`).

The corollary bites in the other direction too, and it is the more dangerous
one. Every guardrail that protects an entry's own file must run for **every**
mechanism that touches an entry's own file, not just the one it was written
alongside. `assert_repo_safe` lived inside the frontmatter branch alone, which
left the archive mechanism free to move a tracked agent out of a dirty
repository — the exact loss the guardrail exists to prevent.

When you add a mechanism, ask which guardrails its write needs, not which ones
the mechanism next to it happens to call.

**When a refusal looks wrong, it is still a stop.** Do not route around it. Say
which guardrail fired, which file it was protecting, which file would actually
have been written, and let the user decide. A false positive that a user
overrides knowingly is an incident they chose; one the model works around
quietly is an incident they discover later. If they do choose to proceed by
hand, reproduce the full ceremony — backup first, manifest with the exact undo
command — and record in the manifest that the script refused and why.

*Covers: "a guardrail fires on the wrong target", "the user overrides a refusal".*

## Archiving

Never overwrite an entry that already exists in the archive directory. A name
existing in both the active and the archive directory is common — it is what a
previous run leaves behind — and a move would destroy the archived copy. Refuse
and let the user resolve the collision.

When the entry is a symlink, remove the link. Never delete the file it points
at — that file belongs to another repository.

*Covers: "a name collision in the archive destination", "an archived entry behind a symlink".*

## Logs

Measure usage **before** deleting any log. Those logs are the usage signal;
deleting first destroys the evidence the analysis depends on. The cleanup
refuses to run without a usage report.

There is no default retention window. Show what each window would free and let
the user choose. Age profiles differ wildly between machines, so any default is
either useless or far more destructive than the user expected.

**A date cutoff cannot tell what a log is.** A machine-generated transcript
nobody will ever reopen and a session worth resuming are not mixed evenly, and
the window's single number hides that. `remediate.py logs` classifies every file
from its path — `session`, `subagent-transcript`, `tool-directory` — and reports
each window broken down by category alongside a `machine_generated_only` option
that has no date cutoff at all.

**That targeted option is usually the right recommendation**, and it is the one
the windows will not surface on their own: on one machine it recovered 91
percent of the log files while every window that freed comparable space also
deleted hundreds of real sessions (`case-studies.md`). Say so explicitly rather
than leaving the user to infer it from the table.

Run it with `--machine-generated-only`. Afterwards, confirm that the plugin's
own store — the distilled data, not the transcripts — is untouched.

Log deletion is the one step in this skill with **no entry in the reversal
manifest**: no backup of a log is taken. State that before it runs, not after.

Always state that deleted sessions can no longer be resumed.

*Covers: "the order of operations", "approval of the cleanup", "choosing the retention window".*
