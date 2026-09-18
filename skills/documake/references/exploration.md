# Exploration subagent briefs

Paste the matching brief **in full** into the subagent prompt, filling the
`<placeholders>`. Subagents don't see this conversation; everything they need
must be in the prompt. Briefs are in English; reports come back in English.

## Brief A — Area

```
You are a code explorer. Your report feeds documentation for ONE area of a
repository, written for a developer who arrives new and must MODIFY the code
(not for its author).

Repo: <root>
Area: <area name>
Area paths: <paths>
System context (README + scan): <2-4 sentences>

Read the area's code (not just file names). Return ONLY facts, each with
evidence as `path` + `Symbol` (never line numbers). If something can't be
verified, write [UNVERIFIED] and why.

Markdown, ~120 lines max:

## Responsibility
1-3 sentences: what it does and what it does NOT do (what looks like it
belongs here but lives elsewhere).

## Key pieces
| Symbol | Path | What it does (1 sentence) |   (max 12; the ones a modifier would touch)

## Outward interface
What other areas use: exported functions/classes/endpoints/events.

## Dependencies
- Uses from other areas: <area> → `symbol` (what for)
- Used by: grep for importers of this area. List who and how many call sites.

## State and data
What it persists, caches, global state/singletons, where it's configured.

## Invariants and contracts
Rules the code assumes that you'd break unknowingly. Include ABSENCE rules
("this module never calls X", "no writes outside Y") and verify them with grep
(include the grep).

## Typical changes
For 2-4 realistic changes here: which files and symbols to touch, in order,
and which test covers it.

## Pitfalls
What would surprise a newcomer: side effects, init order, misleading names,
code that looks dead but isn't, duplication, magic (decorators,
metaprogramming, registration by convention).

## Tests
Where this area's tests live, how to run only them, what is NOT covered.

## Terms
Domain words used in this area's code, with their meaning according to the code.

## Open questions
What reading the code can't resolve; only a person would know.

No code blocks longer than 5 lines. No quality opinions except under Pitfalls,
always backed by a checkable fact. Never copy secrets, passwords, tokens or
real account emails — name the variable/setting and where it is read.
<If a flow was folded into this area, append here: "Also trace flow <name>
(trigger → effect) using the ## Steps / ## Branches and errors / ## Side
effects sections of the flow brief.">
```

## Brief B — Critical flow

```
You are a code explorer. Trace ONE flow end to end in this repo, to document
it for a developer who will have to modify it.

Repo: <root>
Flow: <name>
Trigger: <e.g. "POST /orders", "command `app sync`", "message on queue X", "daily cron">
Expected effect: <e.g. "order saved and email sent">

Find the real entry point and follow execution by reading the code.
Return, ~80 lines max:

## Steps
1. <what happens> — `path` `Symbol`
2. ...
(include validations, transactions, external calls, emitted events, retries,
where errors are handled)

## Branches and errors
Alternative paths and what the user/caller sees on each failure.

## Side effects
Everything left changed: DB, files, queues, caches, emails, audit logs.

## Extension points
Where someone would hook in to change or extend this flow.

## Diagram
One Mermaid `sequenceDiagram`, ≤ 8 participants, named by role (not class name).

## Open questions

Every step with `path` + `Symbol` evidence. No line numbers. If a hop can't be
followed statically (DI, dynamic dispatch), say so and explain how it resolves
at runtime.
```

## Brief C — Cross-cutting

```
You are a code explorer. Investigate the CROSS-CUTTING aspects of this repo,
to document them for a developer who arrives new.

Repo: <root>
Detected stack: <from scan>

Return, ~120 lines max, with `path` + `Symbol`/key evidence:

## Running locally
Real commands (from manifests, Makefile, scripts, docker-compose, README;
verify against the files). Requirements: versions, services, REQUIRED env
vars (name, where read, what happens if missing).

## Tests
Framework; how to run all / one file / one test; fixtures and test data; what
must be running.

## Configuration
Where it loads, precedence, config files, flags.

## Errors and logging
Error convention (custom exceptions, codes, Result…), where caught, what is
logged and where.

## Authentication and authorization
If present: where identity and permissions are decided.

## Persistence
Engine, ORM, where schema and migrations live, how to create a migration.
(Table-level facts are the data explorer's job: Brief D.)

## External integrations
APIs, queues, cloud services, SDKs: where called and how configured.

## Build, CI and deploy
What the pipeline does, what blocks a merge, how it deploys (if visible).

## Relationship with other repositories
`repo.py` already lists declared links (submodules, CI include/trigger, images,
git dependencies, registries). Confirm each in the code and ADD what it cannot
see: env vars / settings that hold URLs of other internal services (name +
where read), SDK/API clients for internal services, contracts shared with
another repo (OpenAPI/proto/types copied from elsewhere), deploy scripts that
pull from or push to another repo. For each: repo or service, direction (this
repo calls it / it calls this repo), evidence `path` + `Symbol`. Anything that
says who CONSUMES this repo → put it under Open questions ("only a person knows").

## Observed conventions
Naming, file layout, import style, repeated patterns. Only what the code
shows, not what an unused linter config says.

## Open questions
```

## Brief D — Data (one explorer, only if schema.py found tables)

```
You are a code explorer. Explain the CORE tables of this repo's database for a
developer who must modify it. Facts only, with evidence `path` + `Symbol`.

Repo: <root>
Core tables proposed by the static extractor: <list from schema.txt>
Groups: <groups from schema.txt>
Hub tables (referenced by most others): <hubs>

For each core table (max ~12), ~120 lines total:
## <table>
- Meaning (1 line): what one row is, in domain words.
- Model / migration: `path` · `Symbol`.
- Written by: which module/service creates or changes rows (`path` · `Symbol`).
  Read by: main readers.
- Key columns a modifier must know (status/enum values, JSON shapes, soft delete, tenant scoping).
- Relations that have NO foreign key (columns holding ids of other tables, JSON refs): name them.
- Rules the code enforces that the DB does not (uniqueness by tenant, cascade done in code, immutability).
## Groups
For each group: what it models and how its tables relate (one paragraph, table names in backticks).
## Rest
Any non-framework table NOT in the core: purpose in one line.
## Open questions
No secrets, no sample data. Never invent a table or a column.
```

## Cross-checking the reports

1. **Asymmetric deps.** A says it uses B but B doesn't list A → grep and fix
   the wrong one.
2. **Conflicting terms.** Same name, different meanings in two areas →
   glossary entry that says so explicitly.
3. **Gaps.** Code dirs no report mentions → assign to an area, or "out of
   scope" with a reason.
4. **Open questions.** Answerable by reading more → targeted subagent. Only a
   person knows → ASK marker.
