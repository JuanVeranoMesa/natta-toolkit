"""Provider handoff only: real Codex is never started by these tests."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import natta


class CodexTests(unittest.TestCase):
    def setUp(self):
        router_checks = patch.object(natta.routing, "doctor_checks", return_value=[(True, "Semantic router fixture ready")])
        router_checks.start()
        self.addCleanup(router_checks.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.example = self.root / 'Example repository'
        self.sample = self.root / 'Sample repository'
        self.example.mkdir()
        self.sample.mkdir()
        self.registry = self.root / 'projects.toml'
        self.registry.write_text('''schema_version = 1
[[projects]]
alias = "example"
aliases = ["example-app"]
name = "Example"
path = "Example repository"
type = "generic-git"
[[projects]]
alias = "sample"
name = "Sample"
path = "Sample repository"
type = "generic-git"
''')

    def invoke(self, *args):
        before = Path.cwd()
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = natta.main(['--registry', str(self.registry), *args])
        finally:
            os.chdir(before)  # exec is mocked; restore the test process only.
        return code, out.getvalue(), err.getvalue()

    def launch(self, *args, expected):
        def handoff(executable, argv):
            self.assertEqual(Path.cwd(), expected)
            self.assertEqual(executable, '/fixture/bin/codex')
            self.assertEqual(argv, [executable])  # No prompt, docs or settings.
        with patch('natta.shutil.which', return_value='/fixture/bin/codex'), \
             patch('natta.os.execv', side_effect=handoff) as execv, \
             patch('natta.execute', side_effect=AssertionError('captured interface output')), \
             patch('natta.ExecutionDirectory', side_effect=AssertionError('created execution artifacts')):
            self.assertEqual(self.invoke(*args), (0, '', ''))
            execv.assert_called_once()

    def test_workspace_root_from_harness_layout(self):
        self.assertEqual(natta.WORKSPACE_ROOT, natta.ROOT.parent.parent)
        self.launch('codex', expected=natta.WORKSPACE_ROOT)

    def test_registered_projects_and_aliases(self):
        for alias, expected in (('example', self.example), ('sample', self.sample), ('example-app', self.example)):
            with self.subTest(alias=alias):
                self.launch('codex', alias, expected=expected)

    def test_workspace_launch_ignores_caller_directory_and_registry(self):
        before = Path.cwd()
        try:
            for cwd in (Path.home(), self.root, self.example):
                os.chdir(cwd)
                with patch('natta.load_registry', side_effect=AssertionError('bare launch loaded registry')):
                    self.launch('codex', expected=natta.WORKSPACE_ROOT)
        finally:
            os.chdir(before)

    def test_unknown_project_clean_error(self):
        with patch('natta.os.execv') as execv:
            code, out, err = self.invoke('codex', 'nonexistent-project')
        self.assertEqual((code, out), (1, ''))
        self.assertIn("natta: Unknown project 'nonexistent-project'", err)
        self.assertNotIn('Traceback', err)
        execv.assert_not_called()

    def test_missing_codex_clean_error(self):
        with patch('natta.shutil.which', return_value=None), patch('natta.os.execv') as execv:
            code, out, err = self.invoke('codex')
        self.assertEqual((code, out), (1, ''))
        self.assertIn('Codex CLI unavailable: codex not found on PATH', err)
        self.assertNotIn('Traceback', err)
        execv.assert_not_called()

    def test_missing_target_clean_error(self):
        self.example.rmdir()
        with patch('natta.os.execv') as execv:
            code, _, err = self.invoke('codex', 'example')
        self.assertEqual(code, 1)
        self.assertIn('Codex launch directory missing:', err)
        self.assertNotIn('Traceback', err)
        execv.assert_not_called()
        with patch('natta.WORKSPACE_ROOT', self.root / 'missing workspace'):
            self.assertEqual(self.invoke('codex')[0], 1)

    def test_exec_failure_clean_error(self):
        with patch('natta.shutil.which', return_value='/fixture/bin/codex'), \
             patch('natta.os.execv', side_effect=PermissionError('permission denied')):
            code, _, err = self.invoke('codex', 'example')
        self.assertEqual(code, 1)
        self.assertIn('Cannot launch Codex', err)
        self.assertIn('permission denied', err)
        self.assertNotIn('Traceback', err)

    def test_process_exit_is_not_intercepted(self):
        with patch('natta.shutil.which', return_value='/fixture/bin/codex'), \
             patch('natta.os.execv', side_effect=SystemExit(23)), \
             self.assertRaises(SystemExit) as result:
            self.invoke('codex')
        self.assertEqual(result.exception.code, 23)

    def test_relative_executable_path_resolved_before_chdir(self):
        before = Path.cwd()
        with patch('natta.shutil.which', return_value='relative/bin/codex'), \
             patch('natta.os.execv') as execv:
            self.invoke('codex', 'example')
        executable = str(before / 'relative/bin/codex')
        execv.assert_called_once_with(executable, [executable])

    def test_help_forms_and_no_execution_flags(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            natta.main(['codex', '--help'])
        text = out.getvalue()
        for phrase in ('natta codex', 'natta codex <project>', 'toolkit checkout root',
                       "registered project's repository", 'natta codex example', 'natta codex sample'):
            self.assertIn(phrase, text)
        for phrase in ('--verbose', '--log', 'natta.py'):
            self.assertNotIn(phrase, text)
        for flag in ('--verbose', '--log'):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
                natta.make_parser().parse_args(['codex', flag])
            self.assertEqual(result.exception.code, 2)

    def test_doctor_codex_check_is_path_only(self):
        actual_which = natta.shutil.which
        for available in (True, False):
            def which(name):
                if name == 'codex':
                    return '/fixture/bin/codex' if available else None
                return actual_which(name)
            out = io.StringIO()
            with patch('natta.shutil.which', side_effect=which), \
                 patch('natta.os.execv', side_effect=AssertionError('doctor launched Codex')), \
                 contextlib.redirect_stdout(out):
                code = natta.doctor([])
            self.assertIn(('OK' if available else 'UNAVAILABLE') + '  Codex CLI available', out.getvalue())
            # Other mandatory checks may fail on this host; Codex is optional.
            self.assertIn('(optional)', out.getvalue())
