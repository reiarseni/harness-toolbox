# Portfolio analysis

Cost tells you what an entry is worth keeping *against*. It does not tell you
whether the entry earns its place. Three questions do.

## 1. Is it actually invoked?

A bare search for a name is worthless. The name of every registered entry
appears in the prompt of every session, so it appears in every log.

Match invocations, not mentions:

- `"display":"/<name>"` — the user typed it as a command
- `Skill(<name>)` or `"skill": "<name>"` — it was invoked as a skill
- `/<name>` with a word boundary on both sides

Two failures seen on the reference installation, in opposite directions:

- An undelimited search for `do` returned 163 hits. All of them were `/doctor`
  and `/docs`. The real count was 3.
- An ecosystem tool inspected only the five most recent session logs and
  reported `release-plan` as unused. The prompt history held 212 mentions.

So: delimit the match, and widen the window. Use the usage report as the
primary measured signal and the full prompt history as the long-term one. When
they disagree, say so rather than picking the convenient one.

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
root **and one level down**. A monorepo keeps its manifests in subdirectories:
scanning only the root of the reference project found `docker` and `gitlab` and
missed Laravel, React, Vite, Tailwind and TypeScript entirely, which in turn
mislabelled two frontend skills as unrelated.

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
| Sole cover of a stack tag, no invocations | **keep**, and report the conflict |
| Invoked, unrelated to the stack | keep — practice beats the profile |
| No invocations, unrelated to the stack | suppress |
| No invocations anywhere | suppress |
| Invoked, but overlaps another entry | review-overlap — the user picks |

Attach the evidence to every row: invocation count, attribution share if
available, and the reason. A recommendation without evidence is an opinion, and
this skill has no standing to offer opinions about the user's work.

When the signals genuinely conflict, say there is no clear answer. Do not
invent a preference to look decisive.

*Covers: "a recommendation without sufficient evidence", "a plan ranked by saving".*
