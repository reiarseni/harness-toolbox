#!/usr/bin/env python3
"""Repository facts for documake (stdlib only): stack, repository kind, deployable units,
links to OTHER repositories, and an abbreviated tree.

Usage: repo.py <root> [--json] [--tree-lines 60]

Everything printed carries its evidence file. The model writes docs/01 (stack + repo) and docs/02 (structure)
from this output and verifies it; nothing here is interpretation. Reverse relations (which repos consume THIS
repo) cannot be seen from inside: the doc must say so and mark them [ASK].
"""
import argparse
import collections
import json
import os
import re
import subprocess
import sys

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan import list_files, run, DATA_DIR, LANGS  # noqa: E402

APP_MANIFESTS = {
    "package.json": "Node/JS", "pyproject.toml": "Python", "setup.py": "Python", "requirements.txt": "Python",
    "composer.json": "PHP", "go.mod": "Go", "Cargo.toml": "Rust", "pom.xml": "Java (Maven)",
    "build.gradle": "JVM (Gradle)", "build.gradle.kts": "JVM (Gradle)", "Gemfile": "Ruby", "mix.exs": "Elixir",
    "pubspec.yaml": "Dart/Flutter", "Package.swift": "Swift",
}
PUBLIC_FORGES = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org")
NOISE_PATH = re.compile(r"(^|/)(\.claude|\.windsurf|\.agents?|\.cursor|\.opencode|node_modules|vendor|site-packages)/|"
                        r"(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|composer\.lock|poetry\.lock|uv\.lock|Cargo\.lock|Gemfile\.lock|go\.sum)$")
PUBLIC_REGISTRIES = ("docker.io", "ghcr.io", "quay.io", "mcr.microsoft.com", "gcr.io", "registry.k8s.io",
                     "public.ecr.aws", "registry.hub.docker.com", "nvcr.io", "docker.elastic.co")

# dependency name (lowercase, no extras) -> category
CATEGORIES = {}
for cat, names in {
    "web framework": "fastapi django flask starlette aiohttp tornado sanic express fastify koa hono @nestjs/core "
                     "laravel/framework symfony/framework-bundle slim/slim gin-gonic/gin labstack/echo gofiber/fiber "
                     "actix-web axum rocket spring-boot-starter-web rails sinatra",
    "ui framework": "react vue svelte @angular/core next nuxt solid-js preact astro",
    "ui kit / styling": "tailwindcss @tailwindcss/vite bootstrap daisyui @mui/material antd @base-ui/react shadcn "
                        "@radix-ui/react-dialog class-variance-authority chakra-ui @chakra-ui/react",
    "routing": "react-router react-router-dom @tanstack/react-router vue-router",
    "state / data fetching": "@tanstack/react-query swr redux @reduxjs/toolkit zustand pinia vuex jotai",
    "forms / validation": "react-hook-form zod yup pydantic pydantic-settings marshmallow @hookform/resolvers",
    "http client": "axios ky httpx requests aiohttp guzzlehttp/guzzle",
    "build tooling": "vite webpack esbuild parcel turbo rollup typescript",
    "ORM": "sqlalchemy sqlmodel tortoise-orm peewee django-orm prisma @prisma/client typeorm sequelize drizzle-orm "
           "doctrine/orm gorm.io/gorm diesel sqlx activerecord",
    "migrations": "alembic flyway liquibase knex",
    "DB driver": "asyncpg psycopg psycopg2 psycopg2-binary psycopg-binary pymysql mysqlclient aiomysql aiosqlite pg mysql2 "
                 "pgvector",
    "queue / cache": "redis arq celery rq dramatiq kombu ioredis bullmq predis/predis laravel/horizon sidekiq",
    "server / runtime": "uvicorn gunicorn hypercorn daphne",
    "auth / security": "pyjwt python-jose passlib argon2-cffi cryptography bcrypt jsonwebtoken laravel/sanctum "
                       "laravel/passport lcobucci/jwt authlib",
    "storage": "boto3 minio aioboto3 @aws-sdk/client-s3 league/flysystem",
    "ML / vision": "numpy opencv-python opencv-python-headless torch transformers sentence-transformers onnxruntime "
                   "pillow scikit-learn openai anthropic",
    "observability": "prometheus-client structlog sentry-sdk @sentry/react opentelemetry-api",
    "PDF / reports": "barryvdh/laravel-dompdf reportlab weasyprint pdfkit",
    "i18n": "i18next react-i18next vue-i18n",
    "charts": "recharts chart.js d3 echarts",
    "test": "pytest pytest-asyncio pytest-cov hypothesis vitest jest mocha playwright @playwright/test cypress "
            "@testing-library/react phpunit/phpunit pestphp/pest",
    "lint / format": "ruff black flake8 mypy pyright eslint prettier biome laravel/pint",
}.items():
    for n in names.split():
        CATEGORIES[n] = cat


# ---------- git ----------

