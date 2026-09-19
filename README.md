# harness-toolbox

Practical tools for working with AI coding agents. Built for
[Claude Code](https://code.claude.com); the skills follow the open
[Agent Skills](https://agentskills.io) format, so they also load in OpenCode.

## Skills

| Skill | What it does |
|---|---|
| [`documake`](skills/documake/SKILL.md) | Explores a whole repository with parallel subagents and writes `docs/` for a developer who has to **modify** the system, not for the one who built it. |
| [`context-optimizer`](skills/context-optimizer/SKILL.md) | Measures what every skill, agent, MCP server, CLAUDE.md and hook costs at startup, finds what overlaps or is never invoked, and applies the cuts you approve with a backup and a reversal manifest. |

## documake

Most project docs are written by the builder ("first we did X, then migrated
to Y"). documake writes for whoever has to change the code: how the system is
*now*, what gets touched together, and what reading the code does not reveal.

What it generates, in reading order:

- stack, repository kind (monorepo or not) and links to other repos
- an abbreviated directory tree with real paths
- a code map ("I want to change X → start at Y"), architecture, core
  database tables with their relations
- end-to-end flows, one card per module, change recipes, pitfalls, glossary

How it stays honest:

- every claim carries a path and symbol you can check;
- `scripts/check_docs.py` verifies that paths, symbols, tables and foreign keys
  exist;
- a zero-context reader agent tests whether a newcomer can actually find
  their way around.

### Requirements

- Claude Code (or another agent that supports Agent Skills)
- Python 3.12+ and git. The scripts use only the standard library.

### Install

```bash
git clone https://github.com/reiarseni/harness-toolbox.git
mkdir -p ~/.claude/skills
cp -r harness-toolbox/skills/documake ~/.claude/skills/
```

Use `.claude/skills/` inside a repo instead to share the skill with your team.
For OpenCode, copy it to `~/.config/opencode/skills/`.

### Usage

From the root of the repository you want to document:

```
/documake
/documake --lang en
/documake --update
/documake --only billing
/documake --publish vitepress
```

| Argument | Effect |
|---|---|
| `[repo path]` | Document that repo instead of the current directory. |
| `--lang <code>` | Output language. Default `es`; `en` built in, others via `references/languages.md`. |
| `--update` | Incremental mode: refresh only what changed since the last run. |
| `--only <module>` | Redo one module card and what references it. |
| `--publish <target>` | After the docs pass verification, publish them: `zensical`, `vitepress`, `docsify`, `gitlab-wiki` or `github-wiki`. |

documake never runs on its own: the skill sets `disable-model-invocation`, so
Claude only runs it when you type `/documake`. The only checkpoint with you is the coverage plan, before the deep exploration.

Publishing details (GitHub Pages, GitLab Pages, wikis, CI templates) are in
[`references/deploy.md`](skills/documake/references/deploy.md).

## context-optimizer

Every session starts with skills, agents, MCP servers, instruction files and
hooks already loaded. Most audits count bytes in one skills directory and stop
there. context-optimizer covers every source, and asks a different question:
not only what each entry costs, but whether it earns its place.

What it reports:

- **Cost** per entry and per source, each figure labelled *measured* or
  *estimated* — and the fixed floor that no change can reduce, stated up front
  so the remaining number is read correctly.
- **Overlap**: entries that duplicate each other, especially across levels,
  where a user skill pays rent for something already built in.
- **Usage**: real invocations, not mentions. A bare search for a skill called
  `do` returns every `/doctor` and `/docs` in your history.
- **Coverage**: the project stack, detected at the root *and* one level down so
  a monorepo is not mistaken for an empty project, against what your entries
  actually cover.

How it stays safe:

- a timestamped backup and a reversal manifest holding the exact undo command
  for every change, written before the first edit;
- CLAUDE.md content, hooks and MCP configuration are measured and proposed on,
  never edited;
- a tracked file in a dirty repository is reported, never touched — no stash,
  no branch;
- a mechanism not yet confirmed on your machine touches one entry, then stops
  until you confirm a real saving.

### Requirements

- Claude Code (or another agent that supports Agent Skills)
- Python 3.12+ and git. The scripts use only the standard library.

### Install

```bash
git clone https://github.com/reiarseni/harness-toolbox.git
mkdir -p ~/.claude/skills
cp -r harness-toolbox/skills/context-optimizer ~/.claude/skills/
```

### Usage

```
/context-optimizer
```

Like `documake`, this skill sets `disable-model-invocation`, so it costs
nothing until you type its name. It asks you once to paste your context and
usage breakdowns: with them every figure is measured, without them every figure
is an estimate and is labelled as one.

Each script carries a `--self-test` that runs recorded fixtures covering every
refusal path, and `tests/pressure-scenarios.md` documents the behaviour the
guardrails are there to force.

## Repository layout

```
skills/documake/
├── SKILL.md         instructions and frontmatter
├── references/      loaded on demand (templates, checks, languages, deploy)
└── scripts/         deterministic helpers: scan, schema, check, publish

skills/context-optimizer/
├── SKILL.md         procedure and guardrails
├── references/      loaded on demand (measurement, portfolio, mechanisms, safety)
├── scripts/         inventory, measured, portfolio, remediate — each with --self-test
└── tests/           pressure scenarios for the guardrails
```

## License

[MIT](LICENSE)
