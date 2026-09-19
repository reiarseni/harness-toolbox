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

That estimator has a known, directional bias: **it underestimates**. On the
reference installation a project instruction file of 8,715 characters estimated
to 2,178 tokens and measured 3,600 — an error of about 39 percent low. The
causes are text that is not English and text containing code, both of which
tokenize worse than the four-character average.

So an estimate is a **lower bound**. Say that out loud when reporting one. A
recommendation built on an estimate is weaker than one built on a measurement,
and the user deserves to know which they are getting.

A report that mixes both is normal: label each row, and make the total say how
much of it is measured.

*Covers: "the user supplies the data", "the user does not supply the data",
"a mixed report", "the estimate deviates from the measurement".*

## What cannot be obtained at all

- **Built-in skills and built-in tools.** They ship with the client. They appear
  in the context breakdown and nowhere on disk. On the reference installation
  they were the single largest block at 12.6k, and none of it is reducible.
- **MCP tool schema sizes.** Schemas are deferred by default: only names load at
  startup. Their real weight shows up only in the breakdown.

Report these as `unavailable` rather than inventing a number.

## Reconciling the two

When both exist, `measured.py reconcile` compares them and reports the
deviation and its direction. The measured figure is always authoritative. The
comparison is not there to correct the estimate — it is there to show the user
how far off an estimate-only run would have been.

## The floor

A large part of startup context cannot be cut by anything this skill does. On
the reference installation the system prompt (2.4k) and built-in tools (12.6k)
formed a floor of about 15k out of 30.5k total.

State the floor early in the report. A user who expects to reach zero will
judge a 7k saving as failure; a user who knows the floor will judge the same
number as most of what was ever available.
