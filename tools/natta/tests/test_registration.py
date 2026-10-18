"""natta project add/remove/validate: registry-only, verified, atomic writes."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import natta


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.home = self.root / 'home'; self.home.mkdir()
        home = patch.dict(os.environ, {'HOME': str(self.home)}); home.start(); self.addCleanup(home.stop)
        self.registry = self.home / '.config/natta/projects.toml'

    def repo(self, name, *files, parent=None):
        path = (parent or self.home / 'Projects') / name
        path.mkdir(parents=True)
        subprocess.run(['git', 'init', '-q', str(path)], check=True, env=natta.git_environment())
        for file in files:
            (path / file).parent.mkdir(parents=True, exist_ok=True)
            (path / file).write_text('')
        return path

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = natta.main(['--registry', str(self.registry), *args])
        return code, out.getvalue(), err.getvalue()

    def entries(self):
        return tomllib.loads(self.registry.read_text())['projects']

    def test_first_add_creates_registry_with_home_relative_path_and_detected_documents(self):
        repo = self.repo('example', 'AGENTS.md', 'docs/ROADMAP.md')
        code, out, err = self.invoke('project', 'add', 'example', str(repo), '--name', 'Example App',
                                     '--extra-alias', 'example-app')
        self.assertEqual((code, err), (0, ''))
        self.assertIn('Registered example (generic-git) at ~/Projects/example', out)
        self.assertEqual(self.entries(), [{'alias': 'example', 'aliases': ['example-app'], 'name': 'Example App',
                                           'path': '~/Projects/example', 'type': 'generic-git',
                                           'agents': 'AGENTS.md', 'roadmap': 'docs/ROADMAP.md'}])
        project, = natta.load_registry(self.registry)
        self.assertEqual((project.path, project.files['roadmap']), (repo, repo / 'docs/ROADMAP.md'))
        code, out, _ = self.invoke('projects')
        self.assertEqual(code, 0); self.assertIn('Example App', out)

    def test_path_outside_home_stays_absolute_and_name_defaults_to_directory(self):
        repo = self.repo('outside', parent=self.root / 'srv')
        self.assertEqual(self.invoke('project', 'add', 'outside', str(repo))[0], 0)
        self.assertEqual(self.entries()[0]['path'], str(repo))
        self.assertEqual(self.entries()[0]['name'], 'outside')

    def test_xcode_container_and_shared_scheme_are_detected(self):
        repo = self.repo('app', 'App.xcodeproj/xcshareddata/xcschemes/App.xcscheme')
        code, out, err = self.invoke('project', 'add', 'app', str(repo), '--unit-target', 'AppTests',
                                     '--ui-target', 'AppUITests', '--ui-test', 'AppUITests/Smoke/testLaunch')
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(self.entries()[0]['type'], 'ios-xcode')
        self.assertEqual(self.entries()[0]['execution'],
                         {'project': 'App.xcodeproj', 'scheme': 'App', 'configuration': 'Debug',
                          'unit_targets': ['AppTests'], 'ui_targets': ['AppUITests'],
                          'test_targets': ['AppTests'], 'ui_tests': ['AppUITests/Smoke/testLaunch']})
        self.assertEqual(natta.load_registry(self.registry)[0].execution['scheme'], 'App')

    def test_xcode_without_targets_is_inspection_only_and_ambiguity_is_refused(self):
        repo = self.repo('app', 'App.xcodeproj/project.pbxproj')
        code, out, _ = self.invoke('project', 'add', 'app', str(repo))
        self.assertEqual(code, 0)
        self.assertIn('inspection only', out)
        self.assertNotIn('execution', self.entries()[0])
        other = self.repo('two', 'A.xcodeproj/project.pbxproj', 'B.xcodeproj/project.pbxproj')
        code, _, err = self.invoke('project', 'add', 'two', str(other), '--unit-target', 'ATests')
        self.assertEqual(code, 1)
        self.assertIn('found: A.xcodeproj, B.xcodeproj); pass --container', err)
        code, _, err = self.invoke('project', 'add', 'two', str(other), '--unit-target', 'ATests',
                                   '--container', 'A.xcodeproj')
        self.assertEqual(code, 1)
        self.assertIn('shared scheme automatically (found: none); pass --scheme', err)
        self.assertEqual(self.invoke('project', 'add', 'two', str(other), '--unit-target', 'ATests',
                                     '--container', 'A.xcodeproj', '--scheme', 'A')[0], 0)

    def test_refusals_leave_the_registry_unchanged(self):
        repo = self.repo('example')
        self.assertEqual(self.invoke('project', 'add', 'example', str(repo))[0], 0)
        before = self.registry.read_bytes()
        (repo / 'sub').mkdir()
        plain = self.root / 'plain'; plain.mkdir()
        other = self.repo('other')
        for args, message in (
                (('example', str(self.repo('dup'))), 'Alias already registered: example'),
                (('x', str(other), '--extra-alias', 'example'), 'Alias already registered: example'),
                (('Bad', str(other)), "Invalid alias 'Bad'"),
                (('x', str(other), '--extra-alias', 'Bad!'), "Invalid alias: 'Bad!'"),
                (('x', str(repo / 'sub')), 'must be the repository root'),
                (('x', str(plain)), 'is not inside a Git repository'),
                (('x', str(self.root / 'missing')), 'is not a directory'),
                (('x', str(other), '--scheme', 'S'), 'Xcode execution options are not supported')):
            with self.subTest(args=args):
                code, _, err = self.invoke('project', 'add', *args)
                self.assertEqual(code, 1)
                self.assertIn(message, err)
                self.assertEqual(self.registry.read_bytes(), before)
        self.assertEqual([p.name for p in self.registry.parent.iterdir()], ['projects.toml'])

    def test_remove_deletes_only_the_entry_and_keeps_comments(self):
        for name in ('one', 'two', 'three'):
            self.repo(name)
        self.registry.parent.mkdir(parents=True)
        self.registry.write_text(
            '# My registry\nschema_version = 1\n\n'
            '[[projects]]\nalias = "one"\nname = "One"\npath = "~/Projects/one"\ntype = "generic-git"\n\n'
            '# Second project\n[[projects]]\nalias = "two"\nname = "Two"\npath = "~/Projects/two"\n'
            'type = "ios-xcode"\n\n[projects.execution]\nproject = "Two.xcodeproj"\nscheme = "Two"\n'
            'configuration = "Debug"\nunit_targets = ["T"]\ntest_targets = ["T"]\n\n'
            '# Third project\n[[projects]]\nalias = "three"\nname = "Three"\npath = "~/Projects/three"\n'
            'type = "generic-git"\n')
        code, out, err = self.invoke('project', 'remove', 'two')
        self.assertEqual((code, err), (0, ''))
        self.assertIn('its repository was not modified', out)
        text = self.registry.read_text()
        self.assertEqual([e['alias'] for e in self.entries()], ['one', 'three'])
        for kept in ('# My registry', '# Third project'):
            self.assertIn(kept, text)
        for removed in ('# Second project', 'Two.xcodeproj'):
            self.assertNotIn(removed, text)
        self.assertTrue(self.repo_exists('two'))
        self.assertEqual(self.invoke('project', 'remove', 'three')[0], 0)
        code, _, err = self.invoke('project', 'remove', 'one')
        self.assertEqual(code, 1); self.assertIn('only registered project', err)
        code, _, err = self.invoke('project', 'remove', 'nope')
        self.assertEqual(code, 1); self.assertIn("Unknown project 'nope'", err)

    def repo_exists(self, name):
        return (self.home / 'Projects' / name / '.git').is_dir()

    def test_remove_refuses_unusual_layouts(self):
        self.repo('one'); self.repo('two')
        self.registry.parent.mkdir(parents=True)
        self.registry.write_text('schema_version = 1\nprojects = [\n'
                                 '  {alias = "one", name = "One", path = "~/Projects/one", type = "generic-git"},\n'
                                 '  {alias = "two", name = "Two", path = "~/Projects/two", type = "generic-git"},\n]\n')
        before = self.registry.read_bytes()
        code, _, err = self.invoke('project', 'remove', 'one')
        self.assertEqual(code, 1); self.assertIn('edit the file manually', err)
        self.assertEqual(self.registry.read_bytes(), before)

    def test_validate_and_json_results(self):
        repo = self.repo('example', 'AGENTS.md')
        code, out, _ = self.invoke('project', 'add', 'example', str(repo), '--json')
        self.assertEqual((code, json.loads(out)['status']), (0, 'added'))
        code, out, _ = self.invoke('project', 'validate', '--json')
        self.assertEqual((code, json.loads(out)), (0, {'status': 'passed', 'projects': ['example'], 'problems': []}))
        (repo / 'AGENTS.md').unlink()
        code, out, _ = self.invoke('project', 'validate')
        self.assertEqual(code, 1)
        self.assertIn('FAIL  example: agents missing', out)
        code, out, _ = self.invoke('project', 'add', 'Bad', str(repo), '--json')
        data = json.loads(out)
        self.assertEqual((code, data['status'], data['error']), (1, 'failed', 'registration_invalid'))
        self.assertIn('Invalid alias', data['message'])

    def test_missing_registry_points_to_project_add(self):
        code, out, _ = self.invoke('doctor')
        self.assertEqual(code, 1)
        self.assertIn('no such file. Register a repository with `natta project add ALIAS PATH`', out)
        code, _, err = self.invoke('projects')
        self.assertEqual(code, 1)
        self.assertIn('natta project add ALIAS PATH', err)


if __name__ == '__main__':
    unittest.main()
