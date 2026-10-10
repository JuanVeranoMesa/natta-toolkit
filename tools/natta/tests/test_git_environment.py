"""Ambient GIT_* routing cannot redirect Natta Git use. Disposable repositories only."""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import commits
import execution
import natta
import runtime_effects

ROUTING = ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY', 'GIT_COMMON_DIR',
           'GIT_CONFIG_GLOBAL', 'GIT_CONFIG_SYSTEM', 'GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_0',
           'GIT_CONFIG_VALUE_0', 'GIT_CONFIG_PARAMETERS', 'GIT_EXTERNAL_DIFF', 'GIT_CEILING_DIRECTORIES')


class DecoyRepositoryTests(unittest.TestCase):
    """A is the registered project; B is a decoy named only by ambient Git variables."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.a, self.b = self.root / 'a', self.root / 'b'
        for repo, subject in ((self.a, 'Intended A'), (self.b, 'Decoy B')):
            repo.mkdir()
            self.git(repo, 'init', '-q', '-b', 'main')
            (repo / 'tracked').write_text(subject + '\n')
            self.git(repo, 'add', 'tracked')
            self.git(repo, 'commit', '-q', '-m', subject)
        # Decoy differs in branch, index and working tree from A.
        self.git(self.b, 'checkout', '-q', '-b', 'decoy')
        (self.b / 'decoy-only').write_text('decoy\n')
        self.git(self.b, 'add', 'decoy-only')
        (self.b / 'tracked').write_text('decoy dirty\n')
        self.registry = self.root / 'projects.toml'
        self.registry.write_text('schema_version=1\n[[projects]]\nalias="a"\nname="Project A"\ntype="generic-git"\npath='
                                 + json.dumps(str(self.a)) + '\n')
        self.project = natta.load_registry(self.registry)[0]
        self.b_head = self.git(self.b, 'rev-parse', 'HEAD')
        self.b_index = (self.b / '.git/index').read_bytes()

    def git(self, repo, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        return subprocess.check_output(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                                        '-c', 'core.hooksPath=/dev/null', '-C', str(repo), *args], env=env).decode()

    def ambient(self, **values):
        return patch.dict(os.environ, values)

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = natta.main(['--registry', str(self.registry), *args])
        return code, out.getvalue(), err.getvalue()

    def assert_decoy_untouched(self):
        self.assertEqual(self.git(self.b, 'rev-parse', 'HEAD'), self.b_head)
        self.assertEqual((self.b / '.git/index').read_bytes(), self.b_index)

    def test_ambient_git_dir_cannot_redirect_status_context_or_diff(self):
        expected = {}
        for command in ('status', 'context', 'diff'):
            expected[command] = self.invoke(command, 'a')
        with self.ambient(GIT_DIR=str(self.b / '.git')):
            for command in ('status', 'context', 'diff'):
                code, out, err = self.invoke(command, 'a')
                self.assertEqual((code, out, err), expected[command], command)
                self.assertIn('Branch: main', out)
                self.assertIn('Working tree: clean (0 changed paths)', out)
                self.assertIn('Intended A', out)
                self.assertNotIn('Decoy B', out)
                self.assertNotIn('decoy-only', out)
        self.assert_decoy_untouched()

    def test_ambient_work_tree_cannot_redirect_repository_identity(self):
        with self.ambient(GIT_WORK_TREE=str(self.b)):
            branch, entries = natta.git_state(self.project)
            self.assertEqual((branch, entries), ('main', []))
            snapshot = execution.snapshot(self.a, protected=True)
        self.assertEqual(snapshot.repository, str(self.a))
        with self.ambient(GIT_DIR=str(self.b / '.git'), GIT_WORK_TREE=str(self.b)):
            self.assertEqual(natta.git_state(self.project), ('main', []))
            capture = runtime_effects.capture(self.project)
        self.assertEqual((capture.repository, capture.git_directory), (str(self.a), str(self.a / '.git')))
        self.assertEqual(capture.commit.decode(), self.git(self.a, 'rev-parse', 'HEAD').strip())

    def test_ambient_index_file_cannot_redirect_snapshots(self):
        decoy_index = self.b / '.git/index'
        clean_protected = execution.snapshot(self.a, protected=True)
        clean_legacy = execution.snapshot(self.a)
        clean_commit = runtime_effects.capture_commit(self.project)
        with self.ambient(GIT_INDEX_FILE=str(decoy_index)):
            self.assertEqual(execution.snapshot(self.a, protected=True), clean_protected)
            self.assertEqual(execution.snapshot(self.a), clean_legacy)
            self.assertEqual(runtime_effects.capture_commit(self.project), clean_commit)
            execution.assert_integrity(self.a, clean_protected, 'ambient index')
        with self.ambient(GIT_DIR=str(self.b / '.git'), GIT_INDEX_FILE=str(decoy_index)):
            self.assertEqual(execution.snapshot(self.a, protected=True), clean_protected)
        self.assert_decoy_untouched()

    def test_adapter_and_workflow_git_children_inspect_registered_project(self):
        (self.b / 'tracked').write_text('trailing whitespace   \n')  # decoy would fail diff --check
        with self.ambient(GIT_DIR=str(self.b / '.git'), GIT_WORK_TREE=str(self.b)):
            top = execution.run(('git', 'rev-parse', '--show-toplevel'), self.a, capture=True)
            self.assertEqual(Path(top.stdout.strip()), self.a)
            checks = natta.adapters.resolve(self.project).diff_checks()
            results = execution.execute(checks, self.a)
            self.assertEqual([r.status for r in results], ['passed', 'passed'])
            code, out, err = self.invoke('verify', 'a', '--level', '1')
        self.assertEqual(code, 0, out + err)
        self.assertIn('PASS  git diff --check (unstaged)', out)
        self.assert_decoy_untouched()

    def test_commit_hardening_still_targets_registered_project(self):
        (self.a / 'tracked').write_text('updated A\n')
        with self.ambient(GIT_DIR=str(self.b / '.git'), GIT_INDEX_FILE=str(self.b / '.git/index'),
                          GIT_WORK_TREE=str(self.b)):
            result = commits.create(self.project, 'Commit A only')
        self.assertEqual(result.status, 'committed', result)
        self.assertEqual(self.git(self.a, 'log', '-1', '--format=%s').strip(), 'Commit A only')
        self.assert_decoy_untouched()


class FixtureDecoyTests(unittest.TestCase):
    """Disposable validation fixtures create their own repository, never the decoy's."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.decoy = Path(temp.name).resolve() / 'decoy'
        self.decoy.mkdir()
        DecoyRepositoryTests.git(self, self.decoy, 'init', '-q', '-b', 'main')
        (self.decoy / 'tracked').write_text('decoy\n')
        DecoyRepositoryTests.git(self, self.decoy, 'add', 'tracked')
        DecoyRepositoryTests.git(self, self.decoy, 'commit', '-q', '-m', 'Decoy')
        self.state = self.decoy_state()

    def decoy_state(self):
        git = self.decoy / '.git'
        return {'head': DecoyRepositoryTests.git(self, self.decoy, 'rev-parse', 'HEAD'),
                'log': DecoyRepositoryTests.git(self, self.decoy, 'log', '--all', '--format=%H %s'),
                'config': (git / 'config').read_bytes(), 'index': (git / 'index').read_bytes(),
                'files': sorted(str(p.relative_to(self.decoy)) for p in self.decoy.rglob('*'))}

    def ambient_variants(self):
        git = self.decoy / '.git'
        yield {'GIT_DIR': str(git)}
        yield {'GIT_DIR': str(git), 'GIT_WORK_TREE': str(self.decoy), 'GIT_INDEX_FILE': str(git / 'index')}

    def run_with_decoy(self, operation):
        for values in self.ambient_variants():
            with self.subTest(ambient=sorted(values)):
                error = None
                with patch.dict(os.environ, values):
                    try:
                        result = operation()
                    except Exception as exc:  # decoy state is asserted first
                        error = exc
                self.assertEqual(self.decoy_state(), self.state)
                if error is not None:
                    raise error
                yield result

    @unittest.skipUnless(Path('/usr/bin/git').exists(), 'fixture uses /usr/bin/git')
    def test_confinement_fixture_git_ignores_ambient_routing(self):
        import macos_confinement as mac
        class Stop(Exception):
            pass
        def stop_at_first_probe(argv, **kwargs):
            # The first probe names the fixture marker; inspect that repository, then stop.
            fixture = Path(argv[-1]).parent  # inspected without ambient GIT_*
            raise Stop(DecoyRepositoryTests.git(self, fixture, 'log', '--format=%s', '--name-only').split())
        def operation():
            with patch.object(mac, 'bounded_run', side_effect=stop_at_first_probe):
                try:
                    mac.fixture_validation('inspection')
                except Stop as stop:
                    return stop.args[0]
        for observed in self.run_with_decoy(operation):
            self.assertEqual(observed, ['fixture', 'marker'])  # the fixture's own commit

    def test_commit_acceptance_fixture_ignores_ambient_routing(self):
        import commit_acceptance as host
        def operation():
            with contextlib.redirect_stdout(io.StringIO()) as out:
                host.fixture()
            return out.getvalue()
        for output in self.run_with_decoy(operation):
            self.assertIn('"disposable_acceptance": "PASS"', output)