def parse_remote(url):
    m = (re.match(r"(?:https?|ssh|git)://(?:[^@/]+@)?([^/:]+)(?::\d+)?/(.+?)(?:\.git)?/?$", url) or
         re.match(r"[^@\s]+@([^:]+):(.+?)(?:\.git)?/?$", url))
    return (m.group(1), m.group(2)) if m else (None, None)


def git_info(root):
    out = run(["git", "remote", "-v"], root) or ""
    remotes = {}
    for ln in out.splitlines():
        p = ln.split()
        if len(p) >= 2 and p[0] not in remotes:
            remotes[p[0]] = p[1]
    origin = remotes.get("origin") or next(iter(remotes.values()), None)
    host, path = parse_remote(origin) if origin else (None, None)
    branch = (run(["git", "branch", "--show-current"], root) or "").strip()
    return {"remotes": remotes, "host": host, "path": path, "default_branch_hint": branch}


# ---------- manifests / stack ----------

def norm_dep(name):
    return re.split(r"[\[<>=!~ ;@]", name.strip().lower(), 1)[0].strip()


def load_toml(path):
    if not tomllib:
        return {}
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except (OSError, ValueError):
        return {}


def read(path, limit=800_000):
    try:
        if os.path.getsize(path) > limit:
            return ""
        return open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return ""


def parse_app(root, d, manifests):
    app = {"dir": d or ".", "manifests": sorted(manifests), "language": None, "name": None, "runtime": None,
           "deps": {}, "workspace_members": None, "package_manager": None, "scripts": []}
    base = os.path.join(root, d)
    langs = sorted({APP_MANIFESTS[m] for m in manifests})
    app["language"] = " + ".join(langs)
    if "package.json" in manifests:
        try:
            pj = json.load(open(os.path.join(base, "package.json"), encoding="utf-8"))
        except (OSError, ValueError):
            pj = {}
        app["name"] = pj.get("name")
        app["runtime"] = (pj.get("engines") or {}).get("node")
        app["package_manager"] = pj.get("packageManager")
        app["scripts"] = sorted((pj.get("scripts") or {}))[:15]
        ws = pj.get("workspaces")
        if ws:
            app["workspace_members"] = ws.get("packages") if isinstance(ws, dict) else ws
        for k in ("dependencies", "devDependencies"):
            for n, v in (pj.get(k) or {}).items():
                app["deps"][n.lower()] = v
    if "pyproject.toml" in manifests:
        t = load_toml(os.path.join(base, "pyproject.toml"))
        proj = t.get("project", {})
        app["name"] = app["name"] or proj.get("name") or t.get("tool", {}).get("poetry", {}).get("name")
        app["runtime"] = app["runtime"] or proj.get("requires-python")
        uvws = t.get("tool", {}).get("uv", {}).get("workspace", {})
        if uvws:
            app["workspace_members"] = uvws.get("members")
        deps = list(proj.get("dependencies", []))
        for grp in (proj.get("optional-dependencies") or {}).values():
            deps += grp
        for grp in (t.get("dependency-groups") or {}).values():
            deps += [x for x in grp if isinstance(x, str)]
        for s in deps:
            n = norm_dep(s)
            m = re.search(r"[<>=!~].*$", s.split(";")[0])
            app["deps"][n] = m.group(0).strip() if m else "*"
        for n, v in (t.get("tool", {}).get("poetry", {}).get("dependencies") or {}).items():
            if n.lower() != "python":
                app["deps"][n.lower()] = v if isinstance(v, str) else "*"
            else:
                app["runtime"] = app["runtime"] or (v if isinstance(v, str) else None)
    if "requirements.txt" in manifests:
        for ln in read(os.path.join(base, "requirements.txt")).splitlines():
            ln = ln.split("#")[0].strip()
            if ln and not ln.startswith(("-", "git+", "http")):
                m = re.search(r"[<>=!~].*$", ln)
                app["deps"][norm_dep(ln)] = m.group(0) if m else "*"
    if "composer.json" in manifests:
        try:
            cj = json.load(open(os.path.join(base, "composer.json"), encoding="utf-8"))
        except (OSError, ValueError):
            cj = {}
        app["name"] = app["name"] or cj.get("name")
        for k in ("require", "require-dev"):
            for n, v in (cj.get(k) or {}).items():
                if n == "php":
                    app["runtime"] = v
                else:
                    app["deps"][n.lower()] = v
    if "go.mod" in manifests:
        text = read(os.path.join(base, "go.mod"))
        mm = re.search(r"^module\s+(\S+)", text, re.M)
        app["name"] = app["name"] or (mm.group(1) if mm else None)
        gv = re.search(r"^go\s+(\S+)", text, re.M)
        app["runtime"] = gv.group(1) if gv else app["runtime"]
        for n, v in re.findall(r"^\s*(\S+/\S+)\s+(v\S+)", text, re.M):
            app["deps"][n.lower()] = v
    if "Cargo.toml" in manifests:
        t = load_toml(os.path.join(base, "Cargo.toml"))
        app["name"] = app["name"] or t.get("package", {}).get("name")
        app["workspace_members"] = app["workspace_members"] or t.get("workspace", {}).get("members")
        for n, v in (t.get("dependencies") or {}).items():
            app["deps"][n.lower()] = v if isinstance(v, str) else (v.get("version", "*") if isinstance(v, dict) else "*")
    if "Gemfile" in manifests:
        for n, v in re.findall(r"^\s*gem\s+['\"]([\w\-]+)['\"](?:\s*,\s*['\"]([^'\"]+)['\"])?", read(os.path.join(base, "Gemfile")), re.M):
            app["deps"][n.lower()] = v or "*"
    for vf in (".python-version", ".nvmrc", ".node-version", ".ruby-version", ".php-version"):
        v = read(os.path.join(base, vf), 200).strip()
        if v:
            app["runtime"] = app["runtime"] or f"{vf.lstrip('.').split('-')[0]} {v}"
    return app


