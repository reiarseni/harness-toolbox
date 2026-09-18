#!/usr/bin/env python3
"""Publish documake docs. The docs folder stays the single source of truth.

Usage: publish.py <docs-dir> --target <target> [--repo ROOT] [--out WIKI_CLONE] [--base /] [--title T]

Targets (site generators write config; every target writes a deploy kit to <repo>/.documake/):
  zensical     <repo>/mkdocs-documake.yml (mkdocs.yml format). Build: zensical build -f mkdocs-documake.yml → site/
  vitepress    <docs>/.vitepress/config.mts (sidebar, local search, Mermaid).   Build: npx vitepress build <docs> → site/
  docsify      <docs>/index.html + _sidebar.md + .nojekyll. No build: serve the docs folder.
  gitlab-wiki  HYBRID sync into <project>.wiki.git: only a reserved section (default documentacion-del-proyecto/)
               is written; every page there carries a "generated, do not edit" banner; stale generated pages
               are removed; nothing outside the section is touched except a marked block in _sidebar.md.
               --out syncs a local clone; --dry-run shows the plan; --force overrides hand-edited pages.
  github-wiki  pages for <repo>.wiki.git, FLAT namespace (README→Home.md, a/b.md→a-b.md, _Sidebar.md). --out syncs.

Deploy kit (<repo>/.documake/): a copy of this script plus, per target,
  <target>.github-pages.yml  <target>.gitlab-ci.yml  <target>.Dockerfile    (site targets)
  <target>-sync.github.yml | <target>-sync.gitlab-ci.yml                     (wiki targets)
Stdlib only, so CI can run the copy with plain python3.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

DIR_LABELS = {"07-flujos": "Flujos", "08-modulos": "Módulos", "07-flows": "Flows",
              "08-modules": "Modules", "_meta": "Meta"}
LINK_RE = re.compile(r"(\]\()([^)\s]+)(\))")
SKIP = {"README.md", "index.html", "_sidebar.md", "_Sidebar.md"}


# ---------- docs model ----------

def title_of(path):
    try:
        for line in open(path, encoding="utf-8", errors="ignore"):
            if line.startswith("# "):
                return line[2:].strip()
    except OSError:
        pass
    return os.path.splitext(os.path.basename(path))[0].split("-", 1)[-1].replace("-", " ").capitalize()


def collect(docs):
    """Nav tree: README first, then files/dirs by name (numbered prefixes), _meta last."""
    entries = []
    for name in sorted(os.listdir(docs), key=lambda n: (n.startswith("_"), n)):
        full = os.path.join(docs, name)
        if name.startswith(".") or name in SKIP:
            continue
        if os.path.isdir(full):
            children = sorted(f for f in os.listdir(full) if f.endswith(".md"))
            if children:
                label = DIR_LABELS.get(name) or name.lstrip("_").split("-", 1)[-1].replace("-", " ").capitalize()
                entries.append((label, [(title_of(os.path.join(full, c)), f"{name}/{c}") for c in children]))
        elif name.endswith(".md"):
            entries.append((title_of(full), name))
    if os.path.isfile(os.path.join(docs, "README.md")):
        entries.insert(0, (title_of(os.path.join(docs, "README.md")), "README.md"))
    return entries


def all_md(docs):
    out = []
    for dp, dns, fns in os.walk(docs):
        dns[:] = [d for d in dns if not d.startswith(".")]
        out += [os.path.relpath(os.path.join(dp, f), docs) for f in fns if f.endswith(".md") and f not in SKIP - {"README.md"}]
    return sorted(out)


def rewrite_links(text, src_rel, mapping, flat=False):
    """Rewrite relative .md links whose target is in mapping (old rel → new rel). flat: bare page names."""
    src_dir = os.path.dirname(src_rel)

    def fix(m):
        target = m.group(2)
        if re.match(r"^[a-z]+:", target) or target.startswith("#"):
            return m.group(0)
        path, _, anchor = target.partition("#")
        resolved = os.path.normpath(os.path.join(src_dir, path))
        if resolved not in mapping:
            return m.group(0)
        if flat:
            new = mapping[resolved][:-3]
        else:
            new = os.path.relpath(mapping[resolved], os.path.dirname(mapping.get(src_rel, src_rel)) or ".")
        return m.group(1) + new + ("#" + anchor if anchor else "") + m.group(3)

    return LINK_RE.sub(fix, text)


def sidebar_md(entries, link, title):
    lines = [f"### {title}", ""]
    for label, target in entries:
        if isinstance(target, list):
            lines.append(f"- **{label}**")
            lines += [f"  - [{l}]({link(t)})" for l, t in target]
        else:
            lines.append(f"- [{label}]({link(target)})")
    return "\n".join(lines) + "\n"


def q(s):
    return json.dumps(s, ensure_ascii=False)


def write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write(body)
    return path


# ---------- wikis ----------

def sync_wiki(docs, out, rename, flat, sidebar_name, link):
    if not os.path.isdir(os.path.join(out, ".git")):
        sys.exit(f"--out {out} is not a git clone of the wiki repo")
    files = all_md(docs)
    mapping = {f: rename(f) for f in files}
    dup = {n for n in mapping.values() if list(mapping.values()).count(n) > 1}
    if dup:
        sys.exit(f"Renaming produces duplicate page names: {sorted(dup)}")
    for f in files:
        text = open(os.path.join(docs, f), encoding="utf-8").read()
        write(os.path.join(out, mapping[f]), rewrite_links(text, f, mapping, flat=flat))
    write(os.path.join(out, sidebar_name), sidebar_md(collect(docs), link, TITLE))
    print(f"Synced {len(files)} pages + {sidebar_name} into {out}")


SECTION_DEFAULT = {"es": "documentacion-del-proyecto", "en": "project-documentation"}
SIDEBAR_TITLE = {"es": "Documentación del proyecto", "en": "Project documentation"}
BANNER = {
    "es": "> ⚠️ **Página generada automáticamente** desde {src}. **No la edites aquí**: el próximo sync la "
          "sobrescribe. Cambia el fichero en el repositorio mediante un MR.",
    "en": "> ⚠️ **Auto-generated page** from {src}. **Do not edit here**: the next sync overwrites it. "
          "Change the file in the repository through a merge request.",
}
GEN = "<!-- documake:generated"
SB_START, SB_END = "<!-- documake:sidebar:start -->", "<!-- documake:sidebar:end -->"


def is_generated(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.readline().startswith(GEN)
    except (OSError, UnicodeDecodeError):
        return False


def gitlab_section(docs, docs_rel, out, section, source_url, sidebar_mode, dry, force):
    if not os.path.isdir(os.path.join(out, ".git")):
        sys.exit(f"--out {out} is not a git clone of the wiki repo")
    section = section.strip("/")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", section):
        sys.exit(f"--section must be one lowercase slug (a-z, 0-9, hyphens), got {section!r}")
    files = all_md(docs)
    mapping = {f: section + "/" + gitlab_rename(f) for f in files}
    new = {}
    for f in files:
        src = f"{docs_rel}/{f}"
        ref = f"[`{src}`]({source_url.rstrip('/')}/{src})" if source_url else f"`{src}`"
        head = f"{GEN} source={src} -->\n{BANNER.get(LANG, BANNER['en']).format(src=ref)}\n\n"
        new[mapping[f]] = head + rewrite_links(open(os.path.join(docs, f), encoding="utf-8").read(), f, mapping)

    base = os.path.join(out, section)
    existing = {}
    if os.path.isdir(base):
        for dp, _, fns in os.walk(base):
            for fn in fns:
                if fn.endswith(".md"):
                    full = os.path.join(dp, fn)
                    existing[os.path.relpath(full, out)] = full
    add, upd, same, delete, conflicts = [], [], [], [], []
    for rel, content in sorted(new.items()):
        if rel not in existing:
            add.append(rel)
        elif not is_generated(existing[rel]):
            conflicts.append(rel)
        elif open(existing[rel], encoding="utf-8").read() == content:
            same.append(rel)
        else:
            upd.append(rel)
    for rel, full in sorted(existing.items()):
        if rel not in new:
            (delete if is_generated(full) else conflicts).append(rel)
    if conflicts and not force:
        print(f"ABORTED: {len(conflicts)} page(s) inside the reserved section '{section}/' were NOT generated "
              "by documake (hand-edited or hand-created):", file=sys.stderr)
        for c in conflicts:
            print(f"  - {c}", file=sys.stderr)
        print("Move them out of the section (the rest of the wiki is yours), or re-run with --force to "
              "overwrite/delete them.", file=sys.stderr)
        sys.exit(2)

    forced = list(conflicts)
    for c in forced:                      # --force: hand-made pages in the section are replaced/removed
        (upd if c in new else delete).append(c)
    if not dry:
        for rel in add + upd:
            write(os.path.join(out, rel), new[rel])
        for rel in delete:
            os.remove(existing[rel])
        for dp, dns, fns in sorted(os.walk(base), reverse=True):
            if os.path.isdir(dp) and not os.listdir(dp):
                os.rmdir(dp)

    sb_state = "skipped"
    if sidebar_mode == "merge":
        block = (SB_START + "\n" + sidebar_md(collect(docs), lambda r: "/" + mapping[r][:-3],
                                              SIDEBAR_TITLE.get(LANG, SIDEBAR_TITLE["en"])) + SB_END + "\n")
        sb = os.path.join(out, "_sidebar.md")
        cur = open(sb, encoding="utf-8").read() if os.path.isfile(sb) else None
        if cur is None:
            merged, sb_state = block, "created"
        elif SB_START in cur and SB_END in cur:
            merged = re.sub(re.escape(SB_START) + r".*?" + re.escape(SB_END) + r"\n?", lambda m: block, cur, flags=re.S)
            sb_state = "block updated" if merged != cur else "block unchanged"
        else:
            merged, sb_state = cur.rstrip("\n") + "\n\n" + block, "block appended (rest untouched)"
        if not dry and merged != cur:
            write(sb, merged)

    tag = "DRY-RUN " if dry else ""
    print(f"{tag}wiki section '{section}/': +{len(add)} new, ~{len(upd)} updated, ={len(same)} unchanged, "
          f"-{len(delete)} removed; _sidebar.md: {sb_state}"
          + (f"; FORCED over {len(forced)} hand-made page(s)" if forced else ""))
    for label, items in (("new", add), ("updated", upd), ("removed", delete)):
        for rel in items:
            print(f"  {label}: {rel}")
    home = os.path.join(out, "home.md")
    if os.path.isfile(home) and section not in open(home, encoding="utf-8").read():
        print(f"hint: your wiki home.md doesn't link to the section; add [Documentación del proyecto](/{section}/home)")


def gitlab_rename(rel):
    rel = "home.md" if rel == "README.md" else rel
    return "meta/" + rel[len("_meta/"):] if rel.startswith("_meta/") else rel


def github_rename(rel):
    return "Home.md" if rel == "README.md" else rel.lstrip("_").replace("/", "-")


# ---------- site generators ----------

def zensical(docs, docs_rel):
    nav = []
    for label, target in collect(docs):
        if isinstance(target, list):
            nav.append(f"  - {q(label)}:")
            nav += [f"      - {q(l)}: {q(t)}" for l, t in target]
        else:
            nav.append(f"  - {q(label)}: {q(target)}")
    cfg = f"""# Generated by documake publish.py — regenerate, don't edit.
