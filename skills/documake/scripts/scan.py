#!/usr/bin/env python3
"""Deterministic repository inventory for documake.

Usage: scan.py <root> [--json] [--depth N]

Stdlib only. Honors .gitignore when the repo is git.
"""
import argparse
import collections
import json
import os
import re
import subprocess
import sys

EXCLUDED_DIRS = {
    ".git", "node_modules", "vendor", "dist", "build", "out", "target",
    "__pycache__", ".venv", "venv", "env", ".tox", ".mypy_cache",
    ".pytest_cache", ".next", ".nuxt", ".cache", "coverage", ".idea",
    ".vscode", ".gradle", "obj", ".terraform", "site-packages",
}

LANGS = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".cjs": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".go": "Go",
    ".rs": "Rust", ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin",
    ".scala": "Scala", ".rb": "Ruby", ".php": "PHP", ".cs": "C#", ".fs": "F#",
    ".c": "C", ".h": "C/C++ header", ".cpp": "C++", ".cc": "C++", ".hpp": "C++",
    ".swift": "Swift", ".m": "Objective-C", ".dart": "Dart", ".ex": "Elixir",
    ".exs": "Elixir", ".erl": "Erlang", ".clj": "Clojure", ".hs": "Haskell",
    ".lua": "Lua", ".r": "R", ".jl": "Julia", ".sh": "Shell", ".bash": "Shell",
    ".ps1": "PowerShell", ".sql": "SQL", ".vue": "Vue", ".svelte": "Svelte",
    ".html": "HTML", ".css": "CSS", ".scss": "SCSS", ".proto": "Protobuf",
    ".graphql": "GraphQL", ".tf": "Terraform", ".zig": "Zig", ".nim": "Nim",
}

MANIFESTS = {
    "package.json": "Node/JS", "pnpm-workspace.yaml": "pnpm monorepo",
    "lerna.json": "Lerna monorepo", "nx.json": "Nx monorepo", "turbo.json": "Turborepo",
    "pyproject.toml": "Python", "setup.py": "Python", "setup.cfg": "Python",
    "requirements.txt": "Python", "Pipfile": "Python (pipenv)", "poetry.lock": "Python (poetry)",
    "uv.lock": "Python (uv)", "go.mod": "Go", "Cargo.toml": "Rust", "pom.xml": "Java (Maven)",
    "build.gradle": "JVM (Gradle)", "build.gradle.kts": "JVM (Gradle)", "Gemfile": "Ruby",
    "composer.json": "PHP", "mix.exs": "Elixir", "pubspec.yaml": "Dart/Flutter",
    "Package.swift": "Swift", "CMakeLists.txt": "C/C++ (CMake)", "Makefile": "Make",
    "justfile": "just", "Taskfile.yml": "Task", "deno.json": "Deno", "bun.lockb": "Bun",
}

INFRA = {
    "Dockerfile": "Docker", "docker-compose.yml": "Docker Compose",
    "docker-compose.yaml": "Docker Compose", "compose.yml": "Docker Compose",
    "compose.yaml": "Docker Compose", "Procfile": "Procfile", "fly.toml": "Fly.io",
    "vercel.json": "Vercel", "netlify.toml": "Netlify", "serverless.yml": "Serverless",
    "helm": "Helm", "k8s": "Kubernetes", "kubernetes": "Kubernetes",
    ".env.example": "Env template", ".env.sample": "Env template",
}

CI = {
    ".github/workflows": "GitHub Actions", ".gitlab-ci.yml": "GitLab CI",
    "Jenkinsfile": "Jenkins", ".circleci": "CircleCI", "azure-pipelines.yml": "Azure Pipelines",
    "bitbucket-pipelines.yml": "Bitbucket Pipelines", ".drone.yml": "Drone",
}