def stack_of(app):
    cats = collections.defaultdict(list)
    for n, v in app["deps"].items():
        cat = CATEGORIES.get(n)
        if cat:
            cats[cat].append(f"{n} {v}".strip() if v not in ("*", "") else n)
    return {k: sorted(v)[:8] for k, v in sorted(cats.items())}


# ---------- workspaces / kind ----------

def workspace_evidence(root, files, apps):
    ev = []
    for a in apps:
        if a["workspace_members"]:
            ev.append((f"{a['dir']}/{a['manifests'][0]}".replace("./", ""), f"workspaces: {a['workspace_members']}"))
    for f in files:
        base = os.path.basename(f)
        if base in ("pnpm-workspace.yaml", "lerna.json", "nx.json", "turbo.json", "go.work", "rush.json"):
            ev.append((f, "workspace tooling file"))
        elif base in ("settings.gradle", "settings.gradle.kts") and re.search(r"include\s*\(?\s*['\"]", read(os.path.join(root, f))):
            ev.append((f, "gradle multi-project include"))
        elif base == "pom.xml" and "<modules>" in read(os.path.join(root, f)):
            ev.append((f, "maven <modules>"))
    return ev


def compose_services(root, files):
    out = []
    for f in files:
        if not re.search(r"(^|/)(docker-)?compose[^/]*\.ya?ml$", f):
            continue
        lines = read(os.path.join(root, f)).splitlines()
        try:
            i = next(k for k, l in enumerate(lines) if re.match(r"services:\s*$", l))
        except StopIteration:
            continue
        svc, ind, cur = None, None, None
        for l in lines[i + 1:]:
            if not l.strip() or l.lstrip().startswith("#"):
                continue
            cur_ind = len(l) - len(l.lstrip())
            if cur_ind == 0:
                break
            if ind is None:
                ind = cur_ind
            if cur_ind == ind and re.match(r"\s*[\w.\-]+:\s*$", l):
                svc = {"file": f, "service": l.strip().rstrip(":"), "image": None, "build": None, "ports": [], "depends_on": [], "volumes": []}
                out.append(svc)
                cur = None
                continue
            if svc is None or cur_ind <= ind:
                continue
            s = l.strip()
            mi = re.match(r"image:\s*['\"]?([^'\"\s#]+)", s)
            if mi:
                svc["image"] = mi.group(1)
            mb = re.match(r"build:\s*['\"]?([^'\"\s#]+)", s)
            if mb:
                svc["build"] = mb.group(1)
            mc = re.match(r"context:\s*['\"]?([^'\"\s#]+)", s)
            if mc:
                svc["build"] = mc.group(1)
            if re.match(r"(ports|depends_on|volumes):", s):
                cur = s.split(":")[0]
                continue
            if cur == "volumes" and not s.startswith("- "):
                msrc = re.match(r"source:\s*['\"]?([^'\"\s#]+)", s)
                if msrc:
                    svc["volumes"].append(msrc.group(1) + ":(long form)")
                continue
            if cur and s.startswith("- "):
                val = s[2:].strip().strip("'\"")
                if cur == "volumes":
                    if not val.startswith(("type:", "source:", "target:")):
                        svc["volumes"].append(val)
                else:
                    (svc["ports"] if cur == "ports" else svc["depends_on"]).append(val)
            elif cur and cur == "depends_on" and re.match(r"[\w.\-]+:\s*$", s) and cur_ind == ind + 4:
                svc["depends_on"].append(s.rstrip(":"))
            elif not s.startswith("- ") and cur_ind <= ind + 2:
                cur = None
    return out


def dockerfiles(root, files):
    out = []
    for f in files:
        if re.search(r"(^|/)(Dockerfile[^/]*|[^/]*\.Dockerfile)$", f) and "/.documake/" not in "/" + f:
            text = read(os.path.join(root, f))
            stages = {a.lower() for a in re.findall(r"^FROM\s+.*?\s+AS\s+(\S+)", text, re.M | re.I)}
            froms = [x for x in re.findall(r"^FROM\s+(?:--platform=\S+\s+)?(\S+)", text, re.M | re.I) if x.lower() not in stages]
            out.append({"file": f, "from": froms})
    return out


