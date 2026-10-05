"""Public help and parser boundaries; no application execution."""
import contextlib
import io
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import natta
from execution import snapshot

COMMANDS = ('projects', 'status', 'context', 'diff', 'doctor', 'build', 'test', 'verify', 'codex')


class HelpTests(unittest.TestCase):
    def help_output(self, command=None):
        args = [command, '--help'] if command else ['--help']
        proc = subprocess.run([sys.executable, str(natta.ROOT / 'natta.py'), *args],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, '')
        self.assertNotIn('natta.py', proc.stdout)
        self.assertIn('usage: natta', proc.stdout)
        return proc.stdout

    def test_root_help(self):
        text = self.help_output()
        self.assertIn('local-first', text)
        self.assertIn('Examples:', text)
        for command in COMMANDS:
            self.assertIn(command, text)

    def test_subcommands_have_meaningful_help(self):
        expected = {'projects': ('alias', 'adapter', 'repository path'),
                    'status': ('branch', 'working-tree', 'version/build', 'canonical'),
                    'context': ('orientation/context', 'future provider', 'does not invoke AI'),
                    'diff': ('Read-only', 'staged/unstaged', 'reset'),
                    'doctor': ('Diagnostic-only', 'Phase 2', 'simulator'),
                    'build': ('registered deterministic', 'No AI', 'without fixing'),
                    'test': ('normal test workflow', 'unavailable', 'no automatic fixes'),
                    'verify': ('project-specific', 'unavailable/skipped', 'Level 3')}
        for command, phrases in expected.items():
            with self.subTest(command=command):
                text = ' '.join(self.help_output(command).split())
                for phrase in phrases:
                    self.assertIn(phrase, text)
                self.assertIn('Example:', text)
                if command not in ('projects', 'doctor'):
                    self.assertIn('PROJECT', text)
                    self.assertIn('Registered Natta project alias, such as example or sample', text)

    def test_verify_levels_and_parser(self):
        text = self.help_output('verify')
        for phrase in ('--level {1,2,3}', 'Level 1: Localized / low-risk',
                       'Level 2: Business-logic', 'Level 3: Platform, persistence, integration'):
            self.assertIn(phrase, text)
        for level in (1, 2, 3):
            self.assertEqual(natta.make_parser().parse_args(['verify', 'example', '--level', str(level)]).level, level)
        for level in ('0', '4', '-1', 'words'):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as result:
                natta.make_parser().parse_args(['verify', 'example', '--level', level])
            self.assertEqual(result.exception.code, 2)
            self.assertIn('natta verify: error:', err.getvalue())
            self.assertNotIn('natta.py', err.getvalue())

    def test_help_stops_before_registry_and_execution(self):
        with patch('natta.load_registry', side_effect=AssertionError('help loaded registry')), \
             patch('natta.execute', side_effect=AssertionError('help executed workflow')), \
             contextlib.redirect_stdout(io.StringIO()):
            for command in (None, *COMMANDS):
                with self.subTest(command=command), self.assertRaises(SystemExit) as result:
                    natta.main([command, '--help'] if command else ['--help'])
                self.assertEqual(result.exception.code, 0)

    def test_help_preserves_registered_application_repositories(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            (repo/'ignored').write_text('synthetic ignored data')
            (repo/'.gitignore').write_text('ignored\n')
            before = snapshot(repo)
            with patch.object(natta, 'DEFAULT_REGISTRY', repo/'missing-registry.toml'):
                for command in (None, *COMMANDS):
                    self.help_output(command)
            self.assertEqual(before, snapshot(repo))
