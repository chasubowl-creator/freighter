"""The version number is the same everywhere. Run: python3 -m unittest discover -s tests -v"""
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from freighter_version import __version__  # noqa: E402


class Version(unittest.TestCase):
    def test_looks_like_a_version(self):
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")

    def test_changelog_has_this_version(self):
        first = re.search(r"^## (\S+)", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)
        self.assertEqual(first.group(1), __version__, "add a CHANGELOG.md entry for the new version")

    def test_every_tool_reports_it(self):
        for script, name in (("freighter.py", "Freighter"), ("manifest.py", "_Manifest"), ("crate.py", "_Crate")):
            out = subprocess.run([sys.executable, str(ROOT / script), "--version"], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout.strip(), f"{name} {__version__}")


if __name__ == "__main__":
    unittest.main()