def ci_jobs(root, files):
    out = []
    reserved = {"stages", "default", "variables", "include", "workflow", "image", "services", "cache",
                "before_script", "after_script", "pages"}
    for f in files:
        if os.path.basename(f) != ".gitlab-ci.yml" or f.count("/") > 1:
            continue
        cur = None
        for l in read(os.path.join(root, f)).splitlines():
            m = re.match(r"^([A-Za-z][\w:\-./ ]*):\s*(#.*)?$", l)
            if m:
                cur = {"job": m.group(1), "stage": None}
                if m.group(1) not in reserved or m.group(1) == "pages":
                    out.append({"file": f, **cur})
                    cur = out[-1]
                else:
                    cur = None
            elif cur is not None:
                s = re.match(r"\s+stage:\s*(\S+)", l)
                if s:
                    cur["stage"] = s.group(1)
    return out


# ---------- links to other repositories ----------

def link_evidence(root, files, git, apps):
    host, self_path = git["host"], git["path"]
    links = {}

    def add(kind, target, evidence, conf="declared", note=""):
        key = (kind, target)
        if key not in links:
            links[key] = {"kind": kind, "target": target, "evidence": evidence, "confidence": conf, "note": note}

    def public(t):
        return any(t.startswith(p + "/") or t == p for p in PUBLIC_REGISTRIES)

    def registry_ref(ref):
        first = ref.split("/")[0]
        return "/" in ref and ("." in first or ":" in first) and not public(ref) and "$" not in first

    text_files = [f for f in files if not NOISE_PATH.search(f) and os.path.splitext(f)[1].lower() in
                  (".yml", ".yaml", ".json", ".toml", ".txt", ".md", ".cfg", ".ini", ".env", ".example", ".sh",
                   ".py", ".ts", ".tsx", ".js", ".php", ".go", ".mod", ".rs", ".gradle", ".xml", "")
                  or os.path.basename(f) in ("Dockerfile", ".gitmodules", ".npmrc", "Gemfile")][:1500]
    url_re = None
    if host:
        url_re = re.compile(r"https?://" + re.escape(host) + r"(?::\d+)?/([\w.\-]+(?:/[\w.\-]+)+?)(?=\.git\b|/-/|/blob\b|/tree\b|/issues\b|/merge_requests\b|/wikis\b|/pipelines\b|/raw\b|/api/|[\"'\s)>`,;]|$)")

    for f in files:
        base = os.path.basename(f)
        text = read(os.path.join(root, f)) if (base in ("Dockerfile", ".gitmodules", ".npmrc", "Gemfile", "go.mod", "Cargo.toml", "package.json",
                                                         "composer.json", "pyproject.toml", "requirements.txt", "pip.conf") or
                                                base.endswith((".gitlab-ci.yml", ".Dockerfile")) or
                                                re.search(r"(docker-)?compose[^/]*\.ya?ml$", base) or
                                                f.startswith(".gitlab/")) else ""
        if not text:
            continue
        if base == ".gitmodules":
            for u in re.findall(r"url\s*=\s*(\S+)", text):
                add("git submodule", u, f)
        if base.endswith(".gitlab-ci.yml") or f.startswith(".gitlab/"):
            lines = text.splitlines()
            for i, ln in enumerate(lines):
                m = re.search(r"\bproject:\s*['\"]?([\w./\-]+)", ln)
                if m:
                    ctx = " ".join(lines[max(0, i - 6): i]).lower()
                    kind = ("ci include from project" if "include:" in ctx else "ci downstream trigger" if "trigger:" in ctx
                            else "ci artifact from project" if "needs:" in ctx else "ci project reference")
                    add(kind, m.group(1), f)
                m = re.search(r"\bcomponent:\s*['\"]?([^\s'\"@]+)", ln)
                if m:
                    add("ci component", m.group(1), f)
                m = re.search(r"\bremote:\s*['\"]?(https?://\S+?)['\"]?\s*$", ln)
                if m:
                    add("ci remote include", m.group(1), f)
                m = re.match(r"\s*trigger:\s*['\"]?([\w.\-]+/[\w./\-]+)['\"]?\s*$", ln)
                if m:
                    add("ci downstream trigger", m.group(1), f)
                m = re.match(r"\s*image:\s*['\"]?([^\s'\"#]+)", ln)
                if m and registry_ref(m.group(1)) and "CI_REGISTRY_IMAGE" not in m.group(1):
                    add("registry image", m.group(1), f)
                if "CI_JOB_TOKEN" in ln and re.search(r"git\s+clone|gitlab-ci-token", ln):
                    add("ci clones another repo", ln.strip()[:120], f, note="uses CI_JOB_TOKEN (needs the job token allowlist)")
        if base == "package.json":
            try:
                pj = json.loads(text)
            except ValueError:
                pj = {}
            for k in ("dependencies", "devDependencies"):
                for n, v in (pj.get(k) or {}).items():
                    if isinstance(v, str) and re.match(r"(git\+|git@|github:|gitlab:|https?://.*\.git|link:\.\.|file:\.\.)", v):
                        add("package from git/path", f"{n} → {v}", f)
        if base in ("requirements.txt", "pyproject.toml", "pip.conf"):
            for u in re.findall(r"git\+(?:https?|ssh)://[^\s\"',]+", text):
                add("python dependency from git", u, f)
            for u in re.findall(r"(?:extra-)?index-url\s*[=\s]\s*['\"]?(https?://[^\s'\"]+)", text) + re.findall(r"^url\s*=\s*['\"](https?://[^'\"]+)", text, re.M):
                if "pypi.org" in u:
                    continue
                add("GitLab package registry" if (host and host in u) else "external package index (not a GitLab repo)", u, f)
        if base == ".npmrc":
            for sc, u in re.findall(r"(@[\w\-]+):registry\s*=\s*(\S+)", text):
                add("GitLab package registry" if (host and host in u) else "external package registry", f"{sc} → {u}", f)
        if base == "composer.json":
            try:
                cj = json.loads(text)
            except ValueError:
                cj = {}
            repos = cj.get("repositories") or []
            for r in (repos.values() if isinstance(repos, dict) else repos):
                if isinstance(r, dict) and r.get("url") and r.get("type") in ("vcs", "path", "composer", "git"):
                    add(f"composer {r['type']} repository", r["url"], f)
        if base == "go.mod":
            for a, b in re.findall(r"^\s*(?:replace\s+)?(\S+)(?:\s+\S+)?\s*=>\s*(\S+)", text, re.M):
                add("go replace", f"{a} => {b}", f)
            if host:
                for m in re.findall(r"^\s*(" + re.escape(host) + r"/\S+)\s+v", text, re.M):
                    add("go module from same GitLab", m, f)
        if base == "Cargo.toml":
            for u in re.findall(r"git\s*=\s*['\"]([^'\"]+)", text):
                add("rust dependency from git", u, f)
            for p in re.findall(r"path\s*=\s*['\"](\.\./[^'\"]+)", text):
                add("rust path dependency outside repo", p, f)
        if base == "Gemfile":
            for u in re.findall(r"git:\s*['\"]([^'\"]+)", text):
                add("gem from git", u, f)
        if base == "Dockerfile" or base.endswith(".Dockerfile"):
            stages = {a.lower() for a in re.findall(r"^FROM\s+.*?\s+AS\s+(\S+)", text, re.M | re.I)}
            for ref in re.findall(r"^FROM\s+(?:--platform=\S+\s+)?(\S+)", text, re.M | re.I):
                if ref.lower() not in stages and registry_ref(ref):
                    add("registry image", ref, f)
        if re.search(r"(docker-)?compose[^/]*\.ya?ml$", base):
            for ref in re.findall(r"^\s*image:\s*['\"]?([^\s'\"#]+)", text, re.M):
                if registry_ref(ref):
                    add("registry image", ref, f)
            for ctx in re.findall(r"^\s*(?:context|build):\s*['\"]?(\.\.[^\s'\"#]*)", text, re.M):
                resolved = os.path.normpath(os.path.join(os.path.dirname(f), ctx))
                if resolved.startswith(".."):
                    add("build context outside repo", ctx, f, note="a sibling directory/repo is needed to build")
    if url_re:
        counts = collections.Counter()
        first = {}
        for f in text_files:
            txt = read(os.path.join(root, f), 300_000)
            for m in url_re.finditer(txt):
                p = m.group(1).rstrip("/")
                if self_path and (p == self_path or p.startswith(self_path + "/")):
                    continue
                if host in PUBLIC_FORGES and self_path and not p.startswith(self_path.split("/")[0] + "/"):
                    continue          # public forge: only repos of the same owner/org are "ours"
                counts[p] += 1
                first.setdefault(p, f)
        for p, n in counts.most_common(25):
            if not any(p in l["target"] or l["target"] in p for l in links.values() if l["confidence"] == "declared"):
                add("mention (URL)", f"{host}/{p}", first[p], "mention", f"{n} mention(s)")
    order = {"declared": 0, "mention": 1}
    return sorted(links.values(), key=lambda l: (order[l["confidence"]], l["kind"], l["target"]))


