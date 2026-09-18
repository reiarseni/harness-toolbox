#!/usr/bin/env python3
"""Static database-schema extractor for documake (stdlib only, no DB connection).

Usage: schema.py <root> [--json] [--core N] [--mermaid]

Reads table definitions and foreign keys from code: SQLAlchemy models (+ Alembic migrations as fallback),
Django models, Laravel migrations, Prisma schema, Rails schema.rb and SQL DDL. Then PROPOSES the core:
ranked tables, hub tables (e.g. tenants), groups, and a draft Mermaid erDiagram of <= 12 core tables.
The model decides the final core; check_docs.py verifies the written diagram against these edges.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan import list_files, DATA_DIR  # noqa: E402

NOISE = re.compile(
    r"^(migrations?|alembic_version|schema_migrations|ar_internal_metadata|failed_jobs|jobs|job_batches|cache|"
    r"cache_locks|sessions|password_resets?|password_reset_tokens|personal_access_tokens|telescope_.*|"
    r"django_(migrations|session|content_type|admin_log|site)|celery_.*|spatial_ref_sys|flyway_schema_history|"
    r"databasechangelog(lock)?|_prisma_migrations)$", re.I)
MAX_CORE = 12


# ---------- small parsing helpers ----------

def read(path, limit=3_000_000):
    try:
        if os.path.getsize(path) > limit:
            return ""
        return open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return ""


def match_close(s, i, php=False, sql=False):
    """Index of the bracket closing s[i] (one of ([{), skipping strings/comments; -1 if unbalanced."""
    opener = s[i]
    closer = {"(": ")", "[": "]", "{": "}"}[opener]
    depth, j, n, q = 0, i, len(s), None
    while j < n:
        c = s[j]
        if q:
            if c == "\\":
                j += 2
                continue
            if s.startswith(q, j):
                j += len(q)
                q = None
                continue
        else:
            if c in "'\"`" and not (sql and c == "`" and False):
                q = c * 3 if s.startswith(c * 3, j) else c
                j += len(q)
                continue
            if c == "#" and not sql or (php and s.startswith("//", j)) or (sql and s.startswith("--", j)):
                e = s.find("\n", j)
                j = n if e < 0 else e
                continue
            if c == opener:
                depth += 1
            elif c == closer:
                depth -= 1
                if depth == 0:
                    return j
        j += 1
    return -1


def split_top(s, sep=","):
    out, depth, q, cur, i = [], 0, None, [], 0
    while i < len(s):
        c = s[i]
        if q:
            cur.append(c)
            if c == "\\" and i + 1 < len(s):
                cur.append(s[i + 1])
                i += 2
                continue
            if c == q:
                q = None
        elif c in "'\"":
            q = c
            cur.append(c)
        elif c in "([{":
            depth += 1
            cur.append(c)
        elif c in ")]}":
            depth -= 1
            cur.append(c)
        elif c == sep and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    if "".join(cur).strip():
        out.append("".join(cur))
    return out


def plural(word):
    w = word.lower()
    if re.search(r"[^aeiou]y$", w):
        return w[:-1] + "ies"
    if re.search(r"(s|x|z|ch|sh)$", w):
        return w + "es"
    return w + "s"


def singular(word):
    w = word.lower()
    if w.endswith("ies"):
        return w[:-3] + "y"
    if w.endswith("ses") or w.endswith("xes"):
        return w[:-2]
    return w[:-1] if w.endswith("s") else w


class Schema:
    def __init__(self):
        self.tables = {}   # name -> {"source","file","columns":[...],"fks":[{col,ref,nullable,unique}]}
        self.sources = set()
        self.unsupported = []

    def table(self, name, source, file):
        t = self.tables.setdefault(name, {"source": source, "file": file, "columns": [], "fks": []})
        return t

    def add_col(self, name, col):
        t = self.tables[name]
        if col and col not in t["columns"] and len(t["columns"]) < 60:
            t["columns"].append(col)

    def add_fk(self, name, col, ref, nullable=None, unique=False):
        t = self.tables[name]
        if not any(f["col"] == col and f["ref"] == ref for f in t["fks"]):
            t["fks"].append({"col": col, "ref": ref, "nullable": nullable, "unique": unique})


# ---------- SQLAlchemy (+ Alembic fallback) ----------

def stmts_of_class(body):
    """Class-level statements of a Python class body, joined on one line."""
    lines = body.split("\n")
    base, depth, cur, out = None, 0, [], []
    for ln in lines:
        if not ln.strip() or ln.strip().startswith("#"):
            continue
        ind = len(ln) - len(ln.lstrip())
        if depth == 0:
            if base is None:
                base = ind
            if ind != base:
                continue
            if cur:
                out.append(" ".join(cur))
                cur = []
        cur.append(ln.strip())
        clean = re.sub(r"(\"[^\"]*\"|'[^']*')", "", ln)
        depth += sum(clean.count(c) for c in "([{") - sum(clean.count(c) for c in ")]}")
        depth = max(depth, 0)
    if cur:
        out.append(" ".join(cur))
    return out


def parse_sqlalchemy(root, files, sc):
    classes = {}
    for f in files:
        if not f.endswith(".py"):
            continue
        text = read(os.path.join(root, f))
        if "Column(" not in text and "mapped_column(" not in text and "Table(" not in text:
            continue
        for m in re.finditer(r"^class\s+(\w+)\s*(?:\(([^)]*)\))?\s*:", text, re.M):
            nl = text.find("\n", m.end())
            nxt = re.search(r"^\S", text[nl + 1:], re.M)
            body = text[nl + 1: nl + 1 + nxt.start()] if nxt else text[nl + 1:]
            info = {"bases": [b.strip().split(".")[-1] for b in (m.group(2) or "").split(",") if b.strip()],
                    "table": None, "cols": [], "file": f}
            for st in stmts_of_class(body):
                tn = re.match(r"__tablename__\s*=\s*['\"]([\w.]+)['\"]", st)
                if tn:
                    info["table"] = tn.group(1).split(".")[-1]
                    continue
                if st.startswith("__table_args__"):
                    for fm in re.finditer(r"ForeignKeyConstraint\(\s*\[([^\]]*)\]\s*,\s*\[([^\]]*)\]", st):
                        cols = re.findall(r"['\"](\w+)['\"]", fm.group(1))
                        refs = re.findall(r"['\"]([\w.]+)['\"]", fm.group(2))
                        for c, r in zip(cols, refs):
                            info["cols"].append({"name": c, "fk": [("t", r.split(".")[-2] if "." in r else r)],
                                                 "nullable": None, "unique": False, "pk": False})
                    continue
                cm = re.match(r"(\w+)\s*(?::\s*(.+?))?\s*=\s*(?:\w+\.)?(?:mapped_)?[Cc]olumn\((.*)\)\s*$", st)
                if not cm:
                    continue
                name, ann, args = cm.group(1), cm.group(2), cm.group(3)
                fks = [("t", x.split(".")[-2] if "." in x else x)
                       for x in re.findall(r"ForeignKey\(\s*['\"]([\w.]+)['\"]", args)]
                fks += [("c", a) for a in re.findall(r"ForeignKey\(\s*(\w+)\.\w+", args)]
                pk = bool(re.search(r"primary_key\s*=\s*True", args))
                nn = re.search(r"nullable\s*=\s*(True|False)", args)
                nullable = (nn.group(1) == "True") if nn else (False if pk else
                           (bool(re.search(r"\bNone\b|Optional\[", ann)) if ann and "Mapped" in ann else None))
                info["cols"].append({"name": name, "fk": fks, "nullable": nullable,
                                     "unique": pk or bool(re.search(r"unique\s*=\s*True", args)), "pk": pk})
            classes[m.group(1)] = info
        for tm in re.finditer(r"\bTable\(\s*['\"](\w+)['\"]", text):
            op = text.find("(", tm.start())
            end = match_close(text, op)
            if end < 0:
                continue
            name = tm.group(1)
            sc.table(name, "sqlalchemy", f)
            for part in split_top(text[op + 1:end])[2:]:
                cn = re.match(r"\s*(?:sa\.|sqlalchemy\.)?Column\(\s*['\"](\w+)['\"]", part)
                if cn:
                    sc.add_col(name, cn.group(1))
                    for r in re.findall(r"ForeignKey\(\s*['\"]([\w.]+)['\"]", part):
                        sc.add_fk(name, cn.group(1), r.split(".")[-2] if "." in r else r, False)
    if not classes:
        return
    table_of = {c: i["table"] for c, i in classes.items() if i["table"]}

    def inherited(cname, seen=()):
        info = classes.get(cname)
        if not info or cname in seen:
            return []
        cols = list(info["cols"])
        for b in info["bases"]:
            cols += inherited(b, seen + (cname,))
        return cols

    for cname, info in classes.items():
        if not info["table"]:
            continue
        sc.sources.add("sqlalchemy")
        sc.table(info["table"], "sqlalchemy", info["file"])
        for col in inherited(cname):
            sc.add_col(info["table"], col["name"])
            for kind, ref in col["fk"]:
                rt = ref if kind == "t" else table_of.get(ref)
                if rt:
                    sc.add_fk(info["table"], col["name"], rt, col["nullable"], col["unique"])


def parse_alembic(root, files, sc):
    """Fallback when there are no SQLAlchemy models: replay create/drop/add in file order (approximate)."""
    migs = sorted(f for f in files if re.search(r"(alembic|migrations)/versions/[^/]+\.py$", f))
    for f in migs:
        text = read(os.path.join(root, f))
        um = re.search(r"def upgrade\([^)]*\)[^:]*:", text)
        if not um:
            continue
        nxt = re.search(r"^def \w+", text[um.end():], re.M)
        body = text[um.end(): um.end() + nxt.start()] if nxt else text[um.end():]
        for m in re.finditer(r"op\.create_table\(\s*['\"](\w+)['\"]", body):
            op_ = body.find("(", m.start())
            end = match_close(body, op_)
            if end < 0:
                continue
            name = m.group(1)
            sc.sources.add("alembic")
            sc.table(name, "alembic", f)
            for part in split_top(body[op_ + 1:end])[1:]:
                cm = re.match(r"\s*sa\.Column\(\s*['\"](\w+)['\"]", part)
                if cm:
                    sc.add_col(name, cm.group(1))
                    nn = re.search(r"nullable\s*=\s*(True|False)", part)
                    for r in re.findall(r"ForeignKey\(\s*['\"]([\w.]+)['\"]", part):
                        sc.add_fk(name, cm.group(1), r.split(".")[-2] if "." in r else r,
                                  (nn.group(1) == "True") if nn else None)
                fk = re.match(r"\s*sa\.ForeignKeyConstraint\(\s*\[([^\]]*)\]\s*,\s*\[([^\]]*)\]", part)
                if fk:
                    for c, r in zip(re.findall(r"['\"](\w+)['\"]", fk.group(1)), re.findall(r"['\"]([\w.]+)['\"]", fk.group(2))):
                        sc.add_fk(name, c, r.split(".")[-2] if "." in r else r)
        for m in re.finditer(r"op\.create_foreign_key\(\s*[^,]*,\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]\s*,\s*\[([^\]]*)\]", body):
            if m.group(1) in sc.tables:
                for c in re.findall(r"['\"](\w+)['\"]", m.group(3)):
                    sc.add_fk(m.group(1), c, m.group(2))
        for m in re.finditer(r"op\.add_column\(\s*['\"](\w+)['\"]\s*,\s*sa\.Column\(\s*['\"](\w+)['\"](.*?)\)\s*\)", body, re.S):
            if m.group(1) in sc.tables:
                sc.add_col(m.group(1), m.group(2))
                for r in re.findall(r"ForeignKey\(\s*['\"]([\w.]+)['\"]", m.group(3)):
                    sc.add_fk(m.group(1), m.group(2), r.split(".")[-2] if "." in r else r)
        for m in re.finditer(r"op\.drop_table\(\s*['\"](\w+)['\"]", body):
            sc.tables.pop(m.group(1), None)


# ---------- Django ----------

def parse_django(root, files, sc):
    cls_table, pending = {}, []
    for f in files:
        if not f.endswith(".py") or not re.search(r"(^|/)models(\.py|/[^/]+\.py)$", f):
            continue
        text = read(os.path.join(root, f))
        if "models." not in text:
            continue
        parts = f.split("/")
        app = parts[-2] if parts[-1] == "models.py" else (parts[-3] if len(parts) > 2 else parts[-2])
        for m in re.finditer(r"^class\s+(\w+)\s*\(([^)]*)\)\s*:", text, re.M):
            if not re.search(r"\bModel\b|models\.Model", m.group(2)):
                continue
            nl = text.find("\n", m.end())
            nxt = re.search(r"^\S", text[nl + 1:], re.M)
            body = text[nl + 1: nl + 1 + nxt.start()] if nxt else text[nl + 1:]
            if re.search(r"abstract\s*=\s*True", body):
                continue
            dbt = re.search(r"db_table\s*=\s*['\"](\w+)['\"]", body)
            tname = dbt.group(1) if dbt else f"{app}_{m.group(1).lower()}"
            cls_table[(app, m.group(1))] = tname
            cls_table.setdefault((None, m.group(1)), tname)
            sc.sources.add("django")
            sc.table(tname, "django", f)
            for st in stmts_of_class(body):
                fm = re.match(r"(\w+)\s*=\s*models\.(ForeignKey|OneToOneField|ManyToManyField)\(\s*['\"]?([\w.]+)['\"]?(.*)\)\s*$", st)
                if fm:
                    pending.append((tname, app, fm.group(1), fm.group(2), fm.group(3), fm.group(4)))
                    continue
                cm = re.match(r"(\w+)\s*=\s*models\.\w+Field\(", st)
                if cm:
                    sc.add_col(tname, cm.group(1))
    for tname, app, field, kind, target, rest in pending:
        tgt = target.split(".")
        ref = (cls_table.get((tgt[0], tgt[1])) if len(tgt) == 2 else
               tname if target == "self" else cls_table.get((app, target)) or cls_table.get((None, target)))
        if not ref:
            continue
        null = bool(re.search(r"null\s*=\s*True", rest))
        if kind == "ManyToManyField":
            assoc = f"{tname}_{field}"
            sc.table(assoc, "django", sc.tables[tname]["file"])
            sc.add_fk(assoc, singular(tname.split("_", 1)[-1]) + "_id", tname, False)
            sc.add_fk(assoc, singular(ref.split("_", 1)[-1]) + "_id", ref, False)
        else:
            sc.add_col(tname, field + "_id")
            sc.add_fk(tname, field + "_id", ref, null, kind == "OneToOneField")


# ---------- Laravel migrations ----------

def parse_laravel(root, files, sc):
    migs = sorted(f for f in files if re.search(r"database/migrations/.+\.php$", f))
    for f in migs:
        text = read(os.path.join(root, f))
        um = re.search(r"function\s+up\s*\([^)]*\)[^{]*\{", text)
        if not um:
            continue
        end = match_close(text, um.end() - 1, php=True)
        up = text[um.end(): end if end > 0 else len(text)]
        for m in re.finditer(r"Schema::(create|table)\(\s*['\"](\w+)['\"]\s*,\s*function\s*\(([^)]*)\)\s*(?:use\s*\([^)]*\)\s*)?\{", up):
            name, kind = m.group(2), m.group(1)
            cend = match_close(up, m.end() - 1, php=True)
            closure = up[m.end(): cend if cend > 0 else len(up)]
            var = re.search(r"\$(\w+)\s*$", m.group(3))
            if kind == "create":
                sc.table(name, "laravel", f)
            elif name not in sc.tables:
                continue
            sc.sources.add("laravel")
            for st in re.split(r";\s*(?:\n|$)", closure):
                st = re.sub(r"\s+", " ", st.strip())
                if not st.startswith("$"):
                    continue
                fm = re.match(r"\$\w+->(foreignId|foreignUuid|foreignUlid)\(\s*['\"](\w+)['\"]\s*\)(.*)", st)
                if fm:
                    col, chain = fm.group(2), fm.group(3)
                    sc.add_col(name, col)
                    cm = re.search(r"constrained\(\s*(?:['\"](\w+)['\"])?", chain)
                    if cm:
                        ref = cm.group(1) or plural(re.sub(r"_id$", "", col))
                        sc.add_fk(name, col, ref, "nullable()" in chain, "unique()" in chain)
                    continue
                fk = re.match(r"\$\w+->foreign\(\s*['\"](\w+)['\"]\s*\)\s*->\s*references\(\s*['\"](\w+)['\"]\s*\)\s*->\s*on\(\s*['\"](\w+)['\"]", st)
                if fk:
                    sc.add_fk(name, fk.group(1), fk.group(3))
                    continue
                cm = re.match(r"\$\w+->(\w+)\(\s*['\"](\w+)['\"]", st)
                if cm and not cm.group(1).startswith(("drop", "index", "unique", "primary", "rename", "foreign")):
                    sc.add_col(name, cm.group(2))
        for m in re.finditer(r"Schema::(?:dropIfExists|drop)\(\s*['\"](\w+)['\"]", up):
            sc.tables.pop(m.group(1), None)


# ---------- Prisma ----------

def parse_prisma(root, files, sc):
    for f in files:
        if not f.endswith(".prisma"):
            continue
        text = read(os.path.join(root, f))
        models = {}
        for m in re.finditer(r"^model\s+(\w+)\s*\{(.*?)^\}", text, re.M | re.S):
            mp = re.search(r"@@map\(\s*['\"](\w+)['\"]", m.group(2))
            models[m.group(1)] = (mp.group(1) if mp else m.group(1), m.group(2))
        for mname, (tname, body) in models.items():
            sc.sources.add("prisma")
            sc.table(tname, "prisma", f)
            for ln in body.split("\n"):
                fm = re.match(r"\s*(\w+)\s+(\w+)(\[\])?(\?)?\s*(.*)", ln)
                if not fm or ln.strip().startswith(("@@", "//")):
                    continue
                fname, ftype, lst, opt, rest = fm.groups()
                if ftype in models:
                    rel = re.search(r"@relation\((?:[^)]*?)fields:\s*\[([^\]]*)\]", rest)
                    if rel:
                        for c in re.findall(r"\w+", rel.group(1)):
                            sc.add_fk(tname, c, models[ftype][0], bool(opt))
                else:
                    sc.add_col(tname, fname)


# ---------- SQL DDL ----------

def parse_sql(root, files, sc):
    for f in files:
        if not f.endswith(".sql") or DATA_DIR.search(f):
            continue
        text = read(os.path.join(root, f))
        if "CREATE TABLE" not in text.upper():
            continue
        ident = r"[`\"\[]?(\w+)[`\"\]]?"
        for m in re.finditer(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:[`\"\[]?\w+[`\"\]]?\.)?" + ident + r"\s*\(", text, re.I):
            op = m.end() - 1
            end = match_close(text, op, sql=True)
            if end < 0:
                continue
            name = m.group(1)
            sc.sources.add("sql")
            sc.table(name, "sql", f)
            for item in split_top(text[op + 1:end]):
                it = re.sub(r"\s+", " ", item.strip())
                fk = re.search(r"FOREIGN KEY\s*\(([^)]*)\)\s*REFERENCES\s+(?:[`\"\[]?\w+[`\"\]]?\.)?[`\"\[]?(\w+)", it, re.I)
                if fk:
                    for c in re.findall(r"\w+", fk.group(1)):
                        sc.add_fk(name, c, fk.group(2))
                    continue
                cm = re.match(r"[`\"\[]?(\w+)[`\"\]]?\s+\w+", it)
                if cm and cm.group(1).upper() not in ("PRIMARY", "UNIQUE", "CONSTRAINT", "KEY", "INDEX", "CHECK"):
                    sc.add_col(name, cm.group(1))
                    rf = re.search(r"REFERENCES\s+(?:[`\"\[]?\w+[`\"\]]?\.)?[`\"\[]?(\w+)", it, re.I)
                    if rf:
                        sc.add_fk(name, cm.group(1), rf.group(1), "NOT NULL" not in it.upper())
        for m in re.finditer(r"ALTER\s+TABLE\s+(?:ONLY\s+)?(?:[`\"\[]?\w+[`\"\]]?\.)?" + ident +
                             r"\s+ADD\s+(?:CONSTRAINT\s+\S+\s+)?FOREIGN KEY\s*\(([^)]*)\)\s*REFERENCES\s+(?:[`\"\[]?\w+[`\"\]]?\.)?[`\"\[]?(\w+)", text, re.I):
            if m.group(1) in sc.tables:
                for c in re.findall(r"\w+", m.group(2)):
                    sc.add_fk(m.group(1), c, m.group(3))


# ---------- Rails ----------

def parse_rails(root, files, sc):
    for f in files:
        if not f.endswith("db/schema.rb"):
            continue
        text = read(os.path.join(root, f))
        for m in re.finditer(r"create_table\s+['\"](\w+)['\"].*?\n(.*?)\n\s*end\b", text, re.S):
            sc.sources.add("rails")
            sc.table(m.group(1), "rails", f)
            for cm in re.finditer(r"t\.\w+\s+['\"](\w+)['\"]", m.group(2)):
                sc.add_col(m.group(1), cm.group(1))
        for m in re.finditer(r"add_foreign_key\s+['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"](?:.*?column:\s*['\"](\w+)['\"])?", text):
            if m.group(1) in sc.tables:
                sc.add_fk(m.group(1), m.group(3) or singular(m.group(2)) + "_id", m.group(2))


# ---------- orchestration and analysis ----------

def extract(root):
    files, _ = list_files(root)
    files = [f for f in files if not DATA_DIR.search(f)]
    sc = Schema()
    parse_sqlalchemy(root, files, sc)
    if "sqlalchemy" not in sc.sources:
        parse_alembic(root, files, sc)
    for p in (parse_django, parse_laravel, parse_prisma, parse_rails, parse_sql):
        p(root, files, sc)
    hints = {"typeorm/jpa entities": r"@Entity\b", "sequelize models": r"sequelize\.define|extends Model\b",
             "mongoose schemas": r"new (mongoose\.)?Schema\("}
    if not sc.tables:
        for label, rx in hints.items():
            n = sum(1 for f in files if f.endswith((".ts", ".js", ".java", ".kt")) and re.search(rx, read(os.path.join(root, f), 300_000)))
            if n:
                sc.unsupported.append(f"{label} in {n} file(s): not parsed, read the entity files by hand")
    return sc


def analyze(sc, core=10):
    t = sc.tables
    edges, logical = [], []
    for name, info in t.items():
        for fk in info["fks"]:
            edges.append({"child": name, "col": fk["col"], "parent": fk["ref"], "nullable": fk["nullable"],
                          "unique": fk["unique"], "parent_known": fk["ref"] in t})
        fk_cols = {fk["col"] for fk in info["fks"]}
        for col in info["columns"]:
            m = re.fullmatch(r"(\w+?)_id", col)
            if m and col not in fk_cols:
                for cand in (plural(m.group(1)), m.group(1)):
                    if cand in t and cand != name:
                        logical.append({"child": name, "col": col, "parent": cand})
                        break
    known = [e for e in edges if e["parent_known"] and e["child"] != e["parent"]]
    indeg, outdeg = {}, {}
    for e in known:
        indeg[e["parent"]] = indeg.get(e["parent"], 0) + 1
        outdeg[e["child"]] = outdeg.get(e["child"], 0) + 1
    assoc = set()
    for name, info in t.items():
        fkc = {f["col"] for f in info["fks"]}
        rest = [c for c in info["columns"] if c not in fkc and c not in ("id", "created_at", "updated_at", "deleted_at")]
        if len(fkc) >= 2 and not rest and len({f["ref"] for f in info["fks"]}) >= 2:
            assoc.add(name)                      # M:N needs >= 2 DISTINCT parents (author/editor -> same table is not M:N)
    noise = {n for n in t if NOISE.match(n)}

    def score(n):
        return 2 * indeg.get(n, 0) + outdeg.get(n, 0)

    ranked = sorted((n for n in t if n not in noise), key=lambda n: (-score(n), n))
    n_tables = len([n for n in t if n not in noise])
    hubs = [n for n in ranked if indeg.get(n, 0) >= max(3, 0.35 * n_tables)]
    chosen = [n for n in ranked if n not in assoc][:max(core, 1)]
    nb = {}
    for e in known:
        nb.setdefault(e["child"], set()).add(e["parent"])
        nb.setdefault(e["parent"], set()).add(e["child"])
    while len(chosen) < MAX_CORE:
        cands = sorted((n for n in ranked if n not in chosen and len(nb.get(n, set()) & set(chosen)) >= 2), key=lambda n: (-score(n), n))
        if not cands:
            break
        chosen.append(cands[0])
    # groups: connected components without hubs/noise
    rest = [n for n in t if n not in noise and n not in hubs]
    seen, groups = set(), []
    for n in rest:
        if n in seen:
            continue
        comp, stack = set(), [n]
        while stack:
            x = stack.pop()
            if x in comp:
                continue
            comp.add(x)
            stack += [y for y in nb.get(x, set()) if y in rest and y not in comp]
        seen |= comp
        if len(comp) >= 2:
            lead = sorted(comp, key=lambda c: (-score(c), c))[0]
            groups.append({"name": lead, "tables": sorted(comp, key=lambda c: (-score(c), c)),
                           "hub_refs": sorted({e["parent"] for e in known if e["child"] in comp and e["parent"] in hubs})})
    groups.sort(key=lambda g: -len(g["tables"]))
    return {"tables": len(t), "edges": edges, "logical_edges": logical, "ranked": ranked, "hubs": hubs,
            "assoc": sorted(assoc), "noise": sorted(noise), "core": chosen, "groups": groups,
            "scores": {n: score(n) for n in ranked}}


def er_draft(sc, an, names):
    ent = lambda n: n if re.fullmatch(r"\w+", n) else f'"{n}"'
    hubs = set(an["hubs"])
    names = set(names) - hubs                     # hubs are referenced by everything: say it once, don't draw it
    lines = ["erDiagram"]
    for h in an["hubs"]:
        n = sum(1 for e in an["edges"] if e["parent"] == h)
        lines.append(f"    %% hub not drawn: {h} is referenced by {n} tables (usually via {h[:-1]}_id)")
    used = set()
    for e in an["edges"]:
        if not e["parent_known"] or e["child"] not in names or e["parent"] not in names or e["child"] in an["assoc"]:
            continue
        left = "||" if e["nullable"] is not True else "|o"
        right = "o{" if not e["unique"] else "o|"
        lines.append(f'    {ent(e["parent"])} {left}--{right} {ent(e["child"])} : "{e["col"]}"')
        used |= {e["parent"], e["child"]}
    for a in an["assoc"]:
        ps = [f["ref"] for f in sc.tables[a]["fks"] if f["ref"] in names]
        if len(ps) >= 2:
            lines.append(f'    {ent(ps[0])} }}o--o{{ {ent(ps[1])} : "M:N via {a}"')
            used |= set(ps[:2])
    for n in sorted(names - used):
        lines.append(f"    {ent(n)}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--core", type=int, default=10, help="how many top tables seed the core proposal")
    ap.add_argument("--mermaid", action="store_true", help="print only the draft erDiagram of the core")
    a = ap.parse_args()
    sc = extract(os.path.abspath(a.root))
    an = analyze(sc, a.core)
    draft = er_draft(sc, an, an["core"])
    if a.json:
        json.dump({"sources": sorted(sc.sources), "unsupported": sc.unsupported, **an,
                   "table_info": sc.tables, "draft_mermaid": draft}, sys.stdout, ensure_ascii=False, indent=1)
        print()
        return
    if a.mermaid:
        print(draft)
        return
    if not sc.tables:
        print("No tables found." + ("\n" + "\n".join(sc.unsupported) if sc.unsupported else ""))
        return
    print(f"# Schema of {os.path.abspath(a.root)}")
    print(f"sources: {', '.join(sorted(sc.sources))} · tables: {an['tables']} · FK edges: {len(an['edges'])} · "
          f"logical (no FK): {len(an['logical_edges'])}\n")
    print("## Hubs (referenced by many tables: show them once, don't draw every arrow)")
    print("- " + (", ".join(an["hubs"]) or "(none)"))
    print("\n## Ranking (2*referenced_by + references)")
    for n in an["ranked"][:20]:
        print(f"- {n}: {an['scores'][n]}  ← {sc.tables[n]['file']}")
    print("\n## Proposed core (<= 12)\n- " + ", ".join(an["core"]))
    print("\n## Groups (connected without hubs)")
    for g in an["groups"][:10]:
        print(f"- {g['name']}: {', '.join(g['tables'])}" + (f"  [refs hubs: {', '.join(g['hub_refs'])}]" if g["hub_refs"] else ""))
    if an["assoc"]:
        print("\n## Association tables (M:N)\n- " + ", ".join(an["assoc"]))
    if an["noise"]:
        print("\n## Framework/infra tables (ignore)\n- " + ", ".join(an["noise"]))
    if an["logical_edges"]:
        print("\n## Logical relations (column looks like a FK but has no constraint; label them 'logical' if drawn)")
        for e in an["logical_edges"][:15]:
            print(f"- {e['child']}.{e['col']} → {e['parent']}")
    print("\n## Draft erDiagram of the proposed core (edit it: rename groups, drop noise, keep <= 12 tables)")
    print("```mermaid\n" + draft + "\n```")
    for u in sc.unsupported:
        print("\nNOTE:", u)


if __name__ == "__main__":
    main()
