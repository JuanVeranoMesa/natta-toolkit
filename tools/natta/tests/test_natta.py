import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import natta


class HarnessTests(unittest.TestCase):
    def setUp(self):
        router_checks = patch.object(natta.routing, "doctor_checks", return_value=[(True, "Semantic router fixture ready")])
        router_checks.start()
        self.addCleanup(router_checks.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "app with spaces"
        self.repo.mkdir()
        self.run_git("init", "-b", "main")
        self.registry = self.root / "projects.toml"
        self.write_registry()
        self.project = natta.load_registry(self.registry)[0]

    def run_git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], stderr=subprocess.STDOUT).decode()

    def write_registry(self, extra="", path="app with spaces", kind="generic-git"):
        self.registry.write_text(f'''schema_version = 1
[[projects]]
alias = "app"
aliases = ["example"]
name = "Example App"
path = "{path}"
type = "{kind}"
{extra}
''')

    def commit(self):
        self.run_git("add", ".")
        self.run_git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-m", "Fixture")

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = natta.main(["--registry", str(self.registry), *args])
        return code, out.getvalue(), err.getvalue()

    def test_registry_lookup_relative_path(self):
        self.assertEqual(self.project.path, self.repo)
        self.assertIs(natta.lookup([self.project], "example"), self.project)
        with self.assertRaisesRegex(natta.NattaError, "Unknown project"):
            natta.lookup([self.project], "missing")

    def test_invalid_registry(self):
        for content in ("not toml!", "schema_version = 2\nprojects = []", "schema_version = 1\nprojects = []"):
            with self.subTest(content=content):
                self.registry.write_text(content)
                self.assertEqual(self.invoke("doctor")[0], 1)

    def test_duplicate_aliases(self):
        with self.registry.open("a") as file:
            file.write('[[projects]]\nalias="example"\nname="Other"\npath="other"\ntype="generic-git"\n')
        with self.assertRaisesRegex(natta.NattaError, "Duplicate alias"):
            natta.load_registry(self.registry)

    def test_schema_validation(self):
        for extra in ('unknown = "value"', 'agents = 12', 'agents = "../outside"', 'version_source = "../outside"'):
            self.write_registry(extra)
            with self.subTest(extra=extra), self.assertRaises(natta.NattaError):
                natta.load_registry(self.registry)
        self.registry.write_text(self.registry.read_text().replace('aliases = ["example"]', 'aliases = "example"'))
        with self.assertRaises(natta.NattaError):
            natta.load_registry(self.registry)

    def test_unborn_context_and_unknown_alias(self):
        code, out, _ = self.invoke("context", "example")
        self.assertEqual(code, 0)
        self.assertIn("Branch: main", out)
        self.assertIn("Working tree: clean", out)
        self.assertIn("no commits yet", out)
        self.assertIn("agents: not configured", out)
        code, _, err = self.invoke("status", "missing")
        self.assertEqual(code, 1)
        self.assertIn("Unknown project", err)

    def test_status_staged_unstaged_untracked_and_rename(self):
        tracked = self.repo / "old name.txt"
        tracked.write_text("original\n")
        self.commit()
        self.assertFalse(natta.git_state(self.project)[1])
        self.run_git("mv", "old name.txt", "new name.txt")
        (self.repo / "new name.txt").write_text("original\nmodified\n")
        (self.repo / "untracked\nname").write_text("new\n")
        entries = natta.git_state(self.project)[1]
        self.assertIn(("RM", "new name.txt", "old name.txt"), entries)
        self.assertIn(("??", "untracked\nname", None), entries)
        before = self.run_git("status", "--porcelain=v1", "-z")
        code, out, _ = self.invoke("diff", "app")
        self.assertEqual(code, 0)
        self.assertIn("Staged diff summary", out)
        self.assertIn("Unstaged diff summary", out)
        self.assertIn("2 changed paths", out)
        self.assertEqual(before, self.run_git("status", "--porcelain=v1", "-z"))

    def test_detached_head(self):
        (self.repo / "file").write_text("test")
        self.commit()
        self.run_git("checkout", "--detach")
        self.assertIn("Branch: detached HEAD", self.invoke("status", "app")[1])

    def test_not_repository_root(self):
        sub = self.repo / "sub"
        sub.mkdir()
        self.write_registry(path="app with spaces/sub")
        code, _, err = self.invoke("status", "app")
        self.assertEqual(code, 1)
        self.assertIn("not a repository root", err)

    def test_version_metadata(self):
        source = self.repo / "Example.xcodeproj/project.pbxproj"
        source.parent.mkdir()
        source.write_text('MARKETING_VERSION = "1.2.3";\nCURRENT_PROJECT_VERSION = 7;\nMARKETING_VERSION = 1.2.3;')
        self.write_registry('version_source = "Example.xcodeproj/project.pbxproj"', kind="ios-xcode")
        project = natta.load_registry(self.registry)[0]
        self.assertEqual(natta.version(project), "1.2.3 (build 7)")
        source.write_text(source.read_text() + '\nMARKETING_VERSION = 2.0;\nCURRENT_PROJECT_VERSION = 8;')
        self.assertEqual(natta.version(project), "1.2.3, 2.0 (build 7, 8)")
        source.unlink()
        with self.assertRaises(natta.NattaError):
            natta.version(project)

    def test_doctor_checks(self):
        launcher = natta.ROOT.parent.parent / "bin/natta"
        actual_which = natta.shutil.which
        with patch("natta.shutil.which", side_effect=lambda name: str(launcher) if name == "natta" else "/fixture/bin/codex" if name == "codex" else actual_which(name)):
            self.assertEqual(self.invoke("doctor")[0], 0)
            self.write_registry('agents = "missing.md"')
            code, out, _ = self.invoke("doctor")
            self.assertEqual(code, 1)
            self.assertIn("FAIL  app: agents", out)
            self.write_registry(path="missing-directory")
            self.assertEqual(self.invoke("doctor")[0], 1)
        self.write_registry()
        with patch("natta.shutil.which", return_value=None):
            code, out, _ = self.invoke("doctor")
            self.assertEqual(code, 1)
            self.assertIn("FAIL  git available", out)
            self.assertIn("launcher on PATH: missing", out)

    def test_context_references_not_document_contents(self):
        (self.repo / "AGENTS.md").write_text("Secret fixture content not for rendering")
        self.write_registry('agents = "AGENTS.md"')
        code, out, _ = self.invoke("context", "app")
        self.assertEqual(code, 0)
        self.assertIn("AGENTS.md [present]", out)
        self.assertNotIn("Secret fixture", out)

    def test_external_diff_not_executed(self):
        (self.repo / "file").write_text("original\n")
        self.commit()
        (self.repo / "file").write_text("changed\n")
        with patch.dict(os.environ, {"GIT_EXTERNAL_DIFF": "/nonexistent/external-diff"}):
            self.assertEqual(self.invoke("diff", "app")[0], 0)

    def test_json_pre_dispatch_configuration_failures(self):
        for command in (('plan', 'Inspect app'), ('commit', 'app'),
                        ('testflight', 'app', '--check'), ('doctor',)):
            with self.subTest(command=command):
                self.registry.unlink(missing_ok=True)
                code, out, err = self.invoke(*command, '--json')
                data = json.loads(out)
                self.assertEqual((code, err), (1, ''))
                self.assertTrue(data['error'])
                self.assertFalse(data.get('execution_started', data.get('executed', False)))
        self.write_registry()
        code, out, err = self.invoke('commit', 'unknown', '--json')
        self.assertEqual((code, err), (1, ''))
        self.assertEqual(json.loads(out)['status'], 'failed')
        self.registry.write_text('invalid TOML !')
        code, out, err = self.invoke('doctor', '--json')
        self.assertEqual((code, err), (1, ''))
        self.assertFalse(json.loads(out)['core_ready'])
        code, out, err = self.invoke('plan', 'Inspect app')
        self.assertEqual((code, out), (1, ''))
        self.assertIn('Cannot read registry', err)

    def test_json_usage_failure_and_unexpected_bug(self):
        code, out, err = self.invoke('plan', '--json')
        self.assertEqual((code, err), (2, ''))
        self.assertEqual(json.loads(out)['error'], 'invalid_arguments')
        with patch.object(natta, 'load_registry', side_effect=RuntimeError('programmer bug')):
            with self.assertRaisesRegex(RuntimeError, 'programmer bug'):
                self.invoke('plan', 'Inspect app', '--json')

    def test_json_provider_unavailable(self):
        provider = natta.routing.provider
        with patch.object(provider, 'generate'), patch.object(provider, 'probe_cli', side_effect=FileNotFoundError):
            for command in ('route', 'execute'):
                code, out, err = self.invoke(command, 'Inspect app', '--json')
                self.assertEqual((code, err), (1, ''))
                data = json.loads(out)
                self.assertEqual(data['error'], 'provider_unavailable')
                self.assertFalse(data['executed'])

    def test_doctor_optional_provider_and_mandatory_health(self):
        launcher = natta.WORKSPACE_ROOT / 'bin/natta'
        actual_which = natta.shutil.which
        def which(name):
            return str(launcher) if name == 'natta' else None if name == 'codex' else actual_which(name)
        with patch.object(natta.shutil, 'which', side_effect=which):
            code, out, err = self.invoke('doctor', '--json')
            self.assertEqual((code, err), (0, ''))
            data = json.loads(out)
            self.assertTrue(data['core_ready'])
            codex = next(c for c in data['checks'] if c['name'].startswith('Codex CLI'))
            self.assertEqual(codex['status'], 'unavailable')
            self.assertTrue(codex['optional'])
            code, out, err = self.invoke('doctor')
            self.assertEqual((code, err), (0, ''))
            self.assertIn('UNAVAILABLE  Codex CLI', out)
            self.assertIn('Core Natta: ready', out)
            self.write_registry('agents = "missing.md"')
            code, out, err = self.invoke('doctor', '--json')
            self.assertEqual((code, err), (1, ''))
            data = json.loads(out)
            self.assertFalse(data['core_ready'])
            self.assertTrue(any(c['status'] == 'failed' and not c['optional'] for c in data['checks']))


if __name__ == "__main__":
    unittest.main()