ENTRY_NAMES = re.compile(
    r"(^|/)(main|index|app|server|cli|__main__|manage|wsgi|asgi|program|startup|bootstrap)"
    r"\.(py|js|ts|tsx|go|rs|java|kt|rb|php|cs|mjs|cjs)$"
)
ENTRY_PATTERNS = [
    (re.compile(r"\A#!"), "Executable script (shebang)"),
    (re.compile(r'if __name__ == ["\']__main__["\']'), "Python __main__"),
    (re.compile(r"^func main\(\)", re.M), "Go main"),
    (re.compile(r"^fn main\(\)", re.M), "Rust main"),
    (re.compile(r"public static void main\("), "Java main"),
    (re.compile(r"\.listen\(\s*\w+"), "Listening server"),
    (re.compile(r"FastAPI\(|Flask\(__name__\)|express\(\)|new Hono\(|Fastify\("), "Web app"),
    (re.compile(r"@click\.command|argparse\.ArgumentParser|typer\.Typer\(|commander"), "CLI"),
]

DOC_NAMES = re.compile(
    r"(^|/)(README|CONTRIBUTING|ARCHITECTURE|CHANGELOG|CLAUDE|AGENTS|DESIGN|HACKING|"
    r"DEVELOPMENT|SECURITY|CONTEXT)[^/]*$", re.I
)
TEST_DIR = re.compile(r"(^|/)(tests?|__tests__|spec|specs|e2e|integration)(/|$)", re.I)
TEST_FILE = re.compile(r"(^|/)(test_[^/]+|[^/]+_test\.\w+|[^/]+\.(test|spec)\.\w+|[^/]+(Test|Tests|Spec)\.\w+)$")
DATA_DIR = re.compile(r"(^|/)(backups?|dumps?|snapshots?|backup[_-][^/]*|dump[_-][^/]*)/", re.I)
ROUTE_FILE = re.compile(
    r"(^|/)(routes?/[^/]+|urls\.py|[^/]*routes?\.(ts|tsx|js|mjs|py|rb|go|php)|router\.(ts|tsx|js|py|go)|"
    r"config/routes\.rb|[^/]*Controller\.(php|java|kt|cs))$"
)
DOC_EXT = {".md", ".mdx", ".rst", ".txt", ".adoc"}
GENERATED = re.compile(r"(\.min\.(js|css)$|\.pb\.go$|_pb2\.py$|\.generated\.|\.lock$|-lock\.json$)")


def run(cmd, cwd):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def list_files(root):
    out = run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], root)
    if out is not None:
        files = [f for f in out.splitlines() if f]
        return [f for f in files if not any(p in EXCLUDED_DIRS for p in f.split("/")[:-1])], True
    files = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d == ".github" or (d not in EXCLUDED_DIRS and not d.startswith("."))]
        for fn in fns:
            files.append(os.path.relpath(os.path.join(dp, fn), root))
    return files, False


def count_lines(path):
    try:
        with open(path, "rb") as fh:
            head = fh.read(8192)
            if b"\0" in head:
                return None
            return head.count(b"\n") + sum(chunk.count(b"\n") for chunk in iter(lambda: fh.read(65536), b""))
    except OSError:
        return None


def read_head(path, n=20000):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read(n)
    except OSError:
        return ""


