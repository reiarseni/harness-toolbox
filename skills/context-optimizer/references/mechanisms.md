# Mechanisms

How an entry is cut depends entirely on where it comes from. Using the wrong
mechanism either does nothing or is silently undone by the next update.

## The table

| Origin | Mechanism | Manual invocation | Reversible | Verified |
|---|---|---|---|---|
| Skill or command in a file the user owns | `disable-model-invocation: true` in the frontmatter | kept | yes, remove the line | on 2.1.278 |
| Project skill under version control | same, and only when the tree is clean | kept | yes | on 2.1.278 |
| Synced or built-in skill | `skillOverrides: {"<name>": "off"}` in `settings.json` | **lost** | yes, remove the key | on 2.1.278 |
| **Plugin skill** | **none that reaches one skill** — see below | — | — | **refuted** |
| Agent | remove the symlink or move the file to the archive directory | lost | yes, restore from backup | on 2.1.278 |
| Whole plugin | `enabledPlugins: {"<id>": false}` in `settings.json`, or `/plugin` | lost | yes, set back to true | on 2.1.278 |

**A mechanism is verified against a client version, never in the abstract.**
`remediate.py` records the version each one was measured on and compares it
against `claude --version` at plan time. On a different version — or when the
version cannot be read — the mechanism goes back to unverified and needs a
probe. That costs one restart; the alternative is applying a block on evidence
gathered on somebody else's machine.

A **built-in skill** never appears on disk, so it only reaches a plan once
`measured.py merge` has folded the pasted `/skills` listing into the inventory.
It is the category `skillOverrides` is most used on, and without the merge the
mechanism has nothing to act upon.

## Why the origin decides

**Files the user owns.** Adding `disable-model-invocation: true` removes the
description from the prompt while leaving the entry invocable by name. This is
the best outcome available: no capability is lost, only the standing cost.

**Plugin files are off limits.** Plugin skills live under
`plugins/cache/<marketplace>/<plugin>/<version>/`. That directory is replaced on
every plugin update, so any edit there is temporary.

**A plugin skill cannot be cut individually.** Measured on Claude Code 2.1.278.
It is the single most expensive thing to get wrong, because a whole block of the
plan can look available and be worth nothing: neither the bare name nor the
qualified name reaches one through `skillOverrides`, the client shows every
plugin skill as `locked by plugin`, and the binary's override registration
returns early for plugin sources. The full evidence is in `case-studies.md`.

So the only lever over a plugin skill is the **whole plugin**, via `/plugin` or
`enabledPlugins`. Every plugin skill is propose-only, and `remediate.py` will
not plan one as an action.

**Read the `/skills` list before proposing an override.** It shows, per entry,
the source, the measured cost and whether it is locked. It is the cheapest
available ground truth and it costs no restart. Anything it reports as locked is
dropped from the plan automatically once the listing has been merged in. Prefer
it to any probe when the question is *can this entry be cut at all*.

**`skillOverrides` is all-or-nothing.** Unlike the frontmatter key, it removes
the entry completely: it cannot be invoked by name either. Always say so before
asking for approval. A user who expects to still be able to type the name will
be surprised, and surprise is how trust is lost.

**A plugin is more than its skills.** Disabling a plugin's skills does not touch
its hooks or its MCP servers, and a plugin's real function often lives in those
rather than in the skills. Check what else it provides before assuming a cut
breaks it, and confirm afterwards which of its functions still work.

**Price the all-or-nothing trade; do not narrate it.** `portfolio.py` emits one
block per plugin from the client's own `pluginUsage` counter: what its skills
cost at startup, how much of that comes from skills nothing has invoked, what
hooks and MCP servers go with it, and how many times the client records the
plugin being used. A plugin at a hundred thousand uses and a plugin at zero are
not the same decision, and the counter is the only thing that tells them apart.
State the trade and let the user decide. Never apply it.

*Covers: "an entry provided by a plugin", "an all-or-nothing mechanism",
"cutting skills from a plugin that has other functions".*

## The probe

A mechanism that is not verified *on the client version running here* cannot be
applied to more than one entry in a run. The sequence is:

1. Apply it to exactly one entry: `remediate.py probe --entry NAME --mechanism M --confirm`.
2. Ask the user to restart Claude Code and run the context breakdown again.
3. Compare against the figure from before. If the saving is real, re-run with
   `--verified M` and the rest of that category may proceed.
4. If nothing changed, revert the probe from the backup and mark that whole
   category as propose-only for this installation.

This costs one restart, and it is worth it. On one installation the unverified
category held most of the available savings, so applying it blindly would have
written useless keys into `settings.json` and reported a saving that never
happened (`case-studies.md`).

*Covers: "an unconfirmed mechanism for a category", "the mechanism does not work".*

## Ask the client before spending a restart

Two probes were once spent guessing name forms for a mechanism that could never
have worked, one restart each, both null. The `/skills` list held the answer the
whole time, for free.

When a mechanism does not appear to work, look for a place where the client
states the entry's own status — `/skills` for a skill, `/plugin` for a plugin —
before spending another restart on a second guess.

## Counting is a sharper signal than tokens

When a run applies several blocks at once and the measured saving comes up
short, the **entry count** says which block failed and the token figure does
not. Entry counts are small integers that add up exactly; token figures drift
for reasons outside the run's control.

Ask for both numbers every time, and reconcile them per block, not in total. A
worked example is in `case-studies.md`.
