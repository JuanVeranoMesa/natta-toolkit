"""Shared contract every registered project adapter must satisfy (docs/EXTENDING.md).

A new adapter is added to adapters.ADAPTERS and given a fixture here; the
fixture-coverage test fails until it is, so no adapter skips the contract.
"""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import adapters
import natta
from execution import Check

SHELLS = {'sh', 'bash', 'zsh', 'dash', 'fish', 'env', 'eval', 'osascript'}
OPERATIONS = (('build', None), ('test', None), ('verify', 1), ('verify', 2), ('verify', 3))


def runner(command, cwd, capture=False, timeout=None):
    """Mocked external tools: Xcode metadata and destinations, Git file listings."""
    output = ''
    if '-list' in command:
        output = json.dumps({'project': {'schemes': ['App'], 'configurations': ['Debug'], 'targets': ['Unit', 'UI']}})
    elif '-showdestinations' in command:
        output = '{ platform:iOS Simulator, id:11111111-1111-1111-1111-111111111111, OS:26.0, name:iPhone 17 }'
    elif 'ls-files' in command:
        output = 'App.xcodeproj/project.pbxproj\0'
    return subprocess.CompletedProcess(command, 0, output, '')


def generic_git(root):
    return {}


def ios_xcode(root):
    (root / 'App.xcodeproj').mkdir()
    (root / 'App.xcodeproj/project.pbxproj').write_text('')
    return {'project': 'App.xcodeproj', 'scheme': 'App', 'configuration': 'Debug', 'unit_targets': ['Unit'],
            'ui_targets': ['UI'], 'test_targets': ['Unit'], 'ui_tests': ['UI/Smoke/testLaunch']}


# adapter type -> function creating a minimal configured repository, returning its execution table
FIXTURES = {'generic-git': generic_git, 'ios-xcode': ios_xcode}


class AdapterContractTests(unittest.TestCase):
    def project(self, kind):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve() / 'repo'; root.mkdir()
        execution = adapters.parse_execution(kind, FIXTURES[kind](root), root)
        state = Path(temp.name).resolve() / 'state'; state.mkdir()
        return natta.Project('app', (), 'App', root, kind, {}, execution), state

    def test_every_adapter_has_a_contract_fixture(self):
        self.assertEqual(set(FIXTURES), set(adapters.ADAPTERS))

    def test_unconfigured_execution_is_accepted_only_as_an_empty_table(self):
        for kind in adapters.ADAPTERS:
            with self.subTest(kind=kind):
                self.assertEqual(adapters.parse_execution(kind, {}, Path('/tmp')), {})
                with self.assertRaises(natta.NattaError):
                    adapters.parse_execution(kind, [], Path('/tmp'))

    def test_contract(self):
        for kind in adapters.ADAPTERS:
            with self.subTest(kind=kind):
                project, state = self.project(kind)
                adapter = adapters.resolve(project, runner)
                diagnosis = adapter.diagnose()
                self.assertTrue(diagnosis)
                for passed, label in diagnosis:
                    self.assertIsInstance(passed, bool)
                    self.assertTrue(isinstance(label, str) and label)
                for operation, level in OPERATIONS:
                    checks = adapter.plan(operation, level, state)
                    self.assertIsInstance(checks, list)
                    for check in checks:
                        self.assertIsInstance(check, Check)
                        self.assertTrue(check.name)
                        # A check either runs a fixed argv tuple or reports why it does not.
                        if check.command:
                            self.assertIsInstance(check.command, tuple)
                            self.assertTrue(all(isinstance(arg, str) and arg for arg in check.command))
                            self.assertNotIn(Path(check.command[0]).name, SHELLS)
                            self.assertEqual(check.status, '')
                        else:
                            self.assertIn(check.status, ('skipped', 'unavailable'))
                            self.assertTrue(check.reason)
                        if check.command[:1] == ('git',):
                            self.assertIn('core.fsmonitor=false', check.command)
                        # Paths stay within the project or the supplied external state directory.
                        for arg in check.command:
                            if arg.startswith('/'):
                                self.assertTrue(Path(arg).is_relative_to(project.path) or
                                                Path(arg).is_relative_to(state), arg)
                verify = [c.name for c in adapter.plan('verify', 1, state)]
                self.assertTrue(any('diff --check' in name for name in verify), verify)


if __name__ == '__main__':
    unittest.main()