# ---------- abbreviated tree ----------

NOTABLE = re.compile(r"^(README[^/]*|Makefile|justfile|Dockerfile|docker-compose[^/]*|compose[^/]*|\.gitlab-ci\.yml|"
                     r"package\.json|pyproject\.toml|composer\.json|go\.mod|Cargo\.toml|pom\.xml|\.env\.example|CLAUDE\.md|AGENTS\.md)$")


def abbreviated_tree(files, budget=60):
    tree = {}
    for f in files:
        if DATA_DIR.search(f):
            continue
        node = tree
        parts = f.split("/")
        for p in parts[:-1]:
            node = node.setdefault(p + "/", {})
        node.setdefault("__files__", []).append(parts[-1])

    def count(n):
        return len(n.get("__files__", [])) + sum(count(v) for k, v in n.items() if k != "__files__")

    lines = []
    expanded = set()
    # decide which dirs to expand: greedily by weight until budget
    order = []

    def collect(n, path, depth):
        for k, v in n.items():
            if k == "__files__":
                continue
            order.append((count(v), depth, path + k))
            collect(v, path + k, depth + 1)
    collect(tree, "", 1)
    shown = len({k for k in tree if k != "__files__"}) + len([f for f in tree.get("__files__", []) if NOTABLE.match(f)])
    for w, depth, path in sorted(order, key=lambda t: (-t[0], t[1], t[2])):
        if depth > 4 or w < 3:
            continue
        parent = "/".join(path.split("/")[:-1]) + "/" if "/" in path[:-1] else ""
        if parent and parent not in expanded and parent != "":
            continue
        node = tree
        for p in path.split("/"):
            if p:
                node = node[p + "/"]
        kids = len([k for k in node if k != "__files__"]) + len([f for f in node.get("__files__", []) if NOTABLE.match(f)])
        if shown + min(kids, 12) > budget:
            continue
        expanded.add(path + "/" if not path.endswith("/") else path)
        shown += min(kids, 12)

    def render(n, path, prefix):
        dirs = sorted((k for k in n if k != "__files__"), key=lambda k: k)
        notable = sorted(f for f in n.get("__files__", []) if NOTABLE.match(f))
        entries = [(k, True) for k in dirs] + [(f, False) for f in notable]
        if path:
            dirs_sorted = sorted(dirs, key=lambda k: -count(n[k]))[:12]
            entries = [(k, True) for k in sorted(dirs_sorted)] + [(f, False) for f in notable][:4]
            hidden = len(dirs) - len(dirs_sorted)
        else:
            hidden = 0
        for i, (name, is_dir) in enumerate(entries):
            last = i == len(entries) - 1 and not hidden
            branch = "└── " if last else "├── "
            if is_dir:
                sub = n[name]
                key = path + name
                if key in expanded:
                    lines.append(f"{prefix}{branch}{name}")
                    render(sub, key, prefix + ("    " if last else "│   "))
                else:
                    n_ = count(sub)
                    lines.append(f"{prefix}{branch}{name}   ({n_} file{'s' if n_ != 1 else ''})")
            else:
                lines.append(f"{prefix}{branch}{name}")
        if hidden:
            lines.append(f"{prefix}└── … +{hidden} more dirs")

    render(tree, "", "")
    return lines