class GitEnvironmentTests(unittest.TestCase):
    def test_routing_removed_ordinary_environment_and_fixed_controls_kept(self):
        values = {name: '/decoy' for name in ROUTING}
        values.update(HOME='/fixture-home', PATH='/fixture-bin', LANG='C', SSH_AUTH_SOCK='/agent', NATTA_FIXTURE='kept')
        with patch.dict(os.environ, values, clear=True):
            env = execution.git_environment()
            self.assertEqual(os.environ['GIT_DIR'], '/decoy')  # never mutates the parent process
        self.assertFalse(set(ROUTING) & set(env))
        self.assertEqual({k: env[k] for k in ('HOME', 'PATH', 'LANG', 'SSH_AUTH_SOCK', 'NATTA_FIXTURE')},
                         {'HOME': '/fixture-home', 'PATH': '/fixture-bin', 'LANG': 'C',
                          'SSH_AUTH_SOCK': '/agent', 'NATTA_FIXTURE': 'kept'})
        self.assertEqual({k: v for k, v in env.items() if k.startswith('GIT_')},
                         {'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0', 'GIT_PAGER': 'cat'})

    def test_workflow_children_use_git_environment_only_for_git(self):
        with patch.dict(os.environ, {'GIT_DIR': '/decoy', 'NATTA_FIXTURE': 'kept'}):
            for command in (('git', 'status'), ('/usr/bin/git', 'diff')):
                self.assertEqual(execution.child_environment(command), execution.git_environment())
            other = execution.child_environment(('xcodebuild', '-list'))
        self.assertEqual(other['NATTA_FIXTURE'], 'kept')
        self.assertEqual((other['GIT_OPTIONAL_LOCKS'], other['GIT_PAGER']), ('0', 'cat'))

    def test_git_call_sites_use_the_central_helper(self):
        project = natta.Project('a', (), 'A', Path('/nonexistent'), 'generic-git', {})
        with patch.dict(os.environ, {'GIT_DIR': '/decoy'}):
            expected = execution.git_environment()
            with patch.object(natta.subprocess, 'run', return_value=subprocess.CompletedProcess((), 0, b'', b'')) as run:
                natta.git(project, 'status')
            self.assertEqual(run.call_args.kwargs['env'], expected)
            with patch.object(commits.subprocess, 'run', return_value=subprocess.CompletedProcess((), 0, b'', b'')) as run:
                commits.git(project, 'status')
            self.assertEqual(run.call_args.kwargs['env'], expected)

    def test_scrubbing_logic_is_centralized(self):
        sources = {path.name: path.read_text() for path in natta.ROOT.glob('*.py')}
        scrubbing = re.compile(r"startswith\(\(?['\"]GIT_")
        self.assertEqual(sorted(name for name, text in sources.items() if scrubbing.search(text)), ['execution.py'])
        self.assertNotIn('os.environ', sources['natta.py'])
        self.assertIsNone(re.search(r'\bgit_env\b', sources['runtime_effects.py'] + sources['execution.py']))


if __name__ == '__main__':
    unittest.main()
