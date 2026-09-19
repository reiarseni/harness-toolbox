# Mechanisms

How an entry is cut depends entirely on where it comes from. Using the wrong
mechanism either does nothing or is silently undone by the next update.

## The table

| Origin | Mechanism | Manual invocation | Reversible | Verified |
|---|---|---|---|---|
| Skill or command in a file the user owns | `disable-model-invocation: true` in the frontmatter | kept | yes, remove the line | **yes** |
| Project skill under version control | same, on a branch when the tree is clean | kept | yes | no |
| Synced, built-in or plugin skill | `skillOverrides: {"<name>": "off"}` in `settings.json` | **lost** | yes, remove the key | **no** |
| Agent | remove the symlink or move the file to the archive directory | lost | yes, restore from backup | yes |
| Whole plugin | `enabledPlugins: {"<id>": false}` in `settings.json` | lost | yes, set back to true | yes |

"Verified" means confirmed to take effect on the machine this ran on. Anything
else must pass a probe first — see below.

## Why the origin decides

**Files the user owns.** Adding `disable-model-invocation: true` removes the
description from the prompt while leaving the entry invocable by name. This is
the best outcome available: no capability is lost, only the standing cost.

**Plugin files are off limits.** Plugin skills live under
`plugins/cache/<marketplace>/<plugin>/<version>/`. That directory is replaced on
every plugin update, so any edit there is temporary. The only durable lever for
a plugin skill is `skillOverrides`, which lives in the user's own settings.

**`skillOverrides` is all-or-nothing.** Unlike the frontmatter key, it removes
the entry completely: it cannot be invoked by name either. Always say so before
asking for approval. A user who expects to still be able to type the name will
be surprised, and surprise is how trust is lost.

**A plugin is more than its skills.** Disabling a plugin's skills does not touch
its hooks or its MCP servers. On the reference installation the memory system
was delivered by a `SessionStart` hook and an MCP server, so cutting fifteen
unused skills from that plugin left the memory fully intact. Check what else a
plugin provides before assuming a cut breaks it, and confirm afterwards which
of its functions still work.

*Covers: "an entry provided by a plugin", "an all-or-nothing mechanism",
"cutting skills from a plugin that has other functions".*

## The probe

A mechanism marked "no" under Verified cannot be applied to more than one entry
in a run. The sequence is:

1. Apply it to exactly one entry: `remediate.py probe --entry NAME --mechanism M --confirm`.
2. Ask the user to restart Claude Code and run the context breakdown again.
3. Compare against the figure from before. If the saving is real, re-run with
   `--verified M` and the rest of that category may proceed.
4. If nothing changed, revert the probe from the backup and mark that whole
   category as propose-only for this installation.

This costs one restart. It is worth it: on the reference installation the
unverified category accounted for roughly 5.7k of the ~7k of available savings,
so applying it blindly would have written useless keys into `settings.json` and
reported a saving that never happened.

*Covers: "an unconfirmed mechanism for a category", "the mechanism does not work".*

## Open question

Whether `skillOverrides` accepts a plugin skill by bare name, by
`plugin:name`, or not at all, is unknown. The probe answers it in one round. Do
not guess the form — try one, measure, and record the answer.
