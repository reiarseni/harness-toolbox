# Constitution and derived rules

> **P1.** A human can enter the project, read the documentation and understand
> where everything is and how it works.
>
> **P2.** The documentation explains the system from the perspective of a
> developer who needs to modify it, not from the perspective of the one who built it.

P1 is **what** the docs achieve: orientation (*where*) and understanding
(*how it works*). P2 is **where they're written from**: the reader arrives with
a change to make and none of the author's context.

## From P1 — "where everything is and how it works"

- **R1.1 One front door.** `README.md` says in under a screen what the system
  is and which doc to read for each goal.
- **R1.2 All code has an owner in the map.** Every dir with own code appears
  in the code map with its responsibility in 1–3 sentences; exclusions
  (generated, vendor) are explicit. *Test:* pick a random file; the map tells
  you its area.
- **R1.3 Task → code lookup.** The map has an "I want to change X → start at Y"
  table. Readers ask "where is the price computed?", not "what's in `core/`?".
- **R1.4 Name, don't link lines.** Backticked paths and symbols survive small
  refactors and are greppable. Never line numbers.
- **R1.5 "How it works" is a walk, not an inventory.** Flows are sequences:
  trigger → step → step → effect, each step with path and symbol.
- **R1.6 One diagram per relationship.** Who-calls-whom, who-depends-on-whom,
  state transitions → Mermaid. Lists stay lists. ≤ 12 nodes.
- **R1.7 Defined vocabulary.** Every domain/team term is in the glossary and
  defined on first use per doc. If code uses two names for one thing (or one
  for two), say it.
- **R1.8 Honest gaps.** TODO/ASK markers are part of the product. Docs that
  look complete and aren't do more harm than docs that admit gaps.
- **R1.9 Context before detail.** The first document says what the repo is
  made of (stack with versions), what kind of repo it is (monorepo, multi-app
  or single project), how it is organized and which other repositories it
  depends on or is coupled to. A modifier who doesn't know a change also
  belongs in another repo will break something. Every claim carries its
  evidence file; what cannot be seen from inside (who consumes this repo) is
  an explicit question, not silence.
- **R1.10 Structure is abbreviated.** One short tree (≤ 70 lines) of what a
  modifier navigates, every path real. The full detail lives in the code map.
- **R1.11 Data shows the core, not everything.** Draw only the tables a
  modifier must understand (≤ 12 per diagram, one diagram per group above ~15-20
  tables, hub tables named once); list the rest in a table. Every entity and
  relation is checked against the code: a made-up table is worse than a
  missing one.

## From P2 — "the modifier's perspective"

- **R2.1 Every section answers a modifier question:** Where is it? What
  happens if I touch it? What else must change with it? What must I not
  break? How do I check I didn't break anything? A section answering none is cut.
- **R2.2 Present, not chronicle.** Describe how the system **is**. History
  stays only if it explains something current, rewritten as a constraint:
  "`sessions.ttl` is kept because `legacy/importer.py` still reads it; it
  can't go without changing the importer."
- **R2.3 "Why" only as consequence.** "It's like this because X; change it
  and Y happens." Never justification or merit ("clean, scalable architecture").
- **R2.4 Pitfalls are first-class.** What the builder finds "obvious" is what
  the modifier needs: init order, side effects, caches, implicit
  dependencies, code that looks dead but isn't, stateful tests, misleading names.
- **R2.5 Invariants, especially of absence.** "Domain imports nothing from
  `web/`", "no handler writes to the DB directly". Invisible in code, first
  thing a newcomer breaks. Give the grep that proves it.
- **R2.6 Recipes list every touch point**, in order, with the test to run. A
  recipe missing a step is worse than none.
- **R2.7 Impersonal, no author jargon.** No we/I/our; no undefined internal
  abbreviations. The reader wasn't in the meetings.
- **R2.8 Explicit blast radius.** Every module card says who depends on it.

## Builder-perspective phrases

`check_docs.py` flags these (es/en lists built in). Not automatic errors;
each needs review.

| Tells (es / en) | Rewrite as |
|---|---|
| decidimos, optamos por, elegimos / we decided, we chose | "It's X because…; changing it means…" |
| inicialmente, al principio, originalmente / initially, at first, originally | The current constraint, or nothing |
| obviamente, simplemente, trivial / obviously, simply, just | Explain it, or drop the adverb |
| nuestro, nosotros / our, we, I | Impersonal |
| más adelante, en el futuro / eventually, in the future | Pitfalls doc, as a current risk |
| elegante, robusto, escalable / elegant, robust, scalable | The checkable fact, or nothing |

## Resolved tension: line citations vs names

Some popular skills (deepwiki-skill, codebase-analysis-skill) cite
`file:line`. Better for verifying at write time, worse to maintain: it breaks
on the first commit and readers stop trusting the whole doc. documake verifies
with `path + symbol` (the checker confirms the symbol exists), which lasts.
