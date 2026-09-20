---
name: documake
description: Explores a whole repository with parallel subagents and writes docs/ for a developer who needs to MODIFY the system, not for the one who built it — opens with a first-change tutorial whose steps are executed, then stack, repository kind (monorepo or not), organization and links to other repos, an abbreviated tree, code map, architecture, core database tables with their relations, end-to-end flows, one card per module, change recipes (the single home for procedures) and glossary. Every claim is checked against the code, a script verifies paths and symbols exist, and a zero-context reader tests the result. Output language is selectable (Spanish by default). Use when the user types /documake, asks to "document this repo", "genera la documentación del proyecto", "que alguien nuevo entienda el código", wants architecture/onboarding docs, or wants those docs updated after code changes.
license: MIT
compatibility: Requires Python 3.12+ (stdlib only) and git. Designed for Claude Code; also works in OpenCode.
metadata:
  author: reiarseni
  version: "1.1.0"
disable-model-invocation: true
argument-hint: "[repo path] [--lang es|en|<code>] [--publish zensical|vitepress|docsify|gitlab-wiki|github-wiki] [--update] [--only <module>]"
allowed-tools: Read Glob Grep Bash Agent Write Edit AskUserQuestion
---

# documake — document for whoever has to modify it

## Constitution (non-negotiable)

1. **A human can enter the project, read the documentation and understand
   where everything is and how it works.**
2. **The documentation explains the system from the perspective of a developer
   who needs to modify it, not from the perspective of the one who built it.**

Everything below derives from these two. When a rule conflicts with them, they
win. Derived rules and their concrete tests: [references/constitution.md](references/constitution.md).
**Read it before writing any documentation.**

| The builder writes… | The modifier needs… |
|---|---|
| "First we did X, then migrated to Y" | How it is **now**, and which current constraint explains it |
| "We use the Repository pattern" | "To change how an order is saved, edit `orders/repo.py`" |
| Inventory of everything | What gets touched **together**, and what breaks otherwise |
| Undefined internal jargon | Glossary; term defined on first use |
| The "obvious" (omitted) | Pitfalls: what reading the code does not reveal |
| Confidence | Evidence: the path and symbol where it can be checked |

## Arguments

- none → document the repo in the current directory. `<path>` → that repo.
- `--lang <code>` → output language. **Default `es` (Spanish from Spain).**
  Built in: `es`, `en`; any other code works via
  [references/languages.md](references/languages.md). A natural-language
  request ("documéntalo en inglés") counts the same.
- `--publish <target>` → after the docs are CLEAN, also publish them (Step 9):
  `zensical`, `vitepress` (static sites: GitHub Pages, GitLab Pages or a
  Docker/nginx image for any server), `docsify` (no build), `gitlab-wiki` or
  `github-wiki`. Default: none — the markdown in the repo is the
  output. Deployment knowledge lives in [references/deploy.md](references/deploy.md).
- `--update` → incremental mode (Step 7).
- `--only <module>` → redo only that module card and what references it.

## Runtime notes (Claude Code and OpenCode)

- **Skill directory.** `scripts/…` and `references/…` in this file are relative
  to the folder that holds this `SKILL.md`. Resolve it **once, first**, and
  substitute the absolute path for `$DOCUMAKE` in every command (shell state may
  not persist between calls):
  ```bash
  for d in .claude/skills/documake .opencode/skills/documake .agents/skills/documake \
           ~/.claude/skills/documake ~/.config/opencode/skills/documake ~/.agents/skills/documake; do
    [ -f "$d/scripts/scan.py" ] && { (cd "$d" && pwd); break; }; done
  ```
  (run it from the repo root; it prints the absolute path to use).
- **Tool names.** `AskUserQuestion` = your question tool (OpenCode: `question`),
  or ask in chat and wait. "Subagent" = your task tool (Claude Code: `Agent`
  with `Explore`/`general-purpose`; OpenCode: `task` with `explore`/`general`).
- **No subagents, or a weak model?** Do the same work sequentially: one area
  or flow at a time, write its findings to `$SCRATCH/findings/<name>.md`
  **before** starting the next, and never keep file contents in your context.
  The findings files are your memory. Tell the user you fell back to this.
- **`$SCRATCH`** = the scratchpad dir if the runtime gives one, else
  `/tmp/documake-<repo-name>/`. Never inside the repo.
