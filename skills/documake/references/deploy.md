# Deploying the docs (load only in Step 9, `--publish`)

Rule: **the docs folder in the repo is the source of truth**, reviewed in the
same MR/PR as the code. Every target below is generated from it by
`scripts/publish.py`; never edit a published copy. Running the script writes,
in `<repo>/.documake/`: a vendored copy of `publish.py` (CI runs that copy, so
it never depends on the user's machine) and the deploy files for the chosen
target. **Never edit `.github/workflows/` or `.gitlab-ci.yml` yourself**: tell
the user which file to copy/merge and why.

```bash
python3 $DOCUMAKE/scripts/publish.py <docs-dir> --target <t> [--base /<repo>/] [--out <wiki-clone>] [--repo <root>]
```

## 1. Pick the target

| Situation | Target |
|---|---|
| Default, any forge, best reading experience, Python-only CI | `zensical` |
| Team already on Node/Vue; want the Vue-ecosystem standard | `vitepress` |
| Existing server, no build step, quick | `docsify` |
| Team lives in the project wiki | `gitlab-wiki` (hybrid: reserved section) / `github-wiki` |
| Just read it in the repo | none (no `--publish`) |

| Target | Reader gets | Build | Versioned with code |
|---|---|---|---|
| `zensical` | Site: nav, full-text search, dark mode | `pip install zensical` | yes |
| `vitepress` | Site: sidebar, local search, Mermaid | Node 22 + `vitepress@1` | yes |
| `docsify` | Site rendered in the browser | none (CDN scripts at read time) | yes |
| `gitlab-wiki` | Generated section inside a wiki people also edit; folders kept | none | docs/ yes; wiki = mirror synced by CI |
| `github-wiki` | Wiki + sidebar, **flat**; takes over the whole wiki (overwrites `Home.md`, `_Sidebar.md`) | none | **no** → sync from CI |

Popular real projects use static sites on their own domain (Netlify,
Cloudflare or GitHub Pages behind Cloudflare): VitePress powers vuejs.org,
vite.dev, vitest.dev; FastAPI's docs already build with Zensical. A static
`site/` is host-agnostic: the same output works on any of the hosts below.

## 2. Where it is hosted (per forge)

| Forge | Website | Wiki |
|---|---|---|
| GitHub.com | GitHub Pages via `<t>.github-pages.yml` → `.github/workflows/docs.yml` | `github-wiki-sync.github.yml` |
| GitLab.com | `<t>.gitlab-ci.yml` (job `pages`) | `gitlab-wiki-sync.gitlab-ci.yml` |
| GitLab self-hosted | Pages **only if the admin enabled it** → `<t>.gitlab-ci.yml`; otherwise the Docker image | same as GitLab.com; the wiki is served on the instance's own domain and SSL |
| Any server | `<t>.Dockerfile` (nginx) behind the TLS reverse proxy | — |

### GitHub Pages
1. Once: Settings → Pages → Source **GitHub Actions** (creates the
   `github-pages` environment; the workflow fails before this).
2. Copy `.documake/<t>.github-pages.yml` to `.github/workflows/docs.yml`.
3. URL is `https://<user>.github.io/<repo>/` (a sub-path) unless a custom domain is set.
   - **VitePress** needs the sub-path: regenerate with `--base /<repo>/`.
     With a custom domain keep `--base /`.
   - Custom domain: Settings → Pages → Custom domain + DNS `CNAME` to
     `<user>.github.io`; enable "Enforce HTTPS". Optionally Cloudflare in front.
4. Private repos: Pages needs a paid plan and the site is public by default
   (private Pages is Enterprise Cloud). For internal docs prefer the Docker route.
5. The artifact always excludes `.git`/`.github`; `.nojekyll` is written for docsify.

### GitLab Pages (gitlab.com or self-hosted)
1. Merge the `pages` job from `.documake/<t>.gitlab-ci.yml` into `.gitlab-ci.yml`.
2. **Self-hosted**: Pages must be enabled by an admin: `pages_external_url` on
   a domain that is *not* a subdomain of the GitLab domain, a **wildcard DNS**
   `*.pages-domain`, and ideally a wildcard certificate. If the admin says no,
   use the Docker image below: nothing is asked of the forge.
3. Project URL: `https://<namespace>.<pages-domain>/<project>/` → VitePress
   needs `--base /<project>/`.

### Docker / nginx (works everywhere, incl. GitLab self-hosted without Pages)
```bash
docker build -f .documake/<t>.Dockerfile -t app-docs .     # from the repo root
docker run -d --restart unless-stopped -p 127.0.0.1:8088:80 app-docs
```
Put TLS in front of it. Example nginx (certificate via certbot/Let's Encrypt
or the org CA), path-mounted or on its own host name:
```nginx
server {
  listen 443 ssl;  server_name docs.example.com;
  ssl_certificate     /etc/letsencrypt/live/docs.example.com/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/docs.example.com/privkey.pem;
  location / { proxy_pass http://127.0.0.1:8088; }
}
```
On its own host name keep `--base /`. It can also be a service in the app's
`docker-compose.prod.yml`. For internal docs restrict it (VPN, `allow`/`deny`,
basic auth): a static site has no login of its own.
CI can build and push the image to the forge's container registry, then the
server pulls it (`docker compose pull && docker compose up -d`).

### GitLab wiki — hybrid model (self-hosted or gitlab.com)

The wiki is a **separate git repo** (`<project>.wiki.git`), not part of the MR,
but it lives on the instance's own domain + SSL and inherits the project's
permissions with zero setup. So: `docs/` in the repo stays the **source**
(reviewed in every MR); CI mirrors it into ONE reserved section; the rest of
the wiki belongs to people.

| Wiki area | Owner | Sync behaviour |
|---|---|---|
| `documentacion-del-proyecto/…` (`--section`, default per language; or `wiki.section` in `_meta/documake.json`) | CI only | rewritten each merge; stale generated pages removed; every page starts with `<!-- documake:generated … -->` + a visible "generated, don't edit here" banner linking to the source file |
| everything else (`home.md`, `spikes/`, `project-syncs/`, notes…) | people | **never touched** |
| `_sidebar.md` | people | only a block between `<!-- documake:sidebar:start/end -->` is replaced; appended on first run if there are no markers; created if missing; `--sidebar skip` to opt out |
| design decisions (ADRs) | repo, in the MR that introduces them | not synced — they are discussed in the MR |

Safeguards (all tested): a page inside the section **not** generated by
documake (hand-edited or hand-created) → the sync aborts with exit 2 and lists
it, changing nothing (`--force` overrides, replacing/removing those pages);
`--dry-run` prints the plan without writing; output is deterministic, so
unchanged pages produce no wiki commit; no changes → no commit; `--section`
must be a single lowercase slug.

Tell the user: add a link to `/documentacion-del-proyecto/home` in their wiki
`home.md` (the script only hints; it never edits the home).

**One-time setup per project on GitLab self-hosted** (do it with the user;
the skill can't reach their instance):
1. Project → Settings → General → Visibility: **Wiki enabled**; create any
   first page in the UI (the wiki repo doesn't exist before).
2. Settings → Access tokens: **project access token**, role Developer or
   higher, scope `write_repository`. Settings → CI/CD → Variables: add it as
   **`WIKI_TOKEN`**, masked (protected too if `main` is protected). If your
   version rejects wiki pushes with a project token, use a bot user's token.
3. Merge `.documake/gitlab-wiki-sync.gitlab-ci.yml` into `.gitlab-ci.yml`;
   commit `.documake/` (it holds the vendored `publish.py` the job runs).
4. **SSL**: with a public CA (Let's Encrypt) nothing to do. With an **internal
   CA**, the job needs to trust it: the kit already exports
   `GIT_SSL_CAINFO=$CI_SERVER_TLS_CA_FILE` when the runner defines it
   (`tls-ca-file` in the runner config); otherwise add the CA to the image.
5. The runner must be able to pull `alpine:3.20` (use a registry mirror in
   restricted networks) and reach the instance's URL from inside the job.
6. Non-default port/sub-path installs work: the job derives the wiki URL from
   `$CI_PROJECT_URL`. `resource_group: wiki-sync` serializes concurrent jobs;
   a rejected push retries with `pull --rebase`, so people's wiki edits win.
7. First run by hand if preferred: `git clone <url>.wiki.git`, then
   `publish.py <docs> --target gitlab-wiki --out <clone> --dry-run`, then
   without `--dry-run`; show `git status`, **ask before pushing**.

Optional per-project override in `docs/<…>/_meta/documake.json`:
`"wiki": {"section": "documentacion-tecnica", "sidebar": "merge"}`.

### GitHub wiki
- The wiki repo exists only after creating **any first page in the UI**.
- GitHub wikis are flat: `08-modulos/x.md` → `08-modulos-x.md`, links by page
  name; the script aborts on name clashes.
- **Not hybrid**: this target owns the wiki root (overwrites `Home.md` and
  `_Sidebar.md`, never deletes stale pages). Use it only for a wiki nobody edits by
  hand; otherwise prefer a Pages site. Copy `github-wiki-sync.github.yml` to
  `.github/workflows/wiki.yml`; ask before the first push.

## 3. Gotchas (each cost time in testing)

- **Anchors**: `#section` slugs differ per generator and break with accents;
  the docs link files only (the checker warns).
- **VitePress**: the README becomes `index.md` via `rewrites`; a markdown rule
  in the generated config also fixes in-page `README.md` links (without it the
  build has dead links). `base` must match the URL sub-path.
- **Zensical**: ignores `exclude_docs`, so `_meta/documake.json` (language,
  commit, area names; nothing sensitive) ends up in `site/`.
- **Docsify**: needs internet on the *reader's* side (jsDelivr); fine on
  intranets only with self-hosted copies of the scripts.
- **Mermaid** renders natively in GitHub and GitLab, and in all three sites.
- **`.documake/` is committed** with the repo (it holds the CI copy of the
  script); the site output (`site/`) and `node_modules/` are not: add them to
  `.gitignore`/`.dockerignore`.

## 4. Verify before reporting done

The kits were built and served locally (Zensical, VitePress and Docsify
images answer HTTP 200 with the home title). The GitLab wiki job was run
verbatim in `alpine:3.20` against a local wiki (hand pages preserved, no-op
run makes no commit, push conflict retried). **The other CI YAML files cannot
be run without the forge**: they were only validated as YAML. That check caught
two real bugs (an unquoted `docs: sync` parsed as a map; a bare `true` parsed
as a boolean), so `publish.py` now lints the `script:` lines of the GitLab CI
files it writes and exits 3 if one would not parse as a string. Say that to the user and ask them to watch the first
pipeline run. Locally:
```bash
zensical build -f mkdocs-documake.yml            # → site/
npm i --no-save vitepress@1 vitepress-plugin-mermaid@2 mermaid@11 && npx vitepress build <docs>   # → site/
python3 -m http.server -d <docs> 8080             # docsify
```
Report: files written, which one the user must copy/merge and where, the
one-time setup steps (Pages source, `WIKI_TOKEN`, DNS), and the resulting URL.
