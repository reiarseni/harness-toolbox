# Case studies

Every figure in this file came off a specific machine on a specific day. None of
it is a constant. It lives here so the other references can state rules without
dragging someone else's numbers along, and so a reader who wants the evidence
behind a rule can find it.

**Read this only when you want to know how a rule was arrived at.** Never quote
a number from here to a user as if it applied to their installation. Measure.

Two installations are referred to throughout:

- **Installation A** — the machine the skill was first built against.
- **Installation B** — a second machine, audited later, which reproduced some
  findings and contradicted others.

---

## The floor

| | A | B |
|---|---:|---:|
| System prompt | 2.4k | — |
| Built-in tools | 12.6k | — |
| Fixed floor | ~15k | 16.6k |
| Startup total | 30.5k | 30.5k |

Both totals landed near 30.5k. That is a coincidence and it has already misled
one run: do not carry it over.

On A the maneuverable remainder was about 15.5k, and roughly 7k of it was
actually reachable. Stated against zero, 7k reads as failure; stated against the
remainder, it is most of what was ever on the table.

*Rule it supports:* `measurement.md` → "The floor".

---

## The estimator underestimates, unevenly

Installation A, one project instruction file:

| Chars | Estimated | Measured | Error |
|---:|---:|---:|---|
| 8,715 | 2,178 | 3,600 | 39% low |

Installation B, two categories:

| Category | Estimated | Measured | Error |
|---|---:|---:|---|
| Skill descriptions | 7,599 | 9,000 | 15.6% low |
| Memory files | 2,744 | 4,600 | 40.3% low |

The bias reproduced but was not uniform, which is why no single correction
factor is applied anywhere in this skill. Instruction files are the worst case
in both runs: they are written in the user's own language and dense with
commands and paths. Skill descriptions are mostly plain English prose and land
closer.

*Rule it supports:* `measurement.md` → "What estimation is for".

---

## Category totals move on their own

Installation B, three consecutive readings of one session:

| Reading | System tools | Deferred tools |
|---|---:|---:|
| 1 | 13.2k | 85 |
| 2 | 12.9k | — |
| 3 | 15.4k | 21 |

Nothing in the run caused this. Loaded tools, connected MCP servers and
deferred-schema state all shift underneath. A before/after built on the total
would have reported a saving that did not happen.

*Rule it supports:* `measurement.md` → "Compare categories, never totals".

---

## A plugin skill cannot be cut individually

Measured on Claude Code **2.1.278**, installation B:

- `skillOverrides` with the bare name (`mem-search`): no effect.
- `skillOverrides` with the qualified name (`claude-mem:mem-search`): no effect.
  Two restarts, two null results, the skill count unmoved.
- `/skills` shows every plugin skill as `🔒 on · locked by plugin`, with the
  footer *"Plugin skills are managed via /plugin"*.
- In the client binary, the function registering an override's alternative names
  returns early on `if (e.source === "plugin")`.

The trade on that machine: 14 unused plugin skills were worth 1,340 tokens, and
buying them would have cost the plugin's `SessionStart` hook and its MCP
server — the user's entire persistent memory.

Two probes were spent guessing name forms, one restart each, both null. The
`/skills` list held the answer the whole time, for free.

*Rules it supports:* `mechanisms.md` → "A plugin skill cannot be cut
individually", and the `/skills` request in Phase 1.

---

## A plugin is more than its skills

Installation A: the memory system was delivered by a `SessionStart` hook and an
MCP server, not by the plugin's skills. Cutting fifteen unused skills from that
plugin left the memory fully intact.

Installation B, priced by `portfolio.py` from the client's own `pluginUsage`
counter: one plugin, 981 tokens recoverable from skills nothing had invoked,
against seven hooks and one MCP server — and 100,696 recorded uses of the
plugin. Another plugin on the same machine sat at zero uses.

Those two numbers are not the same decision, which is why the trade is
calculated rather than narrated.

*Rule it supports:* `mechanisms.md` → "A plugin is more than its skills".

---

## The probe pays for itself

Installation A: the unverified category accounted for roughly 5.7k of the ~7k of
available savings. Applying it blindly would have written useless keys into
`settings.json` and reported a saving that never happened.

*Rule it supports:* `mechanisms.md` → "The probe".

---

## Counting identified the block that failed

One run on installation B applied three blocks at once:

| | Expected | Measured |
|---|---:|---:|
| Entries removed | −24 | −17 |
| Tokens | — | −1,360 short |

9 + 8 = 17 identified the two blocks that worked and the one that did nothing,
with no further measurement. The token deficit against a block worth 1,340 then
confirmed it.

*Rule it supports:* `mechanisms.md` → "Counting is a sharper signal than
tokens".

---

## The usage counter beat the log scrape

Installation B. A run scraped session logs and reported `invocations=0
(measured)` for thirteen entries that `~/.claude.json`'s `skillUsage` map showed
had been used — `karpathy-code-workflow` 36 times, `opsx:apply` 14 times and
five days earlier. Eight were suppressed before the counter was found.

Nothing was lost only because seven of them had been cut with
`disable-model-invocation`, which keeps the entry invocable. Had the mechanism
been `skillOverrides`, the user would have lost working commands on the strength
of a measurement that was wrong.

Three further facts from the same file, each of which the code now depends on:

- 66 counter keys, and **not one** with `usageCount: 0`. The client writes a key
  on first use and never writes a zero, so an absent key is the "never invoked"
  signal and a present zero would be an anomaly.
- `numStartups` read 664, which is the horizon that turns the counter's silence
  into an argument.
