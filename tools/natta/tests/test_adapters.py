import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import adapters
import execution
import natta
import test_natta


class AdapterTests(unittest.TestCase):
    setUp = test_natta.HarnessTests.setUp
    run_git = test_natta.HarnessTests.run_git
    write_registry = test_natta.HarnessTests.write_registry
    commit = test_natta.HarnessTests.commit
    invoke = test_natta.HarnessTests.invoke

    def configure(self, **changes):
        config = dict(project="Example.xcodeproj", scheme="Example", configuration="Debug",
                      unit_targets=["Unit"], ui_targets=["UI"], test_targets=["Unit"], ui_tests=["UI/Smoke"])
        config.update(changes)
        container = self.repo / 'Example.xcodeproj'
        container.mkdir(exist_ok=True)
        (container / 'project.pbxproj').write_text('MARKETING_VERSION = 1;')
        self.write_registry('[projects.execution]\n' + '\n'.join(f'{k} = {json.dumps(v)}' for k, v in config.items()), kind='ios-xcode')
        self.project = natta.load_registry(self.registry)[0]
        self.calls = []
        return adapters.resolve(self.project, self.fake)

    def fake(self, command, cwd, capture=False, timeout=None):
        self.calls.append(command)
        output = ''
        if '-list' in command:
            output = json.dumps({'project': {'schemes': ['Example'], 'configurations': ['Debug'], 'targets': ['Unit', 'UI']}})
        elif '-showdestinations' in command:
            output = '''Available destinations:
{ platform:iOS Simulator, id:11111111-1111-1111-1111-111111111111, OS:26.0, name:iPhone 17 }
{ platform:iOS Simulator, id:22222222-2222-2222-2222-222222222222, OS:26.1, name:iPhone 17 }
Ineligible destinations:
{ platform:iOS Simulator, id:33333333-3333-3333-3333-333333333333, OS:27.0, name:iPhone 18 }'''
        elif 'ls-files' in command:
            output = 'Example.xcodeproj/project.pbxproj\0'
        return subprocess.CompletedProcess(command, 0, output, '')

    def test_resolution_and_missing_execution(self):
        self.assertIsInstance(adapters.resolve(self.project), adapters.GenericGit)
        for kind in ('unknown', ''):
            self.write_registry(kind=kind or 'ios-xcode')
            p = natta.load_registry(self.registry)[0]
            with self.assertRaises(natta.NattaError):
                adapters.resolve(p).plan('build', None, self.root)

    def test_malformed_execution(self):
        bad = [dict(scheme=''), dict(configuration=2), dict(project='../Bad.xcodeproj'),
               dict(project='/tmp/Bad.xcodeproj'), dict(workspace='Example.xcworkspace'),
               dict(unit_targets='Unit'), dict(test_targets=['Missing']), dict(ui_tests=['Unit/Smoke']),
               dict(unit_targets=['Unit', 'Unit']), dict(script='echo unsafe')]
        for change in bad:
            with self.subTest(change=change), self.assertRaises(natta.NattaError):
                self.configure(**change)
        for value in ('shell', [], 2):
            with self.assertRaises(natta.NattaError):
                adapters.parse_execution('ios-xcode', value, self.repo)
        with self.assertRaises(natta.NattaError):
            adapters.parse_execution('generic-git', {'script': 'foo'}, self.repo)

    def test_build_construction_and_external_state(self):
        adapter = self.configure()
        checks = adapter.plan('build', None, self.root / 'external')
        self.assertEqual(len(checks), 1)
        cmd = checks[0].command
        self.assertIn(str(self.repo / 'Example.xcodeproj'), cmd)
        self.assertIn('generic/platform=iOS Simulator', cmd)
        self.assertIn('CODE_SIGNING_ALLOWED=NO', cmd)
        self.assertEqual(cmd[-1], 'build')
        self.assertNotIn('-showdestinations', str(self.calls))
        self.assertEqual(cmd[cmd.index('-derivedDataPath') + 1], str(self.root / 'external/DerivedData'))

    def test_test_selection_and_dynamic_destination(self):
        adapter = self.configure()
        cmd = adapter.plan('test', None, self.root)[0].command
        self.assertIn('-only-testing:Unit', cmd)
        self.assertNotIn('-only-testing:UI', cmd)
        self.assertIn('platform=iOS Simulator,id=22222222-2222-2222-2222-222222222222', cmd)
        self.assertEqual(cmd[-1], 'test')
        self.assertIn('-resultBundlePath', cmd)

    def test_level_order_and_optional_checks(self):
        adapter = self.configure()
        expected = {1: ['Simulator build'], 2: ['Unit tests', 'Simulator build', 'UI smoke tests'],
                    3: ['Unit tests', 'Simulator build', 'UI smoke tests', 'Project/plist syntax', 'Physical-device validation', 'Manual platform/release checks']}
        for level, names in expected.items():
            checks = adapter.plan('verify', level, self.root)
            self.assertEqual([c.name for c in checks], names + ['git diff --check (unstaged)', 'git diff --check (staged)'])
            if level == 3:
                self.assertEqual(checks[4].status, 'unavailable')
                self.assertTrue(checks[4].optional)
                self.assertEqual(checks[5].status, 'skipped')

    def test_absent_ui_skipped_and_absent_simulator_unavailable(self):
        adapter = self.configure(ui_tests=[])
        self.assertEqual(adapter.plan('verify', 2, self.root)[2].status, 'skipped')
        def unavailable(cmd, cwd, **kw):
            if '-showdestinations' in cmd:
                return subprocess.CompletedProcess(cmd, 0, 'No destinations', '')
            return self.fake(cmd, cwd, **kw)
        adapter.runner = unavailable
        checks = adapter.plan('verify', 2, self.root)
        self.assertEqual(checks[0].status, 'unavailable')
        self.assertTrue(checks[1].command)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(execution.render(execution.execute(checks, self.repo, self.fake)), 1)

    def test_doctor_missing_scheme_configuration_target_container(self):
        for changes in (dict(scheme='Bad'), dict(configuration='Release'), dict(unit_targets=['Bad'], test_targets=['Bad'])):
            adapter = self.configure(**changes)
            self.assertFalse(adapter.diagnose()[-1][0])
        adapter = self.configure()
        (self.repo / 'Example.xcodeproj/project.pbxproj').unlink()
        (self.repo / 'Example.xcodeproj').rmdir()
        self.assertFalse(adapter.diagnose()[-1][0])

    def test_generic_levels_and_cli(self):
        self.assertEqual(self.invoke('verify', 'app', '--level', '1')[0], 0)
        for args in [('build', 'app'), ('test', 'app'), ('verify', 'app', '--level', '2'), ('verify', 'app', '--level', '3')]:
            code, out, _ = self.invoke(*args)
            self.assertEqual(code, 1)
            self.assertIn('UNAVAILABLE', out)
        for args in [('verify', 'app'), ('verify', 'app', '--level', '0'), ('verify', 'app', '--level', 'x')]:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                self.invoke(*args)
            self.assertEqual(exc.exception.code, 2)

    def test_execute_failure_exit_output_and_continues(self):
        checks = [execution.Check('Bad', ('false',)), execution.Check('Good', ('true',)),
                  execution.Check('Device', status='unavailable', reason='hardware', optional=True)]
        def fake(cmd, cwd, **kwargs):
            return subprocess.CompletedProcess(cmd, 65 if cmd[0] == 'false' else 0)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            results = execution.execute(checks, self.repo, fake)
            code = execution.render(results)
        self.assertEqual(code, 65)
        self.assertEqual([r.status for r in results], ['failed', 'passed', 'unavailable'])
        self.assertNotIn('$ false', out.getvalue())
        self.assertIn('FAIL  Bad (exit 65)', out.getvalue())
        self.assertIn('Result: failed', out.getvalue())

    def test_os_error_and_signal(self):
        check = [execution.Check('Missing', ('missing',))]
        for process, expected in [(OSError('missing'), 127), (subprocess.CompletedProcess([], -15), 143)]:
            with contextlib.redirect_stdout(io.StringIO()), patch('execution.run', side_effect=process if isinstance(process, Exception) else None, return_value=process):
                # Explicit runner injection avoids default-argument binding.
                result = execution.execute(check, self.repo, execution.run)[0]
            self.assertEqual(result.exit_code, expected)

    def test_integrity_stop_on_content_and_paths(self):
        (self.repo / 'file').write_text('before')
        baseline = execution.snapshot(self.repo)
        calls = []
        def mutate(cmd, cwd, **kwargs):
            calls.append(cmd)
            (self.repo / 'file').write_text('after')
            return subprocess.CompletedProcess(cmd, 0)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(natta.NattaError, 'file'):
            execution.execute([execution.Check('First', ('first',)), execution.Check('Second', ('second',))], self.repo, mutate, baseline)
        self.assertEqual(len(calls), 1)
        self.assertEqual((self.repo / 'file').read_text(), 'after')

    def test_query_errors_and_metadata_shape(self):
        for response in (subprocess.CompletedProcess([], 4, '', 'useful error'), subprocess.CompletedProcess([], 0, '{}', '')):
            adapter = self.configure()
            adapter.runner = lambda *a, **kw: response
            with self.assertRaises(natta.NattaError):
                adapter.metadata()

    def test_cli_exit_and_doctor_integration(self):
        self.configure()
        adapter = adapters.resolve(self.project, self.fake)
        with patch('natta.adapters.resolve', return_value=adapter), patch('natta.execute', return_value=[execution.Result(execution.Check('Simulator build', ('xcodebuild',)), 'failed', 65, 1)]):
            code, out, _ = self.invoke('build', 'app')
            self.assertEqual(code, 65)
            self.assertIn('FAIL  Simulator build', out)
            self.assertNotIn('External build/test state:', out)
        with patch('natta.adapters.resolve', return_value=adapter):
            self.assertIn('Xcode container, scheme', self.invoke('doctor')[1])

    def test_workspace_metadata_and_path(self):
        adapter = self.configure()
        config = dict(self.project.execution)
        del config['project']
        config['workspace'] = 'Example.xcworkspace'
        (self.repo / 'Example.xcworkspace').mkdir()
        from dataclasses import replace
        project = replace(self.project, execution=adapters.parse_execution('ios-xcode', config, self.repo))
        def fake(cmd, cwd, **kw):
            if '-list' in cmd:
                return subprocess.CompletedProcess(cmd, 0, json.dumps({'workspace': {'schemes': ['Example']}}), '')
            return self.fake(cmd, cwd, **kw)
        checks = adapters.resolve(project, fake).plan('build', None, self.root)
        self.assertIn('-workspace', checks[0].command)

    def test_optional_unavailable_is_visible_but_not_failure(self):
        checks = [execution.Check('Optional device', status='unavailable', reason='hardware absent', optional=True),
                  execution.Check('Manual check', status='skipped', reason='manual')]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(execution.render(execution.execute(checks, self.repo, self.fake)), 0)
        self.assertIn('UNAVAILABLE  Optional device', output.getvalue())
        self.assertIn('SKIP  Manual check', output.getvalue())
        self.assertIn('Result: passed with omissions', output.getvalue())

    def test_unsupported_doctor_and_missing_xcode(self):
        self.write_registry(kind='unknown')
        self.assertIn('Unsupported adapter: unknown', self.invoke('doctor')[1])
        adapter = self.configure()
        with patch('adapters.shutil.which', return_value=None):
            self.assertEqual(adapter.diagnose()[0], (False, 'xcodebuild available'))

    def test_integrity_detects_ignored_untracked_and_head_changes(self):
        (self.repo / '.gitignore').write_text('ignored\n')
        (self.repo / 'file').write_text('before')
        self.commit()
        before = execution.snapshot(self.repo)
        (self.repo / 'ignored').write_text('generated')
        with self.assertRaisesRegex(natta.NattaError, 'ignored'):
            execution.assert_integrity(self.repo, before, 'fixture')
        (self.repo / 'ignored').unlink()
        self.assertEqual(execution.snapshot(self.repo), before)
        # An empty commit leaves contents/index/status unchanged but changes HEAD.
        self.run_git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '--allow-empty', '-m', 'Fixture head')
        with self.assertRaisesRegex(natta.NattaError, 'Git state/index'):
            execution.assert_integrity(self.repo, before, 'fixture')

    def test_destination_patch_runtime_and_incompatible_output(self):
        adapter = self.configure()
        adapter.metadata()
        def fake(cmd, cwd, **kw):
            return subprocess.CompletedProcess(cmd, 0, """Destinations compatible with scheme:
{ platform:iOS Simulator, id:11111111-1111-1111-1111-111111111111, OS:26.1, name:iPhone 17 }
{ platform:iOS Simulator, id:22222222-2222-2222-2222-222222222222, OS:26.1.1, name:iPhone 17 }
{ platform:iOS Simulator, id:44444444-4444-4444-4444-444444444444, OS:28.0, name:iPhone 19, error:unavailable }
Destinations incompatible with scheme:
{ platform:iOS Simulator, id:33333333-3333-3333-3333-333333333333, OS:27.0, name:iPhone 18 }""", '')
        adapter.runner = fake
        self.assertEqual(adapter.destination(), 'platform=iOS Simulator,id=22222222-2222-2222-2222-222222222222')

    def test_control_characters_rejected(self):
        for change in (dict(scheme='Bad\x00'), dict(project='Bad\n.xcodeproj'), dict(test_targets=['Unit/Bad\n'])):
            with self.assertRaises(natta.NattaError):
                self.configure(**change)

    def test_cli_checks_integrity_even_when_discovery_fails(self):
        adapter = self.configure()
        def mutate(*args):
            (self.repo / 'unexpected').write_text('generated')
            raise natta.NattaError('Discovery failed')
        with patch('natta.adapters.resolve', return_value=adapter), patch.object(adapter, 'plan', side_effect=mutate):
            code, _, err = self.invoke('build', 'app')
        self.assertEqual(code, 1)
        self.assertIn('unexpected', err)
        self.assertIn('stopped without restoration', err)


if __name__ == '__main__':
    unittest.main()
