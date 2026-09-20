---
name: context-optimizer
description: Audits and optimizes Claude Code startup context — measures what every skill, agent, MCP server, CLAUDE.md and hook actually costs, finds overlapping and unused entries, checks coverage against the project stack, then applies the cuts you approve with a backup and a reversal manifest. Use when startup context is too large or the skill portfolio needs a review.
license: MIT
metadata:
  author: reiarseni
  version: "1.1"
disable-model-invocation: true
allowed-tools: Read Write Edit Bash Glob Grep AskUserQuestion
---

# Context Optimizer

Audit what a Claude Code installation loads before the user has asked for
anything, work out which of it earns its place, and cut the rest safely.

Two things separate this from counting bytes in a skills directory:

- It covers **every** source — built-in, synced, plugin, user, project, agents,
  commands, MCP servers, instruction files and hooks — not just one directory.
- It judges the **portfolio**, not only the cost: what overlaps, what is never
  invoked, and what the project's stack needs that nothing covers.

## Say this first

A large part of startup context cannot be cut by anything here. The system
prompt and the built-in tools are a fixed floor, and on the installations
recorded in `references/case-studies.md` it was roughly half the startup total.
Measure it; never carry a figure over from another machine.

Tell the user the floor before showing any plan. A saving looks like failure
next to zero and like near-total success next to the floor. The second framing
is the true one.

State the **maneuverable remainder** as its own figure, in its own sentence —
"<remainder> is in play" — and report every later saving against that, not
against the total.

## Scripts

Deterministic work belongs in code, not in judgement. All four use only the
Python standard library and detect paths rather than assuming them.

| Script | Purpose |
|---|---|
| `scripts/inventory.py` | Walk every source; emit entries with origin, real path and cost |
| `scripts/measured.py` | Parse pasted context and usage output; reconcile against estimates |
| `scripts/portfolio.py` | Usage, overlap, stack coverage, and a classified plan |
| `scripts/remediate.py` | Backup, reversal manifest, mechanisms, guardrails, logs |

Each takes `--self-test`, which runs recorded fixtures. Run them when changing
a script; they cover every refusal path.

## References

Read a reference when the procedure reaches it, not before.

| File | Read it when |
|---|---|
| `references/measurement.md` | Deciding what counts as measured, and reporting the floor |
| `references/portfolio.md` | Judging usage, overlap and stack coverage |
| `references/mechanisms.md` | Choosing how to cut a specific entry |
| `references/safety.md` | Before the first write of a run |
| `references/case-studies.md` | Only to see the evidence behind a rule — never to quote a figure |

---

## Phase 1 — Ask for measured data

Three things cannot be read from disk, and all three come from slash commands
the user runs. Ask for all of them **once, together**, before anything else:

> To work from measured numbers rather than estimates, paste the output of
> `/context`, `/usage` and `/skills`. Without them I can still run, but every
> figure will be an estimate and estimates come in low.

Each earns its place:

- **`/context`** — the token cost per category, and the floor.
- **`/usage`** — attribution per skill, agent, plugin and MCP server.
- **`/skills`** — the per-entry source, cost and lock status. This is the one
  that is easiest to skip and most expensive to skip. It is the only sighting of
  **built-in skills**, which exist nowhere on disk and are the category
  `skillOverrides` is most used on. It is also the only authority on what is
  **locked**: a locked entry cannot be cut by any mechanism here, and presenting
  a block of them, taking the user's approval, and discovering it two restarts
  later is the worst failure this skill has had.

Also ask for the **entry counts**, not just the tokens — "68 skills · 9k
tokens". The count is the harder number and it is what will later identify which
block of an applied plan actually worked.

If the user declines, continue. Do not ask twice, and do not block.

Save whatever they paste and parse it:

```bash
python3 scripts/measured.py context pasted-context.txt > context.json
python3 scripts/measured.py usage   pasted-usage.txt   > usage.json
python3 scripts/measured.py skills  pasted-skills.txt  > skills.json
```

If everything landed in one file, pass that file to all three: each parser
ignores what is not its own.

Read `references/measurement.md` before labelling anything.

## Phase 2 — Inventory

```bash
python3 scripts/inventory.py --project "$PWD" --json > inventory.json
```

Add `--measure-hooks` only after telling the user what it does: it executes the
hooks that inject text, to measure what they actually emit. Hooks are
third-party programs and may start background services. The output is measured
and discarded.

Then fold in the skill listing, which is what puts built-in skills into the
inventory and marks what the client reports as locked:

```bash
python3 scripts/measured.py merge --inventory inventory.json --skills skills.json > merged.json
```

Use `merged.json` from here on. Then reconcile:

```bash
python3 scripts/measured.py reconcile --inventory merged.json --context context.json
```

Report the deviation between estimate and measurement when both exist. The
measurement is always the authoritative one.

Compare categories against themselves across measurements, **never totals**.
The breakdown's own lines move by thousands of tokens between readings of one
session, for reasons outside this skill's control.

## Phase 3 — Portfolio

```bash
python3 scripts/portfolio.py --inventory merged.json --usage usage.json \
    --session-id "$CLAUDE_SESSION_ID" --json > portfolio.json
```

Pass the session id when you know it: it stops the audit from reading its own
transcript and counting the sentence that proposes a cut as evidence for keeping
the entry.

Read `references/portfolio.md` and present four findings:

1. **Unused** — with the evidence window that supports the claim. The client's
   own counter outranks any log scrape. An entry with no counter but a
   near-identical key may simply have been renamed; that is a conflict, not a
   verdict.
