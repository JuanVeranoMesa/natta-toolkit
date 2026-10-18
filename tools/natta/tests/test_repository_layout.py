"""Separate tools under tools/ follow the documented layout (docs/EXTENDING.md)."""
import ast
from pathlib import Path
import subprocess
import unittest

import natta

TOOLS = natta.ROOT.parent


def separate_tools():
    return sorted(p for p in TOOLS.iterdir() if p.is_dir() and p != natta.ROOT and not p.name.startswith(('.', '__')))


class RepositoryLayoutTests(unittest.TestCase):
    def test_separate_tools_document_and_lock_their_dependencies(self):
        self.assertTrue(separate_tools())
        for tool in separate_tools():
            with self.subTest(tool=tool.name):
                self.assertTrue((tool / 'README.md').is_file())
                if (tool / 'pyproject.toml').exists():
                    self.assertTrue((tool / 'uv.lock').is_file())
                for unwanted in ('.venv', 'venv', 'models'):
                    self.assertFalse((tool / unwanted).is_dir() and not self.ignored(tool / unwanted))

    def ignored(self, path):
        proc = subprocess.run(['git', '-C', str(natta.ROOT), 'check-ignore', '-q', str(path)],
                              env=natta.git_environment())
        return proc.returncode == 0

    def test_core_imports_no_separate_tool(self):
        tool_modules = {p.stem for tool in separate_tools() for p in tool.glob('*.py')}
        core_modules = {p.stem for p in natta.ROOT.glob('*.py')}
        for source in natta.ROOT.glob('*.py'):
            tree = ast.parse(source.read_text())
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported |= {alias.name.split('.')[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split('.')[0])
            with self.subTest(source=source.name):
                self.assertFalse((imported & tool_modules) - core_modules)
                self.assertNotIn('sys.path', source.read_text())


if __name__ == '__main__':
    unittest.main()
