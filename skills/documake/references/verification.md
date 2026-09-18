# Verification

Two layers. The script checks the docs are **true**; the newcomer test checks
they are **useful**. Both are needed: a doc can be entirely true and useless
for modifying anything.

## Layer 1 — check_docs.py

```bash
python3 $DOCUMAKE/scripts/check_docs.py <root> --docs <dir> [--lang <code>] [--mermaid] [--json]
```

| Check | Level | Fix |
|---|---|---|
| Backticked path that doesn't exist | ERROR | Fix or remove the path |
| Symbol not found in code | WARN | Find the real name; if generated, keep and note it |
| Broken internal `.md` link | ERROR | Fix |
| Empty mermaid block / no diagram type | ERROR | Fix |
| Mermaid doesn't compile (`--mermaid` + `mmdc`) | ERROR | Fix |
| Diagram > 12 nodes | WARN | Split |
| Line-number reference (`file.py:123`, `#L123`) | ERROR | Replace with symbol |
| Module card missing a required section | ERROR | Complete it |
| Builder-perspective phrase | WARN | Rewrite per `constitution.md` |
| TODO / ASK markers | INFO | Carry to the coverage file |
| Required document missing | ERROR | Write it |
| `01`/`06` required section missing or empty | ERROR | Complete it (state the finding, or what was searched) |
| Tree path does not exist / tree > 70 lines | ERROR / WARN | Fix the path / abbreviate |
| ER entity not a table in the code; relation without FK | WARN | Fix the name; label a non-FK link "logical" |
| Tables detected but `06` missing | ERROR | Write it, or `"database": false` with a reason |
| Detected link to another repo not mentioned in `01` | WARN | Add the row |
| Link leaves the docs folder | WARN | Cite the file as a backticked path |

Exit 1 on errors. Never close with errors.

`--audit FILE...` (Step 1) runs only the path check over **pre-existing**
docs, resolving paths from the repo root, the doc's folder, or any suffix
match, and ignoring dependency names found in manifests. Each `[DRIFT]` is a
stale reference; always exits 0.

## Layer 2 — The newcomer test

### Question types (mix at least four)

| Type | Example | Doc that should answer |
|---|---|---|
| Locate | "Where is an invoice's VAT computed?" | Code map (task → code table) |
| Impact | "If `Order.total`'s signature changes, what else must change?" | Module card (Who uses it) |
| Recipe | "Which files do you touch to add an endpoint?" | Change recipes |
| Invariant | "Can an HTTP handler write to the DB directly?" | Architecture (invariants) |
| Flow | "What happens, in order, when a payment webhook arrives?" | Flows |
| Run | "How do you run only the billing tests?" | Environment and testing |
| Pitfall | "Why can't `legacy_id` be renamed?" | Pitfalls and risks |
| Vocabulary | "Difference between an *order* and a *shipment order*?" | Glossary |
| Repository | "Is this a monorepo? Which other repos must change together with it?" | Stack and repository |
| Data | "Which tables hold a camera's inspection history and how are they related?" | Data (relationship map) |

Questions must be about **this repo**, concrete, and checkable in code.
"How is the project organized?" doesn't count. Write them in the docs' language.

### Reader prompt (general-purpose subagent)

```
You are a developer who just joined this project and must modify it. You may
ONLY read the folder <docs-dir>. Do not open any other repo file — not with
Read, Grep, Glob or Bash.

Answer each question using only the documentation. For each give:
- The answer.
- The paths and symbols the documentation points you to.
- The document where you found it.
- If the docs don't say or are ambiguous, say exactly that: "The docs don't
  say" / "Ambiguous because…". Do not infer.

Questions:
1. …
```

### Grader prompt (Explore subagent)

```
Check each answer against the code in <root>. For each: CORRECT /
INCOMPLETE (what's missing) / WRONG (what's true, with path and symbol) /
NOT FOUND (reader didn't find it; give the real answer).

<questions and answers>
```

### Scoring

- CORRECT = 1, INCOMPLETE = 0.5, WRONG or NOT FOUND = 0. Threshold **≥ 8/10**.
- Each miss → fix the doc from the table above, not a patch anywhere.
- WRONG is worse than NOT FOUND: the docs say something false. Fix first.

### Record in the coverage file (in the docs' language)

```markdown
## Newcomer test — <date>, commit <sha>
Score: 8.5/10
| # | Type | Question | Result | Fix applied |
```
