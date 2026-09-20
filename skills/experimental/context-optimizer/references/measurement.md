# Measurement

Every figure this skill reports is either **measured** or **estimated**. Never
present one as the other, and never drop the label to make a table tidier.

## What the inventory must reach

A count that covers one directory is not an inventory. All five skill sources
count, and so do commands, agents, MCP servers, instruction files and hooks.
Three cases are easy to miss and must be handled explicitly:

- A **project-scope MCP server** may be declared inside the projects entry of
  the root configuration file with no `.mcp.json` anywhere. Read both places.
- An **instruction file** may import another; report both and the link between
  them.
- A **skill installed as a symlink** must be measured at its real target, and
  reported with both paths. The configuration directory itself may sit
  somewhere other than the default, so detect it rather than assuming it.

*Covers: "a source the existing tools do not cover", "an MCP server declared outside a .mcp.json file", "an instruction file with chained imports", "a skill installed as a symlink", "a configuration directory in a non-standard location".*

## Where measured data comes from

Two sources produce real numbers, and neither can be read from disk:

- **The context breakdown** gives the token cost per category: system prompt,
  built-in tools, MCP tools, skills, memory files, agents, messages.
- **The usage report** gives attribution per skill, subagent, plugin and
  individual MCP server, plus flags for behaviours above ten percent of recent
  usage.

Both are slash commands. The user runs them and pastes the output;
`measured.py` parses either the plain terminal form or the rendered table.

## What estimation is for

Without pasted data the run still completes. `inventory.py` derives a cost from
file size at four characters per token and labels it `estimated`.

That estimator has a known, directional bias: **it underestimates**. The causes
are text that is not English and text containing code, both of which tokenize
worse than the four-character average.

The bias reproduces across installations but is **not uniform**, so never apply
a single correction factor. Instruction files are the worst case — they are the
ones written in the user's own language and dense with commands and paths.
Skill descriptions, mostly plain English prose, land closer. Measured errors
from two machines are in `case-studies.md`; report the direction, not those
figures.

So an estimate is a **lower bound**. Say that out loud when reporting one. A
recommendation built on an estimate is weaker than one built on a measurement,
and the user deserves to know which they are getting.

A report that mixes both is normal: label each row, and make the total say how
much of it is measured.

*Covers: "the user supplies the data", "the user does not supply the data",
"a mixed report", "the estimate deviates from the measurement".*

## What cannot be obtained at all

- **Built-in tools.** They ship with the client, appear in the context breakdown
  and nowhere on disk, and none of their cost is reducible. On most
  installations they are the single largest block.
- **MCP tool schema sizes.** Schemas are deferred by default: only names load at
  startup. Their real weight shows up only in the breakdown.

Report these as `unavailable` rather than inventing a number.

**Built-in skills are the exception, and they matter.** They are not on disk
either, but the pasted `/skills` listing names them one by one, and
`skillOverrides` reaches them. That makes them the category the mechanism is
most used on in practice. `measured.py skills` parses the listing and
`measured.py merge` folds those rows into the inventory with `source:
"built-in"` and a measured cost. Without that merge, a whole category of
reachable savings is invisible to the plan.

## Compare categories, never totals

The breakdown's own categories move between runs for reasons that have nothing
to do with the work: loaded tools, connected MCP servers and deferred-schema
state all shift underneath. One machine's "System tools" line moved by over two
thousand tokens across three consecutive readings of the same session, with no
work done in between (`case-studies.md`).

A before/after built on the total will therefore show a saving that did not
happen, or hide one that did. **Always compare the specific category the work
touched** — Skills against Skills, Memory files against Memory files — and
report the total only as context, saying which parts of it are outside the
run's control.

Ask for the **entry count** alongside the tokens: "68 skills · 9k tokens". The
count is the harder number. It is what identifies which block of an applied plan
actually took effect — see "Counting is a sharper signal than tokens" in
`mechanisms.md`.

## Reconciling the two

When both exist, `measured.py reconcile` compares them and reports the
deviation and its direction. The measured figure is always authoritative. The
comparison is not there to correct the estimate — it is there to show the user
how far off an estimate-only run would have been.

## The floor

A large part of startup context cannot be cut by anything this skill does. The
system prompt and the built-in tools form a fixed floor, and on the two machines
recorded in `case-studies.md` it was roughly half the startup total.

Measure it on the machine in front of you; do not carry a figure over. Both
recorded installations happened to land near the same total, and that
coincidence has already misled one run.

State the floor early in the report, and state the **maneuverable remainder** as
its own figure. A user who expects to reach zero will judge a saving as failure;
a user who knows the remainder will judge the same number as most of what was
ever available.