def scan(root, depth):
    files, is_git = list_files(root)
    langs = collections.Counter()
    lang_files = collections.Counter()
    dir_lines = collections.Counter()
    dir_files = collections.Counter()
    sizes = []
    manifests, infra, ci, docs, entries, tests, generated = [], [], set(), [], [], set(), []
    test_files = 0
    data_dumps, routes = set(), []
    top_code, top_docs = collections.Counter(), collections.Counter()

    for f in files:
        full = os.path.join(root, f)
        if not os.path.isfile(full):
            continue
        base = os.path.basename(f)
        ext = os.path.splitext(f)[1].lower()
        top = f.split("/")[0] + "/" if "/" in f else None
        dm = DATA_DIR.search(f)
        if dm:
            data_dumps.add(f[: dm.end()])
            continue
        if top:
            if ext in LANGS:
                top_code[top] += 1
            elif ext in DOC_EXT:
                top_docs[top] += 1
        if ext in LANGS and ROUTE_FILE.search(f):
            routes.append(f)
        if base in MANIFESTS:
            manifests.append({"path": f, "type": MANIFESTS[base]})
        if base in INFRA:
            infra.append({"path": f, "type": INFRA[base]})
        for key, name in CI.items():
            if f == key or f.startswith(key + "/"):
                ci.add(name)
        if DOC_NAMES.search(f) or f.startswith("docs/") or f.startswith("doc/"):
            if ext in (".md", ".rst", ".txt", ".adoc", "") :
                docs.append(f)
        m = TEST_DIR.search(f)
        if m and ext in LANGS:
            tests.add(f[: m.end(2)] + "/")
        if TEST_FILE.search(f):
            test_files += 1
        if GENERATED.search(f):
            generated.append(f)
            continue
        lang = LANGS.get(ext)
        if not lang:
            continue
        n = count_lines(full)
        if n is None:
            continue
        langs[lang] += n
        lang_files[lang] += 1
        sizes.append((n, f))
        parts = f.split("/")
        for d in range(1, min(depth, len(parts) - 1) + 1):
            key = "/".join(parts[:d]) + "/"
            dir_lines[key] += n
            dir_files[key] += 1
        if len(parts) == 1:
            dir_lines["./"] += n
            dir_files["./"] += 1
        if ENTRY_NAMES.search(f):
            entries.append({"path": f, "reason": "file name"})
        elif n < 5000:
            head = read_head(full)
            for pat, why in ENTRY_PATTERNS:
                if pat.search(head):
                    entries.append({"path": f, "reason": why})
                    break

    for m in manifests:
        if os.path.basename(m["path"]) == "package.json":
            try:
                pkg = json.load(open(os.path.join(root, m["path"]), encoding="utf-8"))
                m["scripts"] = sorted((pkg.get("scripts") or {}).keys())
                deps = list((pkg.get("dependencies") or {}).keys())
                m["main_deps"] = deps[:25]
                for k in ("main", "bin", "module"):
                    if pkg.get(k):
                        entries.append({"path": f"{m['path']} → {k}", "reason": str(pkg[k])[:120]})
            except (OSError, ValueError):
                pass
        if os.path.basename(m["path"]) == "pyproject.toml":
            txt = read_head(os.path.join(root, m["path"]))
            sec = re.search(r"\[project\.scripts\]([^\[]*)", txt)
            if sec:
                m["scripts"] = [l.split("=")[0].strip() for l in sec.group(1).splitlines() if "=" in l]
        if os.path.basename(m["path"]) == "Makefile":
            txt = read_head(os.path.join(root, m["path"]))
            m["targets"] = sorted(set(re.findall(r"^([A-Za-z0-9_.-]+):(?!=)", txt, re.M)))[:40]

    hotspots, recent, head_sha = [], [], None
    if is_git:
        head_sha = (run(["git", "rev-parse", "--short", "HEAD"], root) or "").strip() or None
        log = run(["git", "log", "--since=12 months ago", "--name-only", "--pretty=format:"], root)
        if log:
            c = collections.Counter(l for l in log.splitlines() if l and os.path.splitext(l)[1].lower() in LANGS)
            hotspots = [{"path": p, "commits": n} for p, n in c.most_common(15) if os.path.exists(os.path.join(root, p))]
        rl = run(["git", "log", "-15", "--pretty=format:%h %ad %s", "--date=short"], root)
        recent = rl.splitlines() if rl else []

    total = sum(langs.values())
    return {
        "root": os.path.abspath(root),
        "git": is_git,
        "commit": head_sha,
        "files": len(files),
        "code_lines": total,
        "languages": [
            {"language": l, "lines": n, "files": lang_files[l], "pct": round(100 * n / total, 1) if total else 0}
            for l, n in langs.most_common()
        ],
        "manifests": manifests,
        "infra": infra,
        "ci": sorted(ci),
        "entry_points": entries[:40],
        "dirs": [
            {"path": d, "lines": dir_lines[d], "files": dir_files[d]}
            for d in sorted(dir_lines, key=lambda k: (-dir_lines[k]))
        ][:60],
        "tests": {"dirs": sorted(tests)[:30], "test_files": test_files},
        "existing_docs": sorted(docs)[:60],
        "largest": [{"path": f, "lines": n} for n, f in sorted(sizes, reverse=True)[:15]],
        "hotspots_12m": hotspots,
        "recent_commits": recent,
        "generated_excluded": generated[:30],
        "data_dumps_excluded": sorted(data_dumps)[:20],
        "docs_only_dirs": sorted(t for t in top_docs if not top_code[t] and t not in ("docs/", "doc/"))[:20],
        "route_files": sorted(routes, key=lambda x: ("Controller" in x, x))[:40],
    }