# Build: pip install zensical && zensical build -f mkdocs-documake.yml   → site/
site_name: {q(TITLE)}
docs_dir: {q(docs_rel)}
site_dir: site
exclude_docs: |
  _meta/documake.json
  index.html
  _sidebar.md
theme:
  name: material
  language: {LANG}
  features: [navigation.sections, navigation.top, search.highlight, content.code.copy]
  palette:
    - media: "(prefers-color-scheme: light)"
      scheme: default
      toggle: {{icon: material/brightness-7, name: "Dark mode"}}
    - media: "(prefers-color-scheme: dark)"
      scheme: slate
      toggle: {{icon: material/brightness-4, name: "Light mode"}}
markdown_extensions:
  - admonition
  - tables
  - toc:
      permalink: true
  - pymdownx.superfences:
      custom_fences:
        - name: mermaid
          class: mermaid
          format: !!python/name:pymdownx.superfences.fence_code_format
nav:
""" + "\n".join(nav) + "\n"
    return [write(os.path.join(REPO, "mkdocs-documake.yml"), cfg)]


def vitepress(docs, docs_rel):
    def link(rel):
        return "/" if rel == "README.md" else "/" + rel[:-3]

    items = []
    for label, target in collect(docs):
        if isinstance(target, list):
            kids = ", ".join(f"{{ text: {q(l)}, link: {q(link(t))} }}" for l, t in target)
            items.append(f"      {{ text: {q(label)}, collapsed: false, items: [{kids}] }}")
        else:
            items.append(f"      {{ text: {q(label)}, link: {q(link(target))} }}")
    out_dir = os.path.relpath(os.path.join(REPO, "site"), docs)
    lang = {"es": "es-ES", "en": "en-US"}.get(LANG, LANG)
    cfg = f"""// Generated by documake publish.py — regenerate, don't edit.
