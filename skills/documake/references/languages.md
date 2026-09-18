# Languages

Default output: **Spanish from Spain (`es`)**. The user picks another with
`--lang <code>` or by asking in natural language.

Changes with language: file names, headings, prose, diagram labels, gap
markers. **Never** changes: code identifiers (paths, symbols, commands, env
vars, tables), the `_meta/` folder and `_meta/documake.json` (its keys stay English).

## Built-in languages (checker knows them)

### Files (reading order = numbering)

| # | Role | `es` (default) | `en` |
|---|---|---|---|
| — | Front door | `README.md` | `README.md` |
| 01 | Stack + repository (kind, organization, other repos) | `01-stack-y-repositorio.md` | `01-stack-and-repository.md` |
| 02 | Abbreviated structure (tree) | `02-estructura.md` | `02-structure.md` |
| 03 | Overview | `03-vision-general.md` | `03-overview.md` |
| 04 | Code map | `04-mapa-del-codigo.md` | `04-code-map.md` |
| 05 | Architecture | `05-arquitectura.md` | `05-architecture.md` |
| 06 | Data: core tables and relations (if a database exists) | `06-datos.md` | `06-data.md` |
| 07 | Flows (dir) | `07-flujos/` | `07-flows/` |
| 08 | Module cards (dir) | `08-modulos/` | `08-modules/` |
| 09 | Change recipes | `09-recetas-de-cambio.md` | `09-change-recipes.md` |
| 10 | Environment and testing | `10-entorno-y-pruebas.md` | `10-environment-and-testing.md` |
| 11 | Integrations (if external systems) | `11-integraciones.md` | `11-integrations.md` |
| 12 | Pitfalls and risks | `12-trampas-y-riesgos.md` | `12-pitfalls-and-risks.md` |
| 13 | Operations and data: Docker, data outside git, backups, logs (when there is evidence) | `13-operacion-y-datos.md` | `13-operations-and-data.md` |
| — | Glossary | `glosario.md` | `glossary.md` |
| — | Coverage | `_meta/cobertura.md` | `_meta/coverage.md` |
| — | Subfolder when the docs folder is taken | `sistema/` | `system/` |

### Required headings (exact; the checker enforces them)

**Module card** (`08-…/<area>.md`)

| `es` | `en` |
|---|---|
| `## Responsabilidad` | `## Responsibility` |
| `## Quién lo usa` | `## Who uses it` |
| `## Invariantes` | `## Invariants` |
| `## Cómo modificarlo` | `## How to modify it` |
| `## Cómo probarlo` | `## How to test it` |

**Stack + repository** (`01-…`)

| `es` | `en` |
|---|---|
| `## Stack` | `## Stack` |
| `## Tipo de repositorio` | `## Repository type` |
| `## Organización del repositorio` | `## Repository organization` |
| `## Relación con otros repositorios` | `## Relationship with other repositories` |

**Data** (`06-…`)

| `es` | `en` |
|---|---|
| `## Mapa de relaciones` | `## Relationship map` |
| `## Resto de tablas` | `## Other tables` |

Other headings are translated naturally (`es`: Piezas principales, Cómo
funciona, De qué depende, Estado y configuración, Trampas, Grupos de tablas;
flows: Paso a paso, Cuando algo falla, Efectos secundarios, Para cambiar este
flujo; code map: "Quiero cambiar… → empieza por…").

**Operations** (`13-…`, required when the repo shows evidence)

| `es` | `en` |
|---|---|
| `## Docker` | `## Docker` |
| `## Datos fuera de git` | `## Data outside git` |
| `## Backups` | `## Backups` |
| `## Logs y observabilidad` | `## Logs and observability` |

**Other checked headings:** README `## Ruta feliz (Quickstart)` / `## Quickstart`
(1–5 numbered steps); in `10-…` `## Configuración` / `## Configuration`.

### Markers

| Meaning | `es` | `en` |
|---|---|---|
| More reading would answer it | `[PENDIENTE: …]` | `[TODO: …]` |
| Only a person knows | `[PREGUNTAR: …]` | `[ASK: …]` |
| Couldn't verify | `[NO VERIFICADO]` | `[UNVERIFIED]` |

### Tone

- `es`: Spanish from Spain, impersonal ("se configura en…", "para añadir un
  campo, toca…"). No voseo, no "usted".
- `en`: impersonal or imperative ("configured in…", "to add a field, edit…"). No "we".

## Any other language

Any code works (`pt`, `fr`, `de`, `ca`, `it`…). Translate the file names and
required headings yourself and declare them in `documake.json` so the checker
knows what to require:

```json
{
  "language": "pt",
  "structure": {
    "required": [
      "README.md", "01-stack-e-repositorio.md", "02-estrutura.md", "03-visao-geral.md",
      "04-mapa-do-codigo.md", "05-arquitetura.md", "09-receitas-de-mudanca.md",
      "10-ambiente-e-testes.md", "12-armadilhas-e-riscos.md", "glossario.md", "_meta/cobertura.md"
    ],
    "modules_dir": "08-modulos/",
    "flows_dir": "07-fluxos/",
    "module_sections": ["Responsabilidade", "Quem o usa", "Invariantes", "Como modificá-lo", "Como testá-lo"],
    "stack_doc": "01-stack-e-repositorio.md",
    "stack_sections": ["Stack", "Tipo de repositório", "Organização do repositório", "Relação com outros repositórios"],
    "structure_doc": "02-estrutura.md",
    "data_doc": "06-dados.md",
    "data_sections": ["Mapa de relações", "Demais tabelas"],
    "logical_words": ["lógica", "M:N", "via"],
    "markers": ["PENDENTE", "PERGUNTAR", "NÃO VERIFICADO"],
    "phrases": ["\\bdecidimos\\b", "\\binicialmente\\b", "\\bnosso\\b"],
    "readme_doc": "README.md", "quickstart_section": "Caminho feliz (Quickstart)",
    "recipes_doc": "09-receitas-de-mudanca.md",
    "env_doc": "10-ambiente-e-testes.md", "config_section": "Configuração",
    "ops_doc": "13-operacao-e-dados.md",
    "ops_sections": ["Docker", "Dados fora do git", "Backups", "Logs e observabilidade"]
  }
}
```

The first four keys are mandatory. `readme_doc`+`quickstart_section`, `recipes_doc`, `env_doc`+`config_section` and `ops_doc`+`ops_sections` are optional: each pair turns on its check (quickstart, working directories, configuration, operations). `stack_*`, `structure_doc` and `data_*` turn
on the forced-section checks; declare them (the skill relies on them).
Naming rules: keep the `01-`…`12-` prefixes (they order reading); lowercase,
no accents, hyphens; keep names stable across regenerations. `phrases` is
optional — without it the checker skips builder-perspective linting (and
warns); then review perspective by hand per `constitution.md`.
