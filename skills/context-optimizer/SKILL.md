---
name: context-optimizer
description: Audits and optimizes Claude Code startup context — measures what every skill, agent, MCP server, CLAUDE.md and hook actually costs, finds overlapping and unused entries, checks coverage against the project stack, then applies the cuts you approve with a backup and a reversal manifest. Use when startup context is too large or the skill portfolio needs a review.
license: MIT
metadata:
  author: reiarseni
  version: "1.0"
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
prompt and the built-in tools are a fixed floor — roughly 15k of a 30.5k
startup on the machine this was built against.

Tell the user the floor before showing any plan. A saving of 7k looks like
failure next to zero and like near-total success next to the floor. The second
framing is the true one.

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

---

## Phase 1 — Ask for measured data

Two numbers cannot be read from disk: the context breakdown and the usage
attribution. Both come from slash commands the user runs.

Ask for them once, plainly:

> To work from measured numbers rather than estimates, paste the output of the
> context breakdown and the usage report. Without them I can still run, but
> every figure will be an estimate and estimates come in low.

If the user declines, continue. Do not ask twice, and do not block.

Save whatever they paste to a file and parse it:

```bash
python3 scripts/measured.py context pasted-context.txt > context.json
python3 scripts/measured.py usage pasted-usage.txt > usage.json
```

Read `references/measurement.md` before labelling anything.

## Phase 2 — Inventory

```bash
python3 scripts/inventory.py --project "$PWD" --json > inventory.json
```

Add `--measure-hooks` only after telling the user what it does: it executes the
hooks that inject text, to measure what they actually emit. Hooks are
third-party programs and may start background services. The output is measured
and discarded.

Then reconcile:

```bash
python3 scripts/measured.py reconcile --inventory inventory.json --context context.json
```

Report the deviation between estimate and measurement when both exist. The
measurement is always the authoritative one.

## Phase 3 — Portfolio

```bash
python3 scripts/portfolio.py --inventory inventory.json --usage usage.json --json > portfolio.json
```

Read `references/portfolio.md` and present three findings:

1. **Unused** — with the evidence window that supports the claim.
2. **Overlapping** — naming both sides and their levels.
3. **Coverage** — the detected stack, what covers it, what nothing covers, and
   what matches nothing in it.

When signals conflict — no invocations but sole coverage of a stack tag — say
so and keep the entry. Never resolve a conflict silently.

## Phase 4 — Plan and approval

```bash
python3 scripts/remediate.py plan --inventory inventory.json --classification portfolio.json > plan.json
```

Present the whole plan ranked by saving, grouped into blocks by origin. For
each block state the mechanism, whether it is verified on this machine, the
estimated saving, and — when it applies — that the mechanism also removes the
ability to invoke those entries by name.

Take approval **one block at a time** with AskUserQuestion, and offer to
exclude individual entries within a block.

Silence is not approval. An ambiguous answer is not approval. Neither is the
user changing the subject.

List separately everything that is propose-only: protected entries, and any
entry whose origin has no working mechanism.

## Phase 5 — Apply

Read `references/safety.md` first. Every rule there is a refusal, not a
preference.

For a mechanism not yet verified on this machine, run the probe on a single
entry:

```bash
python3 scripts/remediate.py probe --entry NAME --mechanism M \
    --inventory inventory.json --confirm
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

That prints what each retention window would free. There is no default. Show
the table, let the user pick, then:

```bash
python3 scripts/remediate.py logs --config ~/.claude --retention-days N \
    --usage-report usage.json --confirm
```

Always state that deleted sessions can no longer be resumed.

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
- **A tracked file in a dirty repository is reported, never edited.** Do not
  stash. Do not create a branch. Resolve symlinks before deciding, because a
  skill directory often links back into the repository.
- **Never overwrite an archived entry.** Refuse on a name collision.
- **Never delete the target of a symlink.** Remove the link only.
- **An unverified mechanism touches one entry, then stops** until the user
  confirms a measured saving.
- **Measure usage before deleting any log**, and never apply a retention window
  the user did not choose.
- **Label every number** measured or estimated, and say that estimates are a
  lower bound.
- **Evidence or nothing.** Every recommendation names the signal behind it.
  When the signals conflict, say there is no clear answer instead of inventing
  a preference.
