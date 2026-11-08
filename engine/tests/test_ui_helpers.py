import os
import py_compile
import tempfile
import unittest
from pathlib import Path

from bootforge_ui.backend import OFFICIAL_CATALOG
from bootforge_ui.theme import esc, format_bytes, format_speed, load_prefs, save_prefs


class BootForgeUIHelpersTests(unittest.TestCase):
    def test_markup_escape_and_formatting(self):
        self.assertEqual(esc("Windows 11 & Linux <x86_64>"), "Windows 11 &amp; Linux &lt;x86_64&gt;")
        self.assertEqual(format_bytes(0), "0 o")
        self.assertEqual(format_bytes(512), "512 o")
        self.assertEqual(format_bytes(5 * 1024**3), "5.00 Go")
        self.assertEqual(format_speed(25.5 * 1024**2), "25.5 Mo/s")
        self.assertEqual(format_speed(None), "")

    def test_prefs_persistence_defaults_to_simple(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_cfg = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = tmp
            try:
                self.assertEqual(load_prefs(), {"mode": "simple"})
                save_prefs({"mode": "advanced"})
                self.assertEqual(load_prefs().get("mode"), "advanced")
            finally:
                if old_cfg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = old_cfg

    def test_catalog_entries_valid_https_and_simple_fields(self):
        self.assertGreaterEqual(len(OFFICIAL_CATALOG), 5)
        for item in OFFICIAL_CATALOG:
            self.assertTrue(item["url"].startswith("https://"))
            self.assertTrue(item["checksum_page"].startswith("https://"))
            self.assertTrue(item.get("simple_category"))
            self.assertTrue(item.get("simple_description"))

    def test_python_sources_compile_cleanly(self):
        root = Path(__file__).resolve().parents[2]
        for folder in ("bootforge_ui", "bootforge_win", "engine", "packaging"):
            for py_file in (root / folder).rglob("*.py"):
                py_compile.compile(str(py_file), doraise=True)


if __name__ == "__main__":
    unittest.main()