# ---------- main ----------

CACHE_NAMES = {".git", "node_modules", "vendor", "dist", "build", "out", "target", "__pycache__", ".venv", "venv",
               "env", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".next", ".nuxt", ".cache", "coverage",
               ".idea", ".vscode", ".gradle", "obj", ".terraform", "site-packages", ".parcel-cache", ".turbo",
               ".svelte-kit", ".angular", ".playwright-mcp", ".DS_Store", "htmlcov", ".coverage", "tmp", "temp",
               "logs", "log", ".claude", ".documake"}
STRONG_DATA_HINT = re.compile(r"(upload|media|storage|attachment|avatar|photo|image|video|document|pgdata|postgres|mysql|"
                              r"mariadb|mongo|redis|minio|backup)", re.I)
WEAK_DATA_HINT = re.compile(r"(^|/)(data|db|files?|exports?|imports?|reports?|assets|private|public/[^/]+)($|/)", re.I)
OPS_PATTERNS = {
    "uploads / file storage in code": r"\b(upload_to|MEDIA_ROOT|UPLOAD_DIR|upload_dir|UPLOAD_FOLDER|storage_path|multer|formidable|"
                                      r"FileSystemStorage|Storage::disk|createWriteStream|shutil\.copy|move_uploaded_file|"
                                      r"ActiveStorage|carrierwave|paperclip)\b",
    "S3 / object storage": r"\b(boto3|S3Client|@aws-sdk/client-s3|S3_BUCKET|AWS_S3\w*|AWS_STORAGE_BUCKET_NAME|minio|MINIO_\w+|"
                           r"django-storages|storages\.backends|GCS_BUCKET|AZURE_STORAGE\w*)\b",
    "backup tooling": r"\b(pg_dump|pg_dumpall|mysqldump|mongodump|restic|rclone|borgbackup|duplicity|pgbackrest|barman|"
                      r"wal-g|mc mirror|aws s3 (sync|cp)|BACKUP_\w+)\b",
    "observability / logging": r"\b(SENTRY_DSN|sentry_sdk|Sentry\.init|@sentry/\w+|sentry-sdk|OTEL_\w+|opentelemetry|"
                               r"prometheus[_-]client|datadog|ddtrace|newrelic|rollbar|bugsnag|structlog|winston|pino|"
                               r"LOG_LEVEL|LOG_FILE|LOG_DIR|logrotate|logging\.config|RotatingFileHandler)\b",
}
OPS_RX = {k: re.compile(v, re.I) for k, v in OPS_PATTERNS.items()}
OPS_SCAN_EXT = (".py", ".js", ".ts", ".mjs", ".cjs", ".php", ".rb", ".go", ".java", ".kt", ".cs", ".rs", ".sh", ".yml",
                ".yaml", ".toml", ".json", ".ini", ".cfg", ".conf", ".env", ".example", ".sample", ".txt", ".md", ".cron")
