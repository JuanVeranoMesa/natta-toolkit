"""Real small subprocesses and temporary fixture repos; never run Xcode builds."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import execution
import natta
import test_natta


class ExecutionUXTests(unittest.TestCase):
    setUp = test_natta.HarnessTests.setUp
    run_git = test_natta.HarnessTests.run_git
    write_registry = test_natta.HarnessTests.write_registry
    invoke = test_natta.HarnessTests.invoke
    def session(self, **kwargs):
        return execution.ExecutionDirectory([self.project], self.project, 'verify', 2, **kwargs)

    def invoke_workflow(self, operation='test', flags=(), code=0, details=False):
        paths = []
        class Adapter:
            def plan(inner, op, level, state):
                paths.append(state)
                name = 'Simulator build' if op == 'build' else 'UI smoke tests' if op == 'verify' else 'Automated tests'
                script = ("from pathlib import Path; import sys; "
                          "p=Path(sys.argv[1]); "
                          "(p/'DerivedData').mkdir(); (p/'Packages').mkdir(); "
                          "(p/'UI-smoke-tests.xcresult').mkdir(); "
                          "print('CompileSwift RAW_XCODE_NOISE'); "
                          "print('RAW_STDERR', file=sys.stderr); "
                          f"sys.exit({code})")
                return [execution.Check(name, (sys.executable, '-c', script, str(state)))]
        original_execute = natta.execute
        def run_checks(checks, cwd, **kwargs):
            # Model an Xcode-produced result bundle for persistence assertions.
            kwargs['session'].bundles.append(kwargs['session'].path / 'UI-smoke-tests.xcresult')
            return original_execute(checks, cwd, **kwargs)
        args = (operation, 'app', *(['--level', '2'] if operation == 'verify' else []), *flags)
        with patch('natta.adapters.resolve', return_value=Adapter()), \
             patch('natta.execute', side_effect=run_checks), \
             patch('execution.Path.home', return_value=self.root / 'home'), \
             patch('execution.xcode_details', return_value=(('1 test failed', 'Suite/testExample', 'Assertion failed'), ('Suite/testExample',)) if details else ((), ())):
            result = self.invoke(*args)
        return result, paths[0]

    def test_successful_build_and_test_are_concise(self):
        for operation in ('build', 'test'):
            (code, out, err), state = self.invoke_workflow(operation)
            self.assertEqual((code, err), (0, ''))
            self.assertIn('PASS  ', out)
            self.assertIn('Result: passed', out)
            self.assertNotIn('RAW_', out)
            self.assertNotIn(str(state), out)
            self.assertLess(len(out.splitlines()), 12)
            self.assertFalse(state.exists())
            self.assertFalse((self.root / 'home').exists())

    def test_failed_named_check_with_deterministic_details_and_cleanup(self):
        before = execution.snapshot(self.repo)
        (code, out, err), state = self.invoke_workflow('verify', code=65, details=True)
        self.assertEqual((code, err), (65, ''))
        for text in ('FAIL  UI smoke tests', '1 test failed', 'Suite/testExample', 'Assertion failed', 'Result: failed', '--log or --verbose'):
            self.assertIn(text, out)
        self.assertNotIn('RAW_', out)
        self.assertFalse(state.exists())
        self.assertFalse((self.root / 'home').exists())
        self.assertEqual(before, execution.snapshot(self.repo))

    def test_verbose_and_log_are_independent(self):
        for verbose, log in ((False, True), (True, False), (True, True)):
            with self.subTest(verbose=verbose, log=log):
                flags = tuple(flag for flag, enabled in (('--verbose', verbose), ('--log', log)) if enabled)
                before = execution.snapshot(self.repo)
                (code, out, err), state = self.invoke_workflow(flags=flags)
                self.assertEqual((code, err), (0, ''))
                self.assertEqual('RAW_XCODE_NOISE' in out, verbose)
                self.assertEqual('RAW_STDERR' in out, verbose)
                self.assertEqual('Diagnostics retained:' in out, log)
                self.assertFalse(state.exists())
                root = self.root / 'home/Library/Logs/NattaToolkit'
                if log:
                    retained = Path(out.split('Diagnostics retained:\n  ')[1].strip())
                    self.assertEqual(retained.parent, root)
                    self.assertRegex(retained.name, r'^\d{4}-\d{2}-\d{2}T\d{6}-app-test-')
                    self.assertIn('RAW_XCODE_NOISE', (retained / 'Automated-tests.log').read_text())
                    self.assertIn('RAW_STDERR', (retained / 'Automated-tests.log').read_text())
                    self.assertTrue((retained / 'UI-smoke-tests.xcresult').is_dir())
                    self.assertFalse((retained / 'DerivedData').exists())
                    self.assertFalse(retained.is_relative_to(self.repo))
                self.assertEqual(before, execution.snapshot(self.repo))

    def test_verbose_alone_leaves_no_logs(self):
        (code, out, err), state = self.invoke_workflow(flags=('--verbose',))
        self.assertEqual((code, err), (0, ''))
        self.assertIn('RAW_XCODE_NOISE', out)
        self.assertFalse(state.exists())
        self.assertFalse((self.root / 'home').exists())

    def test_summary_never_overrides_process_exit(self):
        check = execution.Check('UI smoke tests', ('xcodebuild', 'test'))
        for exit_code in (0, 65):
            with patch('execution.xcode_details', return_value=(('42 tests passed',), ())), \
                 contextlib.redirect_stdout(io.StringIO()):
                results = execution.execute([check], self.repo,
                    lambda *a, **k: subprocess.CompletedProcess([], exit_code))
                self.assertEqual(results[0].status, 'passed' if exit_code == 0 else 'failed')
                self.assertEqual(execution.render(results), exit_code)

    def test_failed_logged_run_retains_diagnostics_and_cleans_state(self):
        (code, out, err), state = self.invoke_workflow(flags=('--log',), code=65)
        self.assertEqual((code, err), (65, ''))
        self.assertNotIn('RAW_', out)
        retained = Path(out.split('Diagnostics retained:\n  ')[1].strip())
        self.assertTrue((retained / 'Automated-tests.log').exists())
        self.assertFalse(state.exists())

    def test_cleanup_on_discovery_and_integrity_failure(self):
        for mutate in (False, True):
            paths = []
            class Adapter:
                def plan(inner, op, level, state):
                    paths.append(state)
                    if mutate:
                        (self.repo / 'unexpected').write_text('generated')
                    raise execution.NattaError('Discovery failed')
            with patch('natta.adapters.resolve', return_value=Adapter()):
                code, _, err = self.invoke('build', 'app')
            self.assertEqual(code, 1)
            self.assertIn('integrity changed' if mutate else 'Discovery failed', err)
            self.assertFalse(paths[0].exists())
            if mutate:
                self.assertEqual((self.repo / 'unexpected').read_text(), 'generated')

    def test_cleanup_refuses_changed_marker_and_symlink(self):
        session = self.session()
        session.marker.write_text('someone else')
        with self.assertRaisesRegex(execution.NattaError, 'ownership changed'):
            session.cleanup()
        self.assertTrue(session.path.exists())
        session.marker.write_text(session.token)
        original = session.path
        moved = original.with_name(original.name + '-moved')
        original.rename(moved)
        original.symlink_to(self.repo, target_is_directory=True)
        try:
            with self.assertRaisesRegex(execution.NattaError, 'ownership changed'):
                session.cleanup()
            self.assertTrue(self.repo.exists())
        finally:
            original.unlink()
            moved.rename(original)
            session.cleanup()

    def test_cleanup_refuses_replaced_directory(self):
        session = self.session()
        original = session.path
        moved = original.with_name(original.name + '-moved')
        original.rename(moved)
        original.mkdir()
        (original / '.natta-owner').write_text(session.token)
        try:
            with self.assertRaisesRegex(execution.NattaError, 'ownership changed'):
                session.cleanup()
        finally:
            (original / '.natta-owner').unlink()
            original.rmdir()
            moved.rename(original)
            session.cleanup()

    def test_state_and_retained_logs_must_be_external(self):
        path = self.repo / 'new-state'
        path.mkdir()
        with patch('execution.tempfile.mkdtemp', return_value=str(path)), \
             self.assertRaisesRegex(execution.NattaError, 'outside registered'):
            self.session()
        self.assertFalse(path.exists())
        session = self.session(log=True)
        try:
            with patch('execution.Path.home', return_value=self.repo), \
                 self.assertRaisesRegex(execution.NattaError, 'outside registered'):
                session.retain()
            self.assertFalse((self.repo / 'Library').exists())
        finally:
            session.cleanup()

    def test_xcresult_summary_uses_structured_fields(self):
        bundle = self.root / 'tests.xcresult'
        bundle.mkdir()
        command = ('xcodebuild', '-resultBundlePath', str(bundle), 'test')
        summary = {'passedTests': 42, 'failedTests': 1, 'skippedTests': 2,
                   'testFailures': [{'testIdentifierString': 'Suite/testFailure', 'failureText': 'Expected true'}]}
        calls = []
        def reader(cmd, cwd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, json.dumps(summary), '')
        lines, identifiers = execution.xcode_details(command, self.repo, reader)
        self.assertEqual(identifiers, ('Suite/testFailure',))
        self.assertEqual(lines, ('42 tests passed', '1 test failed', '2 tests skipped', 'Suite/testFailure', 'Expected true'))
        self.assertIn('summary', calls[0][0])
        self.assertEqual(calls[0][1], {'capture': True, 'timeout': 30})
        for response in ('garbage', '[]', '{"passedTests": true, "failedTests": -1, "testFailures": [null]}'):
            lines, identifiers = execution.xcode_details(command, self.repo, lambda *a, **k: subprocess.CompletedProcess([], 0, response))
            self.assertEqual((lines, identifiers), ((), ()))
        for error in (OSError('missing'), subprocess.TimeoutExpired([], 30)):
            with patch('execution.run', side_effect=error):
                self.assertEqual(execution.xcode_details(command, self.repo, execution.run), ((), ()))

    def test_details_and_build_error_summary_are_bounded(self):
        path = self.root / 'build.log'
        path.write_text('noise\n' + ''.join(f'file.swift:1: error: failure {n} ' + 'x' * 1000 + '\n' for n in range(100)))
        lines = execution.build_errors(path)
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(len(line) <= 240 for line in lines))
        self.assertNotIn('noise', '\n'.join(lines))
        self.assertEqual(execution.build_errors(self.root / 'missing.log'), ())

    def test_help_explains_flags(self):
        for operation in ('build', 'test', 'verify'):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                natta.main([operation, '--help'])
            text = ' '.join(out.getvalue().split())
            for phrase in ('--verbose', '--log', 'does not retain logs', 'does not increase terminal verbosity', '~/Library/Logs/NattaToolkit/<run-id>/'):
                self.assertIn(phrase, text.replace('<run- id>', '<run-id>'))
