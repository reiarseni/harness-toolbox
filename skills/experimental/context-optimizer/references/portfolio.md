# Portfolio analysis

Cost tells you what an entry is worth keeping *against*. It does not tell you
whether the entry earns its place. Three questions do.

## 1. Is it actually invoked?

**Read the client's counter first, and prefer it to everything else.**
`~/.claude.json` holds `skillUsage`: a map of `{name: {usageCount,
lastUsedAt}}` the client maintains itself. It is exact and it costs one file
read. Every technique below exists only for what the counter does not cover,
such as agents.

This was learnt expensively: a run that scraped logs instead recommended
suppressing thirteen entries the counter showed had been used, and eight were
cut before the counter was found (`case-studies.md`).

Three properties decide how the counter must be read, and the code depends on
each of them:

- **Absence is the signal, not zero.** The client writes a key on first use and
  never writes a zero. `lookup_counter` therefore returns `None` for an absent
  key, and the report says *"no entry in the client skillUsage counter"* rather
  than *"0"*. A present zero would be an anomaly worth reporting, not acting on.
- **Silence needs a horizon.** "Never invoked" means nothing until you can say
  over how long. `numStartups`, from the same file, is that horizon, and it goes
  into the method string: *"no entry … across 664 recorded startups"*.
- **A rename leaves the old key behind**, holding all the history. When an entry
  has no counter but a near-identical key exists, that is a conflicting signal:
  the entry is kept, the near-miss is reported, and the user is asked. It is
  never counted as this entry's own usage.

**Namespaces are never crossed.** Matching a counter key by its unqualified tail
made the bare name `do` inherit `claude-mem:do`'s invocations. Matching is exact
first, then on the whole normalised key, never on the part after the colon.

### The audit contaminates its own corpus

The session running the audit writes a log like any other, and that log names
every entry under discussion — repeatedly, including in the sentence proposing
to cut it. Scrape it and the audit becomes its own evidence: one synced skill
went from `invocations=0` to `invocations=1` between two passes purely because
the intervening conversation had discussed it.

`load_history` has two defences, in order of preference:

1. **The session id**, passed as `--session-id` or read from
   `CLAUDE_SESSION_ID`. This is exact and excludes only the audit's own log.
2. **A fifteen-minute window**, the fallback when no id is available. It is
   blunt — it also discards legitimate recent sessions — but that is the safer
   direction to err in.

If you scrape by hand, exclude the current session explicitly, and treat any
count of exactly 1 on an entry you have been discussing as noise until proven
otherwise.

A bare search for a name is worthless. The name of every registered entry
appears in the prompt of every session, so it appears in every log.

Match invocations, not mentions:

- `"display":"/<name>"` — the user typed it as a command
- `Skill(<name>)` or `"skill": "<name>"` — it was invoked as a skill
- `"subagent_type": "<name>"` — it was invoked as an **agent**
- `/<name>` with a word boundary on both sides

**An agent is the dangerous case.** It is not invoked the way a skill is, it
has no `skillUsage` entry to fall back on, and its mechanism does not keep
manual invocation. Get the pattern wrong and the skill proposes archiving
agents that are in daily use, with no counter to contradict it — which is
exactly what a live run did before `subagent_type` was added.

**Scrape session logs only, and scrape all of them.** An agent invocation is
recorded in the transcript of the session that made the call, not in the
subagent's own transcript, and a tool's scratch directory holds no invocations
at all. A recency-ordered window over everything is worse than useless here:
on one machine the forty most recent logs were 38 scratch files and 2 real
sessions.

Two failures seen in practice, in opposite directions:

- An undelimited search for a short name returned over a hundred hits, all of
  them longer names that merely started with it.
- An ecosystem tool inspected only the five most recent session logs and
  reported a heavily used skill as unused.

So: delimit the match, and widen the window. The walk is recursive, so subagent
transcripts are reached too — the same set of files every other log survey in
this skill counts. Use the usage report as the primary measured signal and the
full prompt history as the long-term one. When they disagree, say so rather than
picking the convenient one.

*Covers: "a name that prefixes other names", "an insufficient analysis window",
"an entry unused in every source".*

## 2. Does it overlap something else?

Two entries overlap when they carry the same capability, whether or not they
share a name. Two signals:

- **Normalised name match** — strip any `plugin:` prefix, drop `openspec-` /
  `opsx-` prefixes and `-change` suffixes, then compare. This is what pairs a
  command with the skill that does the same thing.
- **Description keyword overlap** — Jaccard similarity above roughly a third,
  after removing stopwords.

Report the level of each side. A cross-level overlap matters more than a
same-level one: an entry that duplicates something built in is paying rent for
a capability that is already free.

A plugin that is installed but not enabled costs nothing and must never appear
in the plan.

*Covers: "a command and a skill doing the same thing", "a user skill duplicating a built-in capability", "an installed but disabled entry".*

## 3. Does it match the work?

Detect the stack from marker files and declared dependencies, at the project
root **and one level down**. A monorepo keeps its manifests in subdirectories,
and a root-only scan misses most of the stack — which then mislabels the skills
that cover it as unrelated.

Then report three things:

- which entries cover each detected tag
- which tags nothing covers — a gap, reported as a gap and never filled with an
  invented entry
- which entries match nothing in the stack

*Covers: "an entry unrelated to the project stack", "an uncovered stack capability", "an entry unrelated to the stack but genuinely used".*

## Turning the three into an action

| Situation | Action |
|---|---|
| Already costs nothing at startup | keep |
| No counter, but a near-identical key exists | **keep**, and report the possible rename |
| Sole cover of a stack tag, no invocations | **keep**, and report the conflict |
| Invoked, unrelated to the stack | keep — practice beats the profile |
| No invocations, unrelated to the stack | suppress |
| No invocations anywhere | suppress |
| Invoked, but overlaps another entry | review-overlap — the user picks |

Attach the evidence to every row: invocation count, attribution share if
available, and the reason. A recommendation without evidence is an opinion, and
this skill has no standing to offer opinions about the user's work.

## 4. What does a whole plugin cost?

A plugin skill has no individual mechanism, so the only question worth asking
about one is the plugin-wide question. `portfolio.py` answers it per plugin,
from the client's own `pluginUsage` counter: the startup cost of its skills, how
much of that comes from skills nothing has invoked, the hooks and MCP servers
that would go with it, and how many times the plugin has been used.

Present the trade as one sentence with all four numbers in it. It is always
propose-only.

When the signals genuinely conflict, say there is no clear answer. Do not
invent a preference to look decisive.

*Covers: "a recommendation without sufficient evidence", "a plan ranked by saving".*