OBS_IMAGES = re.compile(r"(grafana|loki|prometheus|promtail|jaeger|tempo|sentry|glitchtip|elastic|kibana|logstash|fluent|"
                        r"vector|uptime-kuma|otel|signoz|datadog|netdata)", re.I)


def ignored_paths(root):
    """Paths git ignores that exist on disk (not known caches): candidates for user data outside git."""
    out = run(["git", "ls-files", "-o", "-i", "--exclude-standard", "--directory"], root)
    if out is None:
        return None
    res = []
    for line in out.splitlines():
        rel = line.rstrip("/")
        if not rel or any(part in CACHE_NAMES or part.endswith((".egg-info", ".pyc")) for part in rel.split("/")):
            continue
        full = os.path.join(root, rel)
        if os.path.isdir(full):
            n, size = 0, 0
            for dp, _, fns in os.walk(full):
                for fn in fns:
                    n += 1
                    try:
                        size += os.path.getsize(os.path.join(dp, fn))
                    except OSError:
                        pass
                if n > 20000:
                    break
            res.append({"path": rel + "/", "kind": "dir", "files": n, "bytes": size,
                        "signal": "strong" if STRONG_DATA_HINT.search(rel) else ("weak" if WEAK_DATA_HINT.search(rel) else None)})
        else:
            base = os.path.basename(rel)
            kind = "config" if base.startswith(".env") or base.endswith((".pem", ".key")) or "secret" in base.lower() else "file"
            res.append({"path": rel, "kind": kind, "files": 1, "bytes": os.path.getsize(full) if os.path.exists(full) else 0,
                        "signal": None})
    return sorted(res, key=lambda x: ({"strong": 0, "weak": 1}.get(x["signal"], 2), x["path"]))[:60]


def compose_mounts(root, services):
    """Bind mounts and named volumes per compose service; bind mounts flagged when git ignores the host path."""
    mounts = []
    for s in services:
        base = os.path.dirname(s["file"])
        for v in s.get("volumes", []):
            src = v.split(":")[0]
            if src.startswith(("./", "../", "/", "~")) or (os.sep in src and not src.startswith("$")):
                host = os.path.normpath(os.path.join(base, src)) if not src.startswith(("/", "~")) else src
                ign = None
                if not host.startswith(("/", "~")):
                    ign = subprocess.run(["git", "check-ignore", "-q", host], cwd=root, capture_output=True).returncode == 0
                mounts.append({"service": s["service"], "kind": "bind", "host": host, "spec": v, "git_ignored": ign,
                               "file": s["file"]})
            elif src:
                mounts.append({"service": s["service"], "kind": "named", "host": src, "spec": v, "git_ignored": None,
                               "file": s["file"]})
    return mounts


def operations(root, files, services):
    """Evidence for docs/13: docker mounts, ignored data dirs, storage/backup/observability references."""
    hits = {k: {} for k in OPS_RX}
    scanned = 0
    for f in files:
        if scanned > 4000:
            break
        base = os.path.basename(f)
        if not (f.endswith(OPS_SCAN_EXT) or base in ("Makefile", "Dockerfile", "crontab") or ".env" in base):
            continue
        if "/.documake/" in "/" + f or f.startswith("docs/") or "/tests/" in "/" + f or "/test/" in "/" + f:
            continue
        txt = read(os.path.join(root, f), limit=200_000)
        if not txt:
            continue
        scanned += 1
        for k, rx in OPS_RX.items():
            m = rx.search(txt)
            if m and len(hits[k]) < 12:
                hits[k].setdefault(f, m.group(0))
    for f in files:
        if re.search(r"(^|/)[^/]*backup[^/]*$", f, re.I) and len(hits["backup tooling"]) < 12:
            hits["backup tooling"].setdefault(f, "file name")
    obs_services = [s["service"] for s in services if OBS_IMAGES.search((s.get("image") or "") + " " + s["service"])]
    return {
        "compose_mounts": compose_mounts(root, services),
        "ignored_paths": ignored_paths(root),
        "code_evidence": {k: [{"file": f, "match": m} for f, m in v.items()] for k, v in hits.items()},
        "observability_services": obs_services,
    }


def analyze(root, tree_lines=60):
    files, _ = list_files(root)
    files = [f for f in files if not DATA_DIR.search(f)]
    git = git_info(root)
    by_dir = collections.defaultdict(set)
    for f in files:
        b = os.path.basename(f)
        if b in APP_MANIFESTS and "/.documake/" not in "/" + f and "/node_modules/" not in "/" + f:
            by_dir[os.path.dirname(f)].add(b)
    apps = [parse_app(root, d, m) for d, m in sorted(by_dir.items())][:30]
    for a in apps:
        a["stack"] = stack_of(a)
    ws = workspace_evidence(root, files, apps)
    top_apps = [a for a in apps if a["dir"] != "."]
    root_app = next((a for a in apps if a["dir"] == "."), None)
    if ws:
        kind = "monorepo-with-workspace-tooling"
    elif len(apps) >= 2 and (len({a["dir"].split("/")[0] for a in top_apps}) >= 2 or (root_app and top_apps)):
        kind = "multi-app-repo"
    else:
        kind = "single-project"
    services = compose_services(root, files)
    return {
        "git": git, "kind": kind, "kind_evidence": [{"file": f, "what": w} for f, w in ws],
        "apps": apps, "compose_services": services, "dockerfiles": dockerfiles(root, files),
        "ci_jobs": ci_jobs(root, files), "operations": operations(root, files, services), "links": link_evidence(root, files, git, apps),
        "tree": abbreviated_tree(files, tree_lines),
    }