- **Read permissions.** Some setups deny reading `.env*` (including
  `.env.example`). For env var **names** use
  `grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' <file>` through the shell (names only,
  never values).

## Step 0 — Preflight

1. Find the repo root (`git rev-parse --show-toplevel` or the given path).
2. **Choose the docs folder** — it must be **versioned**, or CI, the wiki
   sync and teammates will never see it. Test candidates with
   `git check-ignore -q <dir>/x` (exit 0 = ignored, **do not use**):
   1. `docs/` if it exists or is free, and is not ignored;
   2. else a tracked `doc/` (some repos ignore `docs/` as scratch space and keep
      real docs in `doc/`);
   3. else ask.
   If the chosen folder already has content NOT generated by documake (no
   `_meta/documake.json`), **do not overwrite it**: write to its subfolder
   `sistema/` (`system/` for `en`); if the folder is new/empty, use it directly.
   Say which folder you chose and why (one line), without asking, unless
   `<folder>/sistema` also has foreign content.
3. If `documake.json` exists and `--update` was not given, ask: update or regenerate.
4. **Language:** `--lang` or the user's request; otherwise **`es`, without
   asking**. In `--update`, keep the language in `documake.json` (never mix
   languages in one docs folder). If existing repo docs use another language,
   keep yours and mention it in the final report. Load
   [references/languages.md](references/languages.md) and use that language's
   file names, required headings and markers everywhere.
5. **No destructive git.** This skill only writes to the docs folder. Never
   `git restore`, `clean`, `checkout`, `push`.

## Step 1 — Recon (cheap, deterministic)

```bash
python3 $DOCUMAKE/scripts/scan.py   <root> > "$SCRATCH/scan.txt"     # inventory
python3 $DOCUMAKE/scripts/repo.py   <root> > "$SCRATCH/repo.txt"     # stack, repo kind, other repos, tree
python3 $DOCUMAKE/scripts/schema.py <root> > "$SCRATCH/schema.txt"   # tables, FKs, core proposal, draft ER
```

`$SCRATCH` = your scratchpad dir, never inside the repo. The scan gives
languages and line counts, manifests and scripts, candidate entry points,
**route/controller files** (the best map of what an app exposes), directory
weights, test dirs, CI, containers, existing docs, git hotspots, largest files,
and **"probably out of scope"** (data dumps, docs-only dirs such as planning
or agent config) — take that list straight into the plan.

`repo.py` gives, each with its evidence file: the **stack with versions** (by app),
the **repository kind** (monorepo with workspace tooling / multi-app repo /
single project), the **deployable units** (compose services, Dockerfiles, CI
jobs), every **link to other repositories** (submodules, CI `include: project`,
`trigger`, components, registry images, git dependencies, package registries,
build contexts outside the repo, URLs to the same GitLab host) and the
**abbreviated tree**, plus an **Operations evidence** block: compose bind mounts/named
volumes (flagging host paths git ignores), git-ignored folders that exist on disk
(likely uploads/user data), and references to storage/S3, backup tooling and
observability. Document **only what is in git**; what git ignores is documented
only in `13` as data outside git, with its reason. `schema.py` gives the **tables and foreign keys** read
statically from the code (SQLAlchemy/Alembic, Django, Laravel, Prisma, Rails,
SQL DDL), the **hub tables**, a ranked **core proposal**, the groups and a
**draft erDiagram**. They propose; you verify and decide. If `schema.py` finds
no tables but the repo clearly has a database (its ORM isn't supported: see its
NOTE), read the entity files by hand and say so.

Then read **yourself** only: README, CLAUDE.md/AGENTS.md, existing docs
(skim), manifests, and the **names** (not contents) of files in the heaviest
dirs. No source code yet.

Audit existing docs for drift — they are often stale, and a newcomer trusts
them:

```bash
python3 $DOCUMAKE/scripts/check_docs.py <root> --audit README.md CLAUDE.md <other existing docs>
```