2. **Overlapping** — naming both sides and their levels.
3. **Coverage** — the detected stack, what covers it, what nothing covers, and
   what matches nothing in it.
4. **Plugins** — the all-or-nothing trade for each one, already priced: startup
   cost, recoverable share, hooks and MCP servers that go with it, and the
   client's recorded usage. State it; never apply it.

When signals conflict — no invocations but sole coverage of a stack tag — say
so and keep the entry. Never resolve a conflict silently.

## Phase 4 — Plan and approval

```bash
python3 scripts/remediate.py plan --inventory merged.json --classification portfolio.json > plan.json
```

The plan already drops every entry the client reports as locked, provided the
listing was merged in Phase 2. If it was not, say so before presenting anything:
the plan is then guessing at what is reachable.

Present the whole plan ranked by saving, grouped into blocks by origin. For each
block state the mechanism, the estimated saving, and — when it applies — that
the mechanism also removes the ability to invoke those entries by name.

State the verification honestly, in the block's own words: `plan.json` carries
`mechanism_verification` per block, which names the client version the mechanism
was measured on and the version running here. **A mechanism measured on another
version is not verified.** It needs a probe, exactly as an unmeasured one does.

Each block holds exactly one mechanism, so the block header is true of every
entry under it. Read it out as given — especially `loses_manual_invocation`,
which is the difference between quietening an entry and archiving it.

Take approval **one block at a time** with AskUserQuestion, and offer to
exclude individual entries within a block.

Silence is not approval. An ambiguous answer is not approval. Neither is the
user changing the subject.

List separately everything that is propose-only: protected entries, and any
entry whose origin has no working mechanism.

## Phase 5 — Apply

Read `references/safety.md` first. Every rule there is a refusal, not a
preference.

For a mechanism not verified on the client version running here, run the probe
on a single entry:

```bash
python3 scripts/remediate.py probe --entry NAME --mechanism M \
    --inventory merged.json --confirm
```

Then stop and ask the user to restart and re-run the context breakdown. Only
once they confirm a real saving may the rest of that category proceed:

```bash
python3 scripts/remediate.py apply --plan plan.json \
    --approve BLOCK --exclude NAME1,NAME2 --verified M --confirm
```

Without `--confirm` the command is a dry run. Use it to show exactly what would
happen before it happens.

If the probe shows no saving, restore that entry from the backup and mark the
whole category propose-only for this installation. Say that the expected saving
has dropped, and by how much.

## Phase 6 — Logs

Only after Phase 3. Those logs are the usage signal; deleting them first
destroys the evidence the analysis rests on. The script refuses to run without
a usage report.

```bash
python3 scripts/remediate.py logs --config ~/.claude
```

That prints what each retention window would free, broken down by what the files
actually are, plus a `machine_generated_only` figure that has no date cutoff.
There is no default.

**Read the breakdown before recommending a window.** A date cutoff cannot tell a
subagent transcript from a session worth resuming, and the targeted cleanup is
usually the better offer: it frees most of the space and touches no user
session.

```bash
# The targeted cleanup — subagent transcripts and tool directories, any age.
python3 scripts/remediate.py logs --config ~/.claude --machine-generated-only \
    --usage-report usage.json --confirm

# Or a window the user chose explicitly.
python3 scripts/remediate.py logs --config ~/.claude --retention-days N \
    --usage-report usage.json --confirm
```

Say **before** it runs that log deletion is the one step with no entry in the
reversal manifest, and that deleted sessions can no longer be resumed.

## Phase 7 — Report

Close with:

- **Before and after**, both labelled measured or estimated.
- **The floor**, restated, so the remaining number is read correctly.
- **What was applied**, and the path to the backup and the reversal manifest.
- **What was refused**, and why — refusals are results, not failures.
- **What is propose-only**, with the concrete change for the user to apply.
- **What remains unverified**, if a probe is still pending.

Ask the user to restart and re-run the context breakdown, and compare the real
figure against the predicted one. A prediction that missed is worth reporting;
it is how the estimator gets better.

---

## Guardrails

These are refusals. None of them is overridden by the user asking nicely, being
in a hurry, or saying it is fine.

- **Back up before the first write.** If the backup fails, nothing is modified.
- **The manifest carries the undo command** for every change, not a description
  of one.
- **Never edit** CLAUDE.md content, the `hooks` key of settings.json, or MCP
  server configuration. Measure them, propose changes, stop there. Adding a
  `skillOverrides` key to the same settings file is allowed; the protection is
  per key.
- **Never write inside a plugin cache.** Updates overwrite it.
- **A tracked file in a dirty repository is reported, never edited or moved.**
  Do not stash. Do not create a branch. This holds for every mechanism that
  touches an entry's own file, archiving included. Resolve symlinks before
  deciding, because a skill directory often links back into the repository.
- **Never overwrite an archived entry.** Refuse on a name collision.
- **Never delete the target of a symlink.** Remove the link only.
- **An unverified mechanism touches one entry, then stops** until the user
  confirms a measured saving. A mechanism measured on a different client
  version counts as unverified, and so does one whose version could not be read.
- **An entry the client reports as locked is never planned as an action**, and a
  plugin skill is never cut individually. The only lever is the whole plugin,
  and that is proposed, never applied.
- **Measure usage before deleting any log**, and never apply a retention window
  the user did not choose.
- **Label every number** measured or estimated, and say that estimates are a
  lower bound.
- **Evidence or nothing.** Every recommendation names the signal behind it.
  When the signals conflict, say there is no clear answer instead of inventing
  a preference.