def print_human(r):
    p = print
    g = r["git"]
    p(f"# Repository facts")
    p(f"origin: {g['remotes'].get('origin') or '(no remote)'} → host={g['host']} project={g['path']} · branch={g['default_branch_hint']}")
    p(f"\n## Repository kind: {r['kind']}")
    p("- monorepo-with-workspace-tooling: workspace tool present (npm/pnpm/Nx/Turbo/Cargo/go.work/uv/Maven/Gradle)\n"
      "- multi-app-repo: several apps with own manifests in one repo, no workspace tooling (often called a monorepo informally)\n"
      "- single-project: one app/library")
    for e in r["kind_evidence"]:
        p(f"  evidence: {e['file']} — {e['what']}")
    p("\n## Apps (own manifest) and stack")
    for a in r["apps"]:
        p(f"- {a['dir']}: {a['language']}  name={a['name']}  runtime={a['runtime']}  pm={a['package_manager']}")
        p(f"    manifests: {', '.join(a['manifests'])}")
        for cat, items in a["stack"].items():
            p(f"    {cat}: {', '.join(items)}")
        if a["scripts"]:
            p(f"    scripts: {', '.join(a['scripts'])}")
    p("\n## Deployable units")
    for s in r["compose_services"]:
        p(f"- compose service `{s['service']}` ({s['file']}): image={s['image']} build={s['build']} ports={s['ports']} depends_on={s['depends_on']} volumes={s['volumes']}")
    for d in r["dockerfiles"]:
        p(f"- Dockerfile {d['file']}: FROM {', '.join(d['from'])}")
    for j in r["ci_jobs"]:
        p(f"- CI job `{j['job']}` (stage {j['stage']}) in {j['file']}")
    p("\n## Links to OTHER repositories (evidence; declared = a config says so, mention = only a URL)")
    if not r["links"]:
        p("- none found in: .gitmodules, CI (include/trigger/needs/images), package manifests, Dockerfiles/compose, "
          "registries, URLs to the same GitLab host")
    for l in r["links"]:
        p(f"- [{l['confidence']}] {l['kind']}: {l['target']}  ← {l['evidence']}" + (f"  ({l['note']})" if l["note"] else ""))
    p("NOTE: repos that consume THIS repo can't be seen from inside → write [ASK: who consumes this repo?] or search the GitLab group.")
    o = r["operations"]
    p("\n## Operations evidence (write docs/13 from this; verify each item in code)")
    for m in o["compose_mounts"]:
        flag = {True: " — HOST PATH IGNORED BY GIT (persistent data outside git)", False: " — versioned", None: ""}[m["git_ignored"]]
        p(f"- compose {m['kind']} mount, service `{m['service']}`: {m['spec']}{flag}  ← {m['file']}")
    if o["ignored_paths"] is None:
        p("- ignored paths: not a git repo, cannot tell what is outside git → [ASK: which folders hold user data?]")
    for i in o["ignored_paths"] or []:
        hint = {"strong": " ← STRONG signal: name says user data/uploads/DB. Document it in 13",
                "weak": " ← WEAK signal (generic name): EVALUATE before deciding", None: ""}[i["signal"]]
        p(f"- git-ignored on disk ({i['kind']}): {i['path']}  files={i['files']} bytes={i['bytes']}{hint}")
    for k, items in o["code_evidence"].items():
        for e in items:
            p(f"- {k}: `{e['match']}` in {e['file']}")
    for sname in o["observability_services"]:
        p(f"- observability compose service: `{sname}`")
    p("EVALUATE every git-ignored path before deciding (name alone proves nothing): (1) grep who writes it; (2) can the app "
      "rebuild it from git + a command? (3) would deleting it lose something a user or admin created? "
      "Data that cannot be rebuilt → row under 'Data outside git' + goes in the backup. Rebuildable → say 'disposable' and why, "
      "or list it in _meta/documake.json \"operations_ignore\": [{\"path\": ..., \"reason\": ...}].")
    p("NOTE: backups are usually configured outside the repo (server cron, infra pipeline, managed service): "
      "no evidence above → [ASK: what is backed up (DB + uploaded files), to where (disk / which S3), how often, how to restore?]")
    p("\n## Abbreviated tree (annotate each line with its role in docs/02)")
    p("```\n" + "\n".join(r["tree"]) + "\n```")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--tree-lines", type=int, default=60)
    a = ap.parse_args()
    r = analyze(os.path.abspath(a.root), a.tree_lines)
    if a.json:
        json.dump(r, sys.stdout, ensure_ascii=False, indent=1)
        print()
    else:
        print_human(r)


if __name__ == "__main__":
    main()
