# Where each piece comes from

Research of popular skills and methods (September 2026): what was taken or
dropped, judged against the constitution. Not needed at run time.

| Source | Contributes | Taken | Dropped (why) |
|---|---|---|---|
| matklad's ARCHITECTURE.md (+ `caidanw/skills/architecture-md`) | Codemap, absence invariants, boundaries, name-don't-link, short & stable | Core of code map and architecture; "no line numbers" | — (basis of P1) |
| acquire-codebase-knowledge (github/awesome-copilot) | 7 fixed docs, `scan.py`, `[TODO]`/`[ASK USER]` | Deterministic pre-scan, gap markers, pitfalls/risks doc | Organized by info type, not by modifier question |
| codebase-onboarding (everything-claude-code / alirezarezvani) | Recon → architecture → conventions; "where to look"; trace one request | Task → code table; end-to-end flows | 100-line cap: fine for onboarding, not for a whole system |
| deepwiki-skill (natsu1211) | Phased wiki, Mermaid validation, incremental `--update` | Mermaid validation, diff-based update, `_meta/` | `file:line` citations (rot); read-oriented exhaustive wiki |
| codebase-analysis-skill (Ycsyyds) | Mandatory `[VERIFY: file:line]`, automated checks, planned coverage | Coverage plan before exploring; evidence per claim | 1,500–3,000-line docs contradict "understand where everything is" |
| doc-it (Dosu) | Audit existing docs, never invent flags/fields, match repo style | Don't overwrite existing docs; no invention | API-reference focus |
| Code Tour (github/awesome-copilot) | Persona tours, path validator, SMIG (situation, mechanism, implication, gotcha) | Strict path validation; gotcha in every explanation | `.tour` format tied to VS Code |
| Diátaxis skills | Tutorial / how-to / reference / explanation, no mode mixing | Recipes = how-to; map & cards = reference; architecture = explanation | Learning tutorials: out of scope |
| grill-with-docs (most installed in category) | Resolve terms into a glossary | Glossary with "don't confuse with" | Interviewing: documake reads facts from code, asks only what code can't say |

## Sources for the context, structure and data sections

| Source | Contributes | Applied as |
|---|---|---|
| arc42 §3 *Context and scope* | Delimit the system from its environment; show **all** external interfaces; aggregate neighbours by criterion; business vs technical context | `01`: relationship with other repositories (all of them, grouped) + a stated boundary |
| arc42 §5 *Building block view* | Levels: context → top-level blocks → details | `01` context, `02` tree (level 1), `08` cards (level 2) |
| ER practice (Docsie, Diligent, Red Gate) | Start with core entities; > 15-20 entities → split by domain; one diagram per bounded context + a context map; cross-domain references only as names | `06`: core only, ≤ 12 per diagram, groups, hubs named once |
| ER tools (eralchemy, ErdDocs, Liam ERD, prisma-erd-generator, tbls, SchemaSpy) | Draw *everything*, many need a live DB, Chromium or an external service | `schema.py`: static, zero-dependency, proposes the core; the checker verifies the drawing |
| Mermaid erDiagram syntax | `\|\|--o{`, `\|o--o{`, `}o--o{`, dashed `..` for non-identifying | Cardinality convention in `06` and the checker's parser |
| GitLab multi-project pipelines, `include: project`, components, submodules, package registry, `CI_JOB_TOKEN` allowlist | The declared ways a GitLab repo depends on another | Evidence list of `repo.py`; job-token allowlist as a cross-repo trap in `12` |
| Nx / Turborepo / pnpm / Cargo / go.work / uv / Maven / Gradle workspace docs | How a monorepo is declared | Repository-kind detection |

## What documake adds

- Explicit constitution with checkable derived rules and a builder-phrase
  list enforced by script.
- Mandatory "How to modify it" and "Who uses it" in every module card.
- **Context first**: a forced opening doc with stack + versions, repository kind
  (monorepo, multi-app, single), organization and every detected link to other
  GitLab repos, each with its evidence; the one thing it cannot see (who
  consumes this repo) is written as an explicit question.
- **Abbreviated structure** whose every path is verified against disk.
- **Data section from the code, core only**: a static extractor (7 schema
  formats, verified against two real projects) proposes the core and the
  checker verifies each entity and relation of the drawing.
- The newcomer test: a docs-only reader answers modifier questions and a
  second agent grades them against code — the only check that measures P1 and
  P2 directly.
- Selectable output language, Spanish by default.