Each `[DRIFT]` is a path the docs cite that no longer exists. Never copy
those; list the misleading ones in the pitfalls doc ("`CLAUDE.md` cites
`pages/Projects/`; pages are flat files like `ProjectsPage.tsx`").

## Step 2 — Coverage plan (the only checkpoint with the user)

Split the system into **areas** (5–12): code a modifier would think of as one
unit — a service, package, layer, domain. Don't blindly mirror folders; if
`utils/` is half the domain, say so.

- **Front + back in one repo → slice vertically by domain**: each area holds
  its controllers + models + pages + API client functions ("invoicing", not
  "backend"/"frontend"). A change request arrives as "invoices", not as
  "the PHP side". Add one **shell** area for what every page shares (router,
  HTTP client, shared types, layout, UI kit, i18n).
- Put every top-10 hotspot inside a named area; they are where modifiers go.
- Pick the 3–6 critical flows by what crosses areas and what hotspots touch
  (auth/tenant resolution is almost always one).

Write `$SCRATCH/plan.md`:

```markdown
## Areas
| Area | Paths | Why it is one unit | Weight (lines) |
## Critical flows (3–6)
- <name>: trigger → … → effect   (what a modifier will touch most)
## Documents that apply   (conditional ones yes/no and why)
## Out of scope           (generated, vendor, fixtures… and why)
```

Show it with **one** `AskUserQuestion` call carrying **two** questions, so a
"no" never costs a second round:

1. *Areas* (single-select): "Go with these N areas" (list them in the
   description) / 1–2 **concrete** alternative splits you considered (e.g.
   "Split invoicing and payables"). "Other" lets the user type their own.
2. *Flows* (**multiSelect**): your proposed flows **plus 1–3 runner-up
   candidates** as separate options, each with trigger → effect in the
   description. The proposed ones are listed first.

Never offer bare "Adjust areas / Adjust flows" options: they force a second
question. Skip the checkpoint for small repos (< 5k lines and ≤ 4 areas) and
say so.

## Step 3 — Deep exploration in parallel

**Do not read the codebase in your own window.** Launch read-only explorer
subagents ("very thorough"), **all in one message** (no subagents → see
Runtime notes):

- One **per area** (group small areas; at most 6).
- One **per critical flow that crosses areas**, tracing it end to end. A flow
  that lives inside one area is appended to that area's brief ("also trace
  flow X") instead of getting its own agent.
- One **cross-cutting**: config, errors, logging, auth, persistence, tests,
  running locally, **and how this repo relates to others** (Brief C).
- One **data** explorer if `schema.py` found tables (Brief D): meaning, owner
  module and invariants of the core tables.

**Hard cap: 8 subagents in this step** (the data explorer counts). Over the cap, fold flows into areas
first, then merge the smallest areas.

Paste the matching brief from [references/exploration.md](references/exploration.md)
in full, filling the `<placeholders>`. They return **facts with evidence**
(`path` + `Symbol`), not prose. Save each report to `$SCRATCH/findings/<area>.md`.

Then cross-check: asymmetric dependencies (A uses B, B doesn't list A),
terms with different meanings across areas (→ glossary, ambiguity explicit),
gaps (dirs no report covers). Important gap → targeted follow-up subagent;
otherwise → TODO marker.

## Step 4 — Writing

**Before any doc**, create `<docs>/_meta/documake.json` with at least
`{"language": "<code>", "database": <true if schema.py found tables, else false>}` (plus the `"structure"` block from `languages.md`
for languages other than `es`/`en`). The checker reads it.

Output layout — **the numbering is the reading order** (general → specific:
"where am I" → "what is it" → "how it works" → "how to change it"). Names shown
for `es`; `en` and others in `languages.md`; skeletons in
[references/templates.md](references/templates.md):

```
<docs>/                       ← the folder chosen in Step 0 (docs/, doc/sistema/…)
├── README.md                 ← START HERE: what it is, reading paths by goal
├── 00-primer-cambio.md       ← FORCED: tutorial, clean clone → a real change of your own, steps EXECUTED
├── 01-stack-y-repositorio.md ← FORCED: stack+versions, repo kind (mono/multi/single), how it is organized,
│                                       relationship with OTHER GitLab repos
├── 02-estructura.md          ← FORCED: abbreviated tree (≤ 70 lines), every path real
├── 03-vision-general.md      ← problem, actors, one-page diagram
├── 04-mapa-del-codigo.md     ← codemap + "I want to change X → start at Y" table
├── 05-arquitectura.md        ← layers, boundaries, invariants, dependencies
├── 06-datos.md               ← FORCED if a database exists: core tables + relations (erDiagram), groups, rest
├── 07-flujos/<flow>.md       ← each critical flow, step by step
├── 08-modulos/<area>.md      ← one card per area (fixed format)
├── 09-recetas-de-cambio.md   ← "how to add/change…" with every touch point
├── 10-entorno-y-pruebas.md   ← run, test, debug; real commands
├── 11-integraciones.md       ← (if external systems that are not repos) APIs, queues, secrets
├── 12-trampas-y-riesgos.md   ← what reading the code does not reveal
├── 13-operacion-y-datos.md   ← FORCED when there is evidence: Docker, data outside git (+why), backups (DB + files, disk/S3), logs/observability
├── glosario.md
└── _meta/
    ├── documake.json         ← language, "database": true|false, source commit, date, areas
    └── cobertura.md          ← covered / not covered / markers / reader test
```

**Forced sections.** `00`, `01`, `02` and (when tables exist) `06` are mandatory and
enforced by the checker: required headings, non-empty sections, every tree
path real, every ER entity a real table, every ER relation backed by a real FK
(or labeled *logical*), every detected link to another repo mentioned. The only
way to skip `06` is `"database": false` in `_meta/documake.json`, with the
reason in the coverage file.

`13-operacion-y-datos` is also enforced, but only when `repo.py` shows evidence
(compose/Dockerfile, git-ignored data folders or mounts, storage/backup/
observability references): every ignored data folder or bind mount must appear
under "Datos fuera de git" with what it stores and why it is outside git. Do not
decide from the folder name: for each ignored path grep who writes it, ask whether
the app can rebuild it, and whether deleting it loses user data. Strong signals
(uploads/media/DB volumes/ignored bind mounts) are errors; generic names
(`data/`, `exports/`) are grouped warnings; a folder judged disposable goes in
`"operations_ignore": [{"path", "reason"}]` (reason mandatory). Skip it
only with `"operations": false` in `_meta/documake.json` and the reason in
coverage. Backups usually live outside the repo: use the evidence first, and
what the code does not show is an ASK marker (what is backed up, DB and uploaded
files, disk or which S3, frequency, restore) — ask the user if they are present;
never invent. The README also has a forced **Ruta feliz (Quickstart)**
(1–5 numbered steps, clean clone → first result) and, in multi-app repos, every
command in the recipes and the quickstart states its working directory
(`cd backend && …`). `_meta/cobertura.md` lists each open marker with its exact
text and doc; the checker verifies both.

**Writing order (fixed).** Write what comes from tool output first: it fixes the
vocabulary (area and table names) for everything else.
1. `01-stack-y-repositorio` (from `repo.txt`), 2. `02-estructura` (from its tree),
3. `06-datos` (from `schema.txt` + the data explorer), then
4. `08-modulos` → `09-recetas` → `07-flujos` → `04-mapa` → `05-arquitectura` →
   `10-entorno` → `11-integraciones` → `13-operacion-y-datos` → `12-trampas` → `glosario`, then
5. `03-vision-general` and `README` (they summarize the rest), and last
6. `00-primer-cambio`, **running every step** as you write it (Step 6 covers it).

Recipes come right after the cards **and before the flows** because both link
them: the cards name the change, the recipes own the steps (R2.9). While writing
a card, list its typical changes as recipe names; write those recipes next, then
turn the card's list into links. If a card or a flow ends up with numbered steps
of its own, the procedure was written twice — move it and link it.

How to write the three forced docs (details and skeletons in `templates.md`):
- **01 — Repository type**: name the kind and give the evidence (workspace
  file, several manifests, compose services); say which parts deploy
  independently. **Relationship with other repositories**: one row per
  detected link — repo path, direction (consumes / is consumed), kind, evidence,
  how a change spans repos. Group many similar neighbours. The link finder
  cannot see who *consumes this repo*: write it as an ASK marker. If nothing was
  found, say what was searched. Cross-repo traps (job-token allowlist, pinned
  `ref`s, submodule bumps) go to `12`.
- **02 — Tree**: start from the script's tree, keep ≤ 70 lines, annotate each
  line with its role, add nothing that isn't on disk.
- **06 — Data**: draw **only the core** (start from the proposed core; ≤ 12
  entities per diagram; if there are groups, one small diagram per group and
  cross-group references as names only). Name hub tables (e.g. `tenants`) once
  in prose instead of drawing every arrow. Every remaining non-framework table
  appears in "Other tables" with one line. Use `||--o{` for a required FK,
  `|o--o{` for a nullable one, `}o--o{` for M:N, and the word "logical" in the
  label for links with no FK.

Writing rules (full list in `constitution.md`):

- **Name, don't link to lines.** Paths and symbols in backticks
  (`src/orders/service.py`, `OrderService.cancel`) so they are searchable.
  Never line numbers: they rot on the first commit.
- **Every module card has "How to modify it"** — the entry points and a **link
  to the recipe** that holds the steps, never the steps themselves — and
  **"Who uses it"** (blast radius). Without them it's unfinished.
- **Procedures live in the recipes, once** (R2.9). Cards and flows say where and
  what breaks; the recipes say how. The checker warns when a card or a flow
  carries numbered steps and no link to the recipes.
- **Link files, never `#anchors`.** Slugs differ between GitLab, Zensical and
  Docsify (accents break them); name the section in the text instead.
  Relative `.md` links work on every target.
- **Cross-references are links, and the link text is the destination's name.**
  Never "(see the pitfalls doc)" in prose: on a wiki that's a dead end. Never
  `[Flows](07-flows/one-flow.md)` either — it reads like the group and lands on
  one page. Both are checked.
- **Scannable.** A sequence is a numbered list, not a chain of arrows in a
  paragraph. Keep sentences under ~45 words and tables under ~6 columns (fold
  the path into the symbol cell); wider tables scroll sideways on a wiki and go
  unread. Both are checked.
- **Keep the making-of out.** No audit notes, no "verified with grep", no
  newcomer-test score, no mention of subagents — that lives in `_meta/`. Open
  markers are gathered in the coverage file; leaving them mid-paragraph makes
  the page read as unfinished.
- **Mermaid only when the information is a relationship.** ≤ 12 nodes; node
  labels use glossary terms, not class names.
- **Present tense, impersonal.** No "we decided", "initially", "our idea".
  History only when it explains a current constraint — written as the constraint.
- **Mark what you don't know**: TODO marker if more reading would answer it;
  ASK marker if only a person knows (intent, business decision). Never fill
  with guesses.
- **Short and stable.** Document what changes rarely (structure, contracts,
  invariants).
- Respect pre-existing docs; don't duplicate them — unless the audit showed
  them stale; then point to them with a warning. **Cite files outside the docs
  folder as backticked paths** (`README.md`, `doc/backlog/x.md`), never as
  markdown links: links leaving the folder break on wiki/site targets (the
  checker warns). Markdown links are for pages inside the docs folder.
- **No secrets.** Never copy credentials, tokens, passwords, emails of real
  accounts or `.env` values found in code or docs (repos often have "dev
  credentials" in CLAUDE.md). Name the variable and where it's read; point to
  `.env.example`.
- **Everything in the chosen language** — headings, tables, diagram labels,
  markers. **Never translate code identifiers**: paths, symbols, commands, env
  vars, table names. Subagent reports may be in any language; translate when writing.


## Step 5 — Mechanical check

```bash
python3 $DOCUMAKE/scripts/check_docs.py <root> --docs <docs-dir> [--lang <code>] [--mermaid]
```

Language defaults to `documake.json`, else `es`. Checks: backticked paths
exist; cited symbols appear in code; internal links; mermaid blocks empty or
untyped (compiled for real with `--mermaid` if `mmdc` exists); diagrams > 12
nodes; line-number references; module cards missing required sections;
builder-perspective phrases; open markers; required docs present; **forced
sections** (`01` headings and non-empty sections, links found by `repo.py`
mentioned; `02` tree paths exist and ≤ 70 lines; `06` required when tables
exist, entities and relations verified against `schema.py`, ≤ 12 entities,
every non-framework table mentioned).
**Fix every ERROR and re-run until CLEAN.** Review WARNs one by one (a symbol
generated at build time is legitimate).

## Step 6 — The newcomer test (validates the constitution)

The script checks the docs are **true**; this checks they are **useful**.

1. Write 8–10 **concrete, checkable** modifier questions about this repo,
   from the findings, mixing the types in
   [references/verification.md](references/verification.md): locate, impact,
   recipe, invariant, flow, run, pitfall, vocabulary, **repository** ("which
   repos must change together with this one?"), **data** ("which tables hold X
   and how are they related?"). Include at least one of the last two.
2. Launch a `general-purpose` subagent that may **only read the docs folder**,
   never code; it answers citing the paths/symbols the docs give, or "the
   docs don't say". No hints.
3. Launch an `Explore` subagent to grade each answer against the real code.
4. Score: correct 1 / incomplete 0.5 / wrong or not found 0. Each miss is a
   hole: fix it in the document that should have answered it (usually the
   map, a module card or the recipes). Wrong answers first — the docs say
   something false.
5. Threshold **≥ 8/10**. If not reached, fix and repeat once with new
   questions. Record the score in `_meta/documake.json` (`newcomer_test`), which
   is not published — **never in a page a reader opens**, and not in the coverage
   file either, whose job is the open gaps. Questions go in the docs' language.
6. **Run the tutorial.** Write `00-primer-cambio` now and execute every step as
   you write it, in the repo as it stands. A step that fails gets fixed or
   dropped; the "Check it works" output is the one you actually saw, pasted. If
   the repo cannot be run here (no credentials, needs a GPU or external
   service), set `"tutorial": false` in `documake.json` and put the reason in
   coverage — never ship steps you did not run.

## Step 7 — `--update` mode

1. Read `documake.json` → source commit and language.
2. `git diff --stat <commit>..HEAD`, `git log --oneline <commit>..HEAD`.
3. Map each changed file to its area via `documake.json`; new files outside
   every area → possible new area: ask.
4. Re-run Step 3 **only** for affected areas/flows; rewrite their cards and
   whatever references them (map, recipes, flows). Re-run `repo.py` and
   `schema.py` and refresh `01`, `02` and `06` if manifests, CI, compose,
   migrations or models changed (the checker flags drift anyway).
5. Always run Step 5 in full (renamed paths break untouched docs). Step 6
   with 5 questions about what changed.
6. Update `documake.json` with the new commit.

## Step 8 — Close

1. Write `documake.json`: `{"language": "es", "database": true, "tutorial": true,
   "commit": "<sha>", "date": "<ISO>", "areas": {"<area>": ["paths"]}, "flows": [...],
   "newcomer_test": "9/10", "skill_version": 4}`. `"tutorial": false` only with the
   reason in coverage (Step 6.6).
2. Report to the user **in the user's language**, briefly: what was generated
   (README path), check and newcomer-test results, the top 3 ASK markers (only
   a person can answer them), weak areas.
3. **No commit** unless asked. If asked: `docs: documentación del sistema
   generada con documake` (in the docs' language), no `Co-Authored-By`, never push.

## Step 9 — Publish (only with `--publish`)

**Load [references/deploy.md](references/deploy.md) now** — it holds everything
about targets, forges (GitHub, GitLab.com, GitLab self-hosted), Docker/nginx,
wiki sync and gotchas; it is not loaded before this step.

1. Ask nothing about the target if `--publish` named one; otherwise use the
   pick table in `deploy.md`.
2. Run `publish.py <docs-dir> --target <t>` (add `--base /<repo>/` for
   VitePress under a sub-path). If `mkdocs-documake.yml`, `.vitepress/` or
   `.documake/` exist and weren't generated by documake, stop and ask.
3. Build once locally if the tool is installed and report warnings.
4. Report: files written, which one to copy/merge and where, one-time setup
   (Pages source, `WIKI_TOKEN`, DNS), the expected URL, and that CI files were
   only validated as YAML.
5. `gitlab-wiki` is **hybrid**: it only writes a reserved section of the wiki
   and never touches the people-owned pages (spikes, project syncs, notes;
   design decisions stay in the repo). Walk the user through the one-time
   GitLab setup checklist in `deploy.md` (wiki enabled, `WIKI_TOKEN`, SSL/CA,
   runner) and tell them to link the section from their wiki home. On abort
   (exit 2: hand-edited page inside the section) show the list; never `--force`
   without their OK.
6. Wikis: **ask before pushing**; pushing publishes.

## Never

- Invent paths, symbols, flags, env vars or commands.
- Copy secrets or credentials into docs.
- Trust existing docs without `--audit`.
- Put line numbers in docs.
- Read the whole codebase in your own window.
- Overwrite docs documake didn't generate.
- Finish a module card without "How to modify it" and "Who uses it".
- Write the same procedure in a card, a flow and the recipes: it lives in the recipes.
- Ship a tutorial whose steps you did not run.
- Leave a cross-reference as prose, or label a link with the name of its folder.
- Put audit notes, scores or subagent talk in the docs instead of `_meta/`.
- Close without a CLEAN check and the newcomer test.
- Tell the project's history as history.
- Mix languages in one docs folder, or translate code identifiers.
- Push a wiki, or edit `.github/workflows/` / `.gitlab-ci.yml`, without the user's explicit OK.
