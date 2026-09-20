"""Tests for the gitlab-wiki target of publish.py. Run: python3 -m unittest discover -s .documake"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import publish  # noqa: E402

FILES = {
    "README.md": "# Facecheck — documentación para quien modifica\n\nIntro con [visión](03-vision-general.md#que) y [ext](https://x.io/a.md).\n",
    "03-vision-general.md": "# Visión general\n\nVer [admin](08-modulos/admin-web.md#rutas) y [código](../backend/app.py) y [local](#aqui).\n",
    "07-flujos/marca-async.md": "# Flujo: marca y verificación asíncrona\n\nTexto.\n",
    "08-modulos/admin-web.md": "# Admin web\n\n" + "".join(f"## S{i}\n\ntexto\n\n" for i in range(4)),
    "_meta/cobertura.md": "# Cobertura\n\nok\n",
    "_meta/documake.json": "{}",
}


def make_docs(root):
    for rel, body in FILES.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w", encoding="utf-8").write(body)


def make_wiki(root):
    subprocess.run(["git", "init", "-q", root], check=True)
    return root


class RenameTests(unittest.TestCase):
    def test_slugs(self):
        s = publish.wiki_slug
        self.assertEqual(s("README.md"), "Home")
        self.assertEqual(s("08-modulos/admin-web.md"), "Modulos/Admin-web")
        self.assertEqual(s("03-vision-general.md"), "Vision-general")
        self.assertEqual(s("_meta/cobertura.md"), "Meta/Cobertura")
        self.assertEqual(s("07-flujos/marca-async.md"), "Flujos/Marca-async")


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.mapping = {"03-vision-general.md": "Documentacion/Vision-general.md",
                        "08-modulos/admin-web.md": "Documentacion/Modulos/Admin-web.md"}

    def test_strips_md_keeps_anchor(self):
        out = publish.rewrite_wiki_links("[a](08-modulos/admin-web.md#rutas)", "03-vision-general.md", self.mapping)
        self.assertEqual(out, "[a](/Documentacion/Modulos/Admin-web#rutas)")

    def test_relative_from_subdir(self):
        out = publish.rewrite_wiki_links("[a](../03-vision-general.md)", "08-modulos/admin-web.md", self.mapping)
        self.assertEqual(out, "[a](/Documentacion/Vision-general)")

    def test_leaves_external_anchor_and_unknown(self):
        text = "[e](https://x.io/a.md) [l](#aqui) [c](../backend/app.py)"
        self.assertEqual(publish.rewrite_wiki_links(text, "03-vision-general.md", self.mapping), text)


class PageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.docs = self.tmp.name
        make_docs(self.docs)
        self.nav = {"titles": {"README.md": "Facecheck"}}
        publish.LANG = "es"
        self.pages, self.mapping = publish.wiki_pages(self.docs, "docs/sistema", "Documentacion",
                                                      "https://g/-/blob/main", self.nav)

    def tearDown(self):
        self.tmp.cleanup()

    def test_paths(self):
        self.assertEqual(sorted(self.pages), [
            "Documentacion/Flujos/Marca-async.md", "Documentacion/Home.md",
            "Documentacion/Meta/Cobertura.md", "Documentacion/Modulos/Admin-web.md",
            "Documentacion/Vision-general.md"])

    def test_front_matter_and_h1_removed(self):
        page = self.pages["Documentacion/Vision-general.md"]
        self.assertTrue(page.startswith('---\ntitle: "Visión general"\n---\n'))
        self.assertNotIn("\n# Visión general", page)

    def test_home_title_override(self):
        home = self.pages["Documentacion/Home.md"]
        self.assertTrue(home.startswith('---\ntitle: "Facecheck"\n---\n'))
        self.assertNotIn("documentación para quien modifica", home.split("<!--", 1)[0])

    def test_links_and_footer(self):
        page = self.pages["Documentacion/Vision-general.md"]
        self.assertIn("(/Documentacion/Modulos/Admin-web#rutas)", page)
        self.assertNotIn(".md#", page.split("---", 2)[2].split("Generado")[0])
        self.assertIn("← [Inicio](/Documentacion/Home) · [Índice](/Documentacion/Home) · "
                      "[Marca y verificación asíncrona](/Documentacion/Flujos/Marca-async) →", page)
        self.assertIn("Generado desde [`docs/sistema/03-vision-general.md`](https://g/-/blob/main/docs/sistema/03-vision-general.md)", page)

    def test_marker_found_after_front_matter(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
            fh.write(self.pages["Documentacion/Home.md"])
        try:
            self.assertTrue(publish.is_generated(fh.name))
        finally:
            os.unlink(fh.name)

    def test_toc_only_on_long_pages(self):
        self.assertIn("[[_TOC_]]", self.pages["Documentacion/Modulos/Admin-web.md"])
        self.assertNotIn("[[_TOC_]]", self.pages["Documentacion/Flujos/Marca-async.md"])

    def test_collision(self):
        open(os.path.join(self.docs, "09-vision-general.md"), "w").write("# Otra\n")
        with self.assertRaises(SystemExit):
            publish.wiki_pages(self.docs, "docs/sistema", "Documentacion", "", self.nav)


class SidebarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        make_docs(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def render(self, nav, details=True):
        entries = publish.nav_entries(self.tmp.name, nav)
        return publish.wiki_sidebar(entries, lambda r: "/" + publish.wiki_slug(r), "Docs", details)

    def test_labels_without_group_prefix(self):
        out = self.render({})
        self.assertIn("[Marca y verificación asíncrona](/Flujos/Marca-async)", out)
        self.assertNotIn("Flujo:", out)
        self.assertIn("[Inicio](/Home)", out)

    def test_groups_are_details(self):
        out = self.render({})
        self.assertIn("<summary><b>Flujos</b></summary>", out)
        self.assertIn("<summary><b>Módulos</b></summary>", out)

    def test_flat_fallback(self):
        out = self.render({}, details=False)
        self.assertNotIn("<details>", out)
        self.assertIn("- **Flujos**", out)

    def test_overrides(self):
        out = self.render({"labels": {"08-modulos/admin-web.md": "Panel"}, "groups": {"08-modulos": "Piezas"}})
        self.assertIn("[Panel](/Modulos/Admin-web)", out)
        self.assertIn("<summary><b>Piezas</b></summary>", out)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.docs = os.path.join(self.tmp.name, "docs")
        self.wiki = make_wiki(os.path.join(self.tmp.name, "wiki"))
        make_docs(self.docs)
        publish.LANG, publish.TITLE = "es", "T"

    def tearDown(self):
        self.tmp.cleanup()

    def run_sync(self, force=False):
        publish.gitlab_section(self.docs, "docs/sistema", self.wiki, "Documentacion", "", "merge",
                               False, force, nav={}, legacy=["documentacion-del-proyecto"])

    def write_wiki(self, rel, body):
        path = os.path.join(self.wiki, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w", encoding="utf-8").write(body)
        return path

    def test_migrates_legacy_generated_pages(self):
        old = self.write_wiki("documentacion-del-proyecto/home.md", "<!-- documake:generated source=x -->\nold\n")
        self.run_sync()
        self.assertFalse(os.path.exists(old))
        self.assertFalse(os.path.isdir(os.path.join(self.wiki, "documentacion-del-proyecto")))
        self.assertTrue(os.path.isfile(os.path.join(self.wiki, "Documentacion", "Home.md")))

    def test_aborts_on_hand_edited_legacy_page(self):
        self.write_wiki("documentacion-del-proyecto/notas.md", "escrito a mano\n")
        with self.assertRaises(SystemExit):
            self.run_sync()

    def test_idempotent(self):
        self.run_sync()
        before = open(os.path.join(self.wiki, "Documentacion", "Home.md"), encoding="utf-8").read()
        self.run_sync()
        after = open(os.path.join(self.wiki, "Documentacion", "Home.md"), encoding="utf-8").read()
        self.assertEqual(before, after)

    def test_sidebar_block_replaced_rest_kept(self):
        self.write_wiki("_sidebar.md", "Mío\n\n" + publish.SB_START + "\nviejo\n" + publish.SB_END + "\n")
        self.run_sync()
        sb = open(os.path.join(self.wiki, "_sidebar.md"), encoding="utf-8").read()
        self.assertTrue(sb.startswith("Mío"))
        self.assertNotIn("viejo", sb)
        self.assertIn("[Inicio](/Documentacion/Home)", sb)


if __name__ == "__main__":
    unittest.main()
