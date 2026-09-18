# harness-toolbox

Practical tools for working with AI coding agents. Built for
[Claude Code](https://code.claude.com); the skills follow the open
[Agent Skills](https://agentskills.io) format, so they also load in OpenCode.

## Skills

| Skill | What it does |
|---|---|
| [`documake`](skills/documake/SKILL.md) | Explores a whole repository with parallel subagents and writes `docs/` for a developer who has to **modify** the system, not for the one who built it. |

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

## Repository layout

```
skills/documake/
├── SKILL.md         instructions and frontmatter
├── references/      loaded on demand (templates, checks, languages, deploy)
└── scripts/         deterministic helpers: scan, schema, check, publish
```

## License

[MIT](LICENSE)