- Both `karpaty-code-workflow` (6) and `karpathy-code-workflow` (36) were
  present: a rename leaves the old key behind holding all the history.

*Rules it supports:* `portfolio.md` → "Read the client's counter first", and the
near-miss warning in `portfolio.py`.

---

## Namespaces must not be crossed

Installation B. Matching a counter key by its unqualified tail made the bare
name `do` collect `claude-mem:do`'s 99 invocations, and `apply` collect
`opsx:apply`'s 14. Either would have saved an unused skill from the cut on
another entry's evidence.

*Rule it supports:* the exact-then-whole-key matching in `lookup_counter`.

---

## The audit contaminates its own corpus

Installation B. A synced skill went from `invocations=0` to `invocations=1`
between two passes, purely because the intervening conversation had discussed
it — the sentence proposing to cut it was itself the match.

*Rule it supports:* `portfolio.md` → "The audit contaminates its own corpus".

---

## A guardrail guarded the wrong file

Installation B. `assert_not_plugin_cache` ran against the entry's own
`SKILL.md`, so every `skill-override-off` on a plugin skill was refused with
"refusing to write inside a plugin cache" — for a write that was always going to
`settings.json`. Fourteen approved entries, refused over a file nobody was
touching.

*Rule it supports:* `safety.md` → "A guardrail guards the file that gets
written".

---

## A date cutoff cannot tell what a log is

Installation B, the 7-day window:

| | Files | Size |
|---|---:|---:|
| Subagent transcripts from a memory plugin | 10,257 (97%) | 487 MB |
| The user's own sessions | 289 | 392 MB |

Offering "7 days, 879 MB" would have bought 475 MB of genuine garbage at the
price of 392 MB of real history, including every session of two recent projects.

A later scan of the same machine, with the classifier now in
`remediate.py`:

| | Files | Size |
|---|---:|---:|
| All logs | 3,767 | 770 MB |
| Machine-generated only, any age | 3,435 (91.2%) | 266 MB |
| 7-day window | 484 | 415 MB, of which 219 files were real sessions |

The right recommendation was not one of the windows.

Two machine-generated shapes are recognisable from the path alone:

- a subagent transcript, in a `subagents/` directory or named `agent-<hash>.jsonl`;
- a tool's own scratch directory, which shows as a doubled dash in the project
  directory name because the encoding flattens a dot-directory —
  `-home-rei--claude-mem-observer-sessions` is `~/.claude-mem/observer-sessions`.

*Rule it supports:* `safety.md` → "Archiving" and the log phase.

---

## An agent is not invoked the way a skill is

Installation B, found by running the skill against the live machine. Every
agent was reported `invocations=0` and proposed for deletion. The logs said
otherwise:

| Agent | Real invocations | Verdict given |
|---|---:|---|
| security-auditor | 5 | suppress |
| code-reviewer | 5 | suppress |
| architect-reviewer | 2 | suppress |
| refactoring-specialist | 1 | suppress |
| debugger | 0 | suppress (correct) |

Two independent causes, both of which had to be fixed:

1. No template matched `"subagent_type": "<name>"`, which is how an agent is
   actually called. The templates only knew about `/name`, `Skill(name)` and
   `"skill": "name"`.
2. The log window was the forty most recently modified files. After the walk
   became recursive, those forty were 38 files from a memory plugin's scratch
   directory and 2 real sessions — so the scrape read 8 MB of noise and never
   saw a session log at all.

This mattered more than the earlier counter incident: an agent has no
`skillUsage` entry to fall back on, and its mechanism, `unlink-agent`, does not
keep manual invocation. The entries would have been archived, not merely
quietened.

## Matching over a real log tree has to be windowed

Installation B, 328 session logs, 506 MB. The invocation templates carry
lookarounds, which defeat the literal-prefix optimisation in Python's `re`, so
an alternation scans every byte at about 20 MB/s.

| Approach | CPU |
|---|---:|
| One combined regex over every byte | 2 min 45 s |
| Prefilter on the bare name | no help — see below |
| Regex only around each literal occurrence | 9 s, whole run |

The bare-name prefilter fails because the system prompt lists every registered
entry, so every name appears in every log. What does work is that it appears a
handful of times: `str.find` locates those positions at C speed and the
expensive pattern then runs on 160 characters instead of 14 MB.

## The same synced skill under two buckets

Installation B. `skills/synced/` held two bucket directories, one per account,
each containing the same 12 skills byte for byte. The walk counted 24. Any
saving estimated for that block would have been double the truth.

## A block that mixed two mechanisms

Installation B. Blocks were grouped by source, so a user-global skill cut with
`disable-model-invocation` shared a block with a user-global agent archived by
`unlink-agent`. The block header, taken from the first entry, announced a
reversible mechanism that keeps manual invocation — over an entry that does
neither. Approval is taken per block, so that header is the sentence the user
says yes to.

## Two applies in the same second

Installation B. The backup directory is named to the second, and the skill
prescribes taking approval one block at a time. The second block's apply hit
`FileExistsError` and was refused with "cannot create the backup directory",
stopping a run that had done nothing wrong.

## An unlinked agent could be moved out of a dirty repository

Installation B, found by review rather than by loss. `apply_unlink` never called
`assert_repo_safe`; only the frontmatter mechanism did. A project agent that was
a real file rather than a symlink would have been `shutil.move`d out of a
repository with uncommitted work, in direct contradiction of a guardrail the
skill states in its own text.

*Rule it supports:* `safety.md` → the dirty-repository refusal, now enforced for
every mechanism that touches an entry's own file.