// Build (repo root): npm i --no-save vitepress@1 vitepress-plugin-mermaid@2 mermaid@11
//                    npx vitepress build {docs_rel}   → site/
import {{ defineConfig }} from 'vitepress'
import {{ withMermaid }} from 'vitepress-plugin-mermaid'

export default withMermaid(defineConfig({{
  lang: {q(lang)},
  title: {q(TITLE)},
  base: {q(BASE)},
  outDir: {q(out_dir)},
  rewrites: {{ 'README.md': 'index.md' }},
  srcExclude: ['_sidebar.md'],
  lastUpdated: false,
  markdown: {{
    // rewrites don't apply to links inside pages: point README.md links at index.md
    config: (md) => {{
      md.core.ruler.push('documake-readme-links', (state) => {{
        for (const block of state.tokens) for (const t of block.children || [])
          if (t.type === 'link_open') {{
            const href = t.attrGet('href')
            if (href && !/^[a-z]+:/i.test(href)) t.attrSet('href', href.replace(/(^|\\/)README\\.md(?=#|$)/, '$1index.md'))
          }}
      }})
    }}
  }},
  themeConfig: {{
    search: {{ provider: 'local' }},
    outline: {{ level: [2, 3] }},
    sidebar: [
{",\n".join(items)}
    ]
  }}
}}))
"""
    return [write(os.path.join(docs, ".vitepress", "config.mts"), cfg)]


DOCSIFY_HTML = """<!DOCTYPE html>
<html lang="{lang}">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/docsify@4/lib/themes/vue.css">
</head>
<body>
  <div id="app"></div>
  <script>
    window.$docsify = {{
      name: {title_js},
      loadSidebar: true,
      subMaxLevel: 2,
      relativePath: true,
      search: {{ placeholder: 'Buscar', noData: 'Sin resultados' }}
    }};
  </script>
  <script src="https://cdn.jsdelivr.net/npm/docsify@4"></script>
  <script src="https://cdn.jsdelivr.net/npm/docsify@4/lib/plugins/search.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
  <script>mermaid.initialize({{ startOnLoad: false }});</script>
  <script src="https://cdn.jsdelivr.net/npm/docsify-mermaid@2/dist/docsify-mermaid.js"></script>
</body>
</html>
"""


def docsify(docs, docs_rel):
    html = DOCSIFY_HTML.format(lang=LANG, title=TITLE, title_js=q(TITLE))
    open(os.path.join(docs, ".nojekyll"), "w").close()
    return [write(os.path.join(docs, "index.html"), html),
            write(os.path.join(docs, "_sidebar.md"), sidebar_md(collect(docs), lambda r: r, TITLE))]


# ---------- deploy kits ----------

BUILD = {
    # target: (setup steps for GitHub Actions, build command, output dir, Docker build stage)
    "zensical": (
        "      - uses: actions/setup-python@v7\n        with:\n          python-version: \"3.12\"\n"
        "      - run: pip install zensical",
        "zensical build -f mkdocs-documake.yml", "site",
        "FROM python:3.12-slim AS build\nWORKDIR /src\nRUN pip install --no-cache-dir zensical\n"
        "COPY mkdocs-documake.yml ./\nCOPY DOCS ./DOCS\nRUN zensical build -f mkdocs-documake.yml\n"),
    "vitepress": (
        "      - uses: actions/setup-node@v7\n        with:\n          node-version: 22\n"
        "      - run: npm i --no-save vitepress@1 vitepress-plugin-mermaid@2 mermaid@11",
        "npx vitepress build DOCS", "site",
        "FROM node:22-alpine AS build\nWORKDIR /src\n"
        "RUN npm i --no-save vitepress@1 vitepress-plugin-mermaid@2 mermaid@11\n"
        "COPY DOCS ./DOCS\nRUN npx vitepress build DOCS\n"),
    "docsify": ("", "", "DOCS", ""),
}
GITLAB_IMAGE = {"zensical": "python:3.12-slim", "vitepress": "node:22-alpine", "docsify": "alpine:3.20"}
GITLAB_SETUP = {"zensical": "pip install --quiet zensical",
                "vitepress": "npm i --no-save vitepress@1 vitepress-plugin-mermaid@2 mermaid@11",
                "docsify": "echo docsify needs no build step"}


def site_kit(target, docs_rel):
    setup, cmd, out, stage = BUILD[target]
    out = out.replace("DOCS", docs_rel)
    build_steps = (setup + "\n      - run: " + cmd.replace("DOCS", docs_rel) + "\n") if cmd else ""
    gh = f"""# GitHub Pages ({target}). Copy to .github/workflows/docs.yml.
# Once: Settings → Pages → Source: "GitHub Actions". Custom domain: Settings → Pages → Custom domain
# (+ DNS CNAME to <user>.github.io); keep base "/". Project URL <user>.github.io/<repo>/ needs --base /<repo>/.
name: docs
on:
  push:
    branches: [main]
    paths: ["{docs_rel}/**", ".documake/**", "mkdocs-documake.yml"]
  workflow_dispatch:
permissions:
  contents: read
  pages: write
  id-token: write
concurrency:
  group: pages
  cancel-in-progress: false
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
{build_steps}      - uses: actions/upload-pages-artifact@v5
        with:
          path: {out}
  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{{{ steps.deployment.outputs.page_url }}}}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v5
"""
    gl = f"""# GitLab Pages ({target}): gitlab.com, or self-hosted ONLY if the admin enabled Pages
# (own domain + wildcard DNS). Merge this job into .gitlab-ci.yml.
pages:
  image: {GITLAB_IMAGE[target]}
  script:
    - {GITLAB_SETUP[target]}
    - {cmd.replace("DOCS", docs_rel) if cmd else "echo serving the docs folder as is"}
    - rm -rf public && cp -r {out} public
  artifacts:
    paths: [public]
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
      changes: ["{docs_rel}/**/*", ".documake/**/*", "mkdocs-documake.yml"]
"""
    stage = stage.replace("DOCS", docs_rel)
    copy_from = f"COPY --from=build /src/{out} /usr/share/nginx/html" if stage else f"COPY {docs_rel} /usr/share/nginx/html"
    dk = f"""# Docs as a static nginx image ({target}). Works on ANY server: VPS, internal network,
# GitLab self-hosted without Pages. From the repo root:
#   docker build -f .documake/{target}.Dockerfile -t app-docs .
#   docker run -d -p 8088:80 app-docs      (put your TLS reverse proxy in front)
{stage}
FROM nginx:alpine
{copy_from}
"""
    return [write(os.path.join(KIT, f"{target}.github-pages.yml"), gh),
            write(os.path.join(KIT, f"{target}.gitlab-ci.yml"), gl),
            write(os.path.join(KIT, f"{target}.Dockerfile"), dk.replace("\n\n\n", "\n\n"))]


def wiki_kit(target, docs_rel):
    if target == "gitlab-wiki":
        body = f"""# Sync docs → RESERVED SECTION of the project wiki after each merge (GitLab.com or self-hosted).
# The wiki is a separate repo (<project>.wiki.git) served on your GitLab domain with its SSL and the
# project's permissions. Only the section is written; the rest of the wiki is never touched.
# One-time setup (see references/deploy.md): wiki enabled + first page created; project access token
# (Developer+, scope write_repository) stored as masked CI variable WIKI_TOKEN. Merge into .gitlab-ci.yml.
wiki-sync:
  image: alpine:3.20
  interruptible: false      # never cancel a half-done push (many projects set default interruptible: true)
  resource_group: wiki-sync
  before_script:
    - apk add --no-cache git python3 ca-certificates
    - '[ -n "$CI_SERVER_TLS_CA_FILE" ] && export GIT_SSL_CAINFO="$CI_SERVER_TLS_CA_FILE" || true'
  script:
    - WIKI_URL="$(echo "$CI_PROJECT_URL" | sed "s#://#://oauth2:${{WIKI_TOKEN}}@#").wiki.git"
    - git clone --quiet "$WIKI_URL" /tmp/wiki
    - python3 .documake/publish.py {docs_rel} --target gitlab-wiki --out /tmp/wiki --repo . --source-url "${{CI_PROJECT_URL}}/-/blob/${{CI_DEFAULT_BRANCH}}"
    - cd /tmp/wiki && git add -A
    - |
      if git diff --cached --quiet; then echo "wiki already up to date"; exit 0; fi
    - 'git -c user.name=docs-bot -c user.email="docs-bot@${{CI_SERVER_HOST}}" commit -q -m "docs: sync from ${{CI_COMMIT_SHORT_SHA}}"'
    - 'git push --quiet origin HEAD || (git pull --rebase --quiet origin "$(git rev-parse --abbrev-ref HEAD)" && git push --quiet origin HEAD)'
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
      changes: ["{docs_rel}/**/*"]
"""
        return [write(os.path.join(KIT, "gitlab-wiki-sync.gitlab-ci.yml"), body)]
    body = f"""# Sync docs → GitHub wiki after each push to main. Copy to .github/workflows/wiki.yml.
# Once: enable the wiki and create any first page in the UI (the wiki repo doesn't exist before).
name: wiki
on:
  push:
    branches: [main]
    paths: ["{docs_rel}/**"]
  workflow_dispatch:
permissions:
  contents: write
jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/checkout@v7
        with:
          repository: ${{{{ github.repository }}}}.wiki
          path: wiki
      - run: python3 .documake/publish.py {docs_rel} --target github-wiki --out wiki --repo .
      - run: |
          cd wiki && git add -A
          git -c user.name=docs-bot -c user.email=docs-bot@users.noreply.github.com commit -m "docs: sync from ${{{{ github.sha }}}}" || exit 0
          git push
"""
    return [write(os.path.join(KIT, "github-wiki-sync.github.yml"), body)]


# ---------- lint of generated GitLab CI ----------

YAML_SCALARS = {"true", "false", "yes", "no", "on", "off", "null", "~"}


def lint_gitlab_ci(path):
    """Stdlib check: every unquoted line under script:/before_script:/after_script: must be a YAML *string*.
    `- docs: sync` parses as a map and `- true` as a boolean; GitLab rejects both."""
    problems, key_indent = [], None
    for n, raw in enumerate(open(path, encoding="utf-8"), 1):
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        m = re.match(r"\s*(before_script|script|after_script):\s*$", line)
        if m:
            key_indent = indent
            continue
        if key_indent is not None:
            if indent <= key_indent and not line.lstrip().startswith("- "):
                key_indent = None
                continue
            item = re.match(r"\s*- (.*)$", line)
            if item and indent >= key_indent:
                v = item.group(1).strip()
                if v[:1] in "'\"|>":
                    continue
                if v.lower() in YAML_SCALARS or re.fullmatch(r"-?\d+(\.\d+)?", v):
                    problems.append(f"{path}:{n}: '{v}' parses as a non-string; quote it")
                elif re.search(r":(\s|$)", v):
                    problems.append(f"{path}:{n}: unquoted ': ' makes YAML parse a map; wrap the line in single quotes")
    return problems


# ---------- main ----------

def repo_root(docs, given):
    if given:
        return os.path.abspath(given)
    try:
        return subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=docs, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return os.path.abspath(os.path.join(docs, "..", ".."))


def main():
    global REPO, KIT, TITLE, LANG, BASE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("docs")
    ap.add_argument("--target", required=True,
                    choices=["zensical", "mkdocs", "vitepress", "docsify", "gitlab-wiki", "github-wiki"])
    ap.add_argument("--repo", help="repo root (default: git toplevel of the docs dir)")
    ap.add_argument("--out", help="wiki targets: local clone of the wiki repo to sync into")
    ap.add_argument("--base", default="/", help="vitepress: URL base path, e.g. /<repo>/ on <user>.github.io")
    ap.add_argument("--title", default=None)
    ap.add_argument("--section", help="gitlab-wiki: reserved wiki section slug (default per language, or _meta/documake.json wiki.section)")
    ap.add_argument("--source-url", help="gitlab-wiki: base URL of the repo files, e.g. $CI_PROJECT_URL/-/blob/main (banner links)")
    ap.add_argument("--sidebar", choices=["merge", "skip"], default=None, help="gitlab-wiki: merge a marked block into _sidebar.md (default) or leave it alone")
    ap.add_argument("--dry-run", action="store_true", help="gitlab-wiki: show what would change, write nothing")
    ap.add_argument("--force", action="store_true", help="gitlab-wiki: overwrite/delete hand-edited pages inside the section")
    a = ap.parse_args()
    target = "zensical" if a.target == "mkdocs" else a.target

    docs = os.path.abspath(a.docs)
    if not os.path.isfile(os.path.join(docs, "README.md")):
        sys.exit(f"{docs} has no README.md: generate the docs first")
    REPO = repo_root(docs, a.repo)
    docs_rel = os.path.relpath(docs, REPO)
    if docs_rel.startswith(".."):
        sys.exit("--repo must contain the docs dir")
    KIT = os.path.join(REPO, ".documake")
    TITLE = a.title or title_of(os.path.join(docs, "README.md"))
    BASE = a.base if a.base.endswith("/") else a.base + "/"
    LANG, meta = "es", {}
    try:
        meta = json.load(open(os.path.join(docs, "_meta", "documake.json")))
        LANG = meta.get("language", "es")
    except (OSError, ValueError):
        pass

    written = []
    if target in ("gitlab-wiki", "github-wiki"):
        if a.out:
            if target == "gitlab-wiki":
                wcfg = meta.get("wiki") or {}
                gitlab_section(docs, docs_rel, os.path.abspath(a.out),
                               a.section or wcfg.get("section") or SECTION_DEFAULT.get(LANG, SECTION_DEFAULT["en"]),
                               a.source_url, a.sidebar or wcfg.get("sidebar", "merge"), a.dry_run, a.force)
            else:
                sync_wiki(docs, os.path.abspath(a.out), github_rename, True, "_Sidebar.md",
                          lambda r: github_rename(r)[:-3])
        if not a.out:                      # with --out it runs inside CI: don't rewrite the kit there
            written += wiki_kit(target, docs_rel)
    else:
        written += {"zensical": zensical, "vitepress": vitepress, "docsify": docsify}[target](docs, docs_rel)
        written += site_kit(target, docs_rel)
    if written and os.path.abspath(__file__) != os.path.join(KIT, "publish.py"):
        os.makedirs(KIT, exist_ok=True)
        shutil.copy2(os.path.abspath(__file__), os.path.join(KIT, "publish.py"))
        written.append(os.path.join(KIT, "publish.py"))
    for w in written:
        print("wrote", os.path.relpath(w, REPO))
    bad = [p for w in written if w.endswith(".gitlab-ci.yml") for p in lint_gitlab_ci(w)]
    if bad:
        print("CI LINT FAILED (generator bug, do not ship):", *bad, sep="\n  ", file=sys.stderr)
        sys.exit(3)


REPO = KIT = TITLE = LANG = BASE = None

if __name__ == "__main__":
    main()