def print_human(r):
    p = print
    p(f"# Scan of {r['root']}")
    p(f"git: {'yes, commit ' + str(r['commit']) if r['git'] else 'no'} · files: {r['files']} · code lines: {r['code_lines']}\n")
    p("## Languages")
    for l in r["languages"][:12]:
        p(f"- {l['language']}: {l['lines']} lines ({l['pct']}%), {l['files']} files")
    p("\n## Manifests")
    for m in r["manifests"]:
        extra = ""
        if m.get("scripts"):
            extra += f" · scripts: {', '.join(m['scripts'][:15])}"
        if m.get("targets"):
            extra += f" · targets: {', '.join(m['targets'][:15])}"
        if m.get("main_deps"):
            extra += f" · deps: {', '.join(m['main_deps'][:12])}"
        p(f"- `{m['path']}` ({m['type']}){extra}")
    if r["infra"]:
        p("\n## Infra")
        for i in r["infra"]:
            p(f"- `{i['path']}` ({i['type']})")
    p(f"\n## CI: {', '.join(r['ci']) or 'none detected'}")
    p("\n## Candidate entry points")
    for e in r["entry_points"]:
        p(f"- `{e['path']}` — {e['reason']}")
    p("\n## Directories by weight (code lines)")
    for d in r["dirs"][:35]:
        p(f"- `{d['path']}` {d['lines']} lines, {d['files']} files")
    p(f"\n## Tests: {r['tests']['test_files']} test files")
    for t in r["tests"]["dirs"][:15]:
        p(f"- `{t}`")
    p("\n## Existing docs")
    for d in r["existing_docs"] or ["(none)"]:
        p(f"- `{d}`")
    if r["route_files"]:
        p("\n## Route / controller files (best map of what the app exposes)")
        for f in r["route_files"]:
            p(f"- `{f}`")
    if r["data_dumps_excluded"] or r["docs_only_dirs"]:
        p("\n## Probably out of scope")
        for d in r["data_dumps_excluded"]:
            p(f"- `{d}` — data dump/backup (not counted as code)")
        for d in r["docs_only_dirs"]:
            p(f"- `{d}` — only docs/notes, no code (planning, agent config?)")
    p("\n## Largest files")
    for f in r["largest"]:
        p(f"- `{f['path']}` {f['lines']} lines")
    if r["hotspots_12m"]:
        p("\n## Hotspots (commits, last 12 months)")
        for h in r["hotspots_12m"]:
            p(f"- `{h['path']}` {h['commits']}")
    if r["recent_commits"]:
        p("\n## Recent commits")
        for c in r["recent_commits"]:
            p(f"- {c}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--depth", type=int, default=4, help="directory aggregation depth")
    a = ap.parse_args()
    if not os.path.isdir(a.root):
        sys.exit(f"Not a directory: {a.root}")
    r = scan(a.root, a.depth)
    if a.json:
        json.dump(r, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print_human(r)


if __name__ == "__main__":
    main()
