"""Only applied external clean/process filters are refused. Isolated HOME; no LFS needed."""
import contextlib
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import commits
import execution
import natta
import policy
import router_codex as provider
import routing
import runtime_effects
import semantic_execution as gate


def git_without_ambient(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    return subprocess.check_output(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                                    '-c', 'core.hooksPath=/dev/null', '-c', 'protocol.file.allow=always',
                                    '-C', str(repo), *args], env=env, stderr=subprocess.DEVNULL)


def add_filtered_submodule(repo, source, attributes='data.txt filter=lfs\n', local_clean=None):
    """Only the submodule's own .gitattributes applies the driver; local_clean, if
    given, defines filter.custom.clean ONLY in the submodule's own local config."""
    source.mkdir()
    git_without_ambient(source, 'init', '-q', '-b', 'main')
    (source / 'data.txt').write_text('submodule text\n')
    (source / '.gitattributes').write_text(attributes)
    git_without_ambient(source, 'add', '.')
    git_without_ambient(source, 'commit', '-q', '-m', 'Submodule')
    git_without_ambient(repo, 'submodule', 'add', '-q', str(source), 'sub')
    git_without_ambient(repo, 'commit', '-q', '-m', 'Add submodule')
    if local_clean is not None:
        git_without_ambient(repo / 'sub', 'config', 'filter.custom.clean', local_clean)
    return repo / 'sub' / 'data.txt'


def require_refresh(path):
    # Same bytes, new mtime: Git must re-hash, i.e. run the clean filter, to compare.
    info = path.stat()
    path.write_bytes(path.read_bytes())
    os.utime(path, (info.st_atime + 100, info.st_mtime + 100))


class GitFilterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.home, self.repo = self.root / 'home', self.root / 'repo'
        self.home.mkdir()
        self.repo.mkdir()
        # Isolated global Git configuration/attributes; ambient GIT_* is removed by Natta.
        environment = patch.dict(os.environ, {'HOME': str(self.home), 'XDG_CONFIG_HOME': str(self.home / '.config')})
        environment.start()
        self.addCleanup(environment.stop)
        self.git('init', '-q', '-b', 'main')
        (self.repo / 'notes.txt').write_text('text\n')
        (self.repo / 'image.bin').write_bytes(b'\0binary\n')
        self.git('add', '.')
        self.git('commit', '-q', '-m', 'Initial')
        # Any execution of a configured filter leaves this marker behind.
        self.marker = self.root / 'filter-executed'
        self.command = 'touch ' + json.dumps(str(self.marker)) + ' && cat'
        self.project = natta.Project('fixture', (), 'Fixture', self.repo, 'generic-git', {})

    def git(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        return subprocess.check_output(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                                        '-c', 'core.hooksPath=/dev/null', '-C', str(self.repo), *args], env=env)

    def global_config(self, *args):
        subprocess.check_call(['git', 'config', '--file', str(self.home / '.gitconfig'), *args])

    def install_global_lfs_like(self):
        # Shape of `git lfs install`, with a harmless marker command instead of git-lfs.
        for key, value in (('clean', self.command), ('smudge', 'cat'), ('process', self.command), ('required', 'true')):
            self.global_config('filter.lfs.' + key, value)

    def attributes(self, text, path='.gitattributes'):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def assert_available(self):
        before = runtime_effects.capture(self.project)
        execution.assert_integrity(self.repo, before, 'filter fixture')
        self.assertIsInstance(runtime_effects.capture_commit(self.project), execution.ProtectedSnapshot)
        self.assertFalse(self.marker.exists())

    def assert_refused(self):
        for capture in (runtime_effects.capture, runtime_effects.capture_commit):
            with self.assertRaises(execution.ExternalFilterConfigured) as raised:
                capture(self.project)
            self.assertEqual(raised.exception.code, 'external_filter_configured')
            self.assertIn('clean/process filter applies to repository files', str(raised.exception))
            self.assertNotIn(str(self.marker), str(raised.exception))  # configured command never printed
        self.assertFalse(self.marker.exists())

    def test_global_lfs_like_filter_unused_by_repository_is_allowed(self):
        self.install_global_lfs_like()
        self.assert_available()
        (self.repo / 'notes.txt').write_text('changed\n')
        result = commits.create(self.project, 'Commit without LFS')
        self.assertEqual(result.status, 'committed', result)
        self.assertFalse(self.marker.exists())

    def test_configured_unused_custom_clean_and_process_filters_are_allowed(self):
        self.git('config', 'filter.custom.clean', self.command)
        self.git('config', 'filter.other.process', self.command)
        self.attributes('*.txt filter=unconfigured\n')  # applied name with no command
        self.assert_available()

    def test_applied_global_lfs_filter_is_refused(self):
        self.attributes('*.bin filter=lfs diff=lfs merge=lfs -text\n')
        self.git('add', '.gitattributes')
        self.git('commit', '-q', '-m', 'Track binaries with LFS')
        self.install_global_lfs_like()
        self.assert_refused()
        (self.repo / 'notes.txt').write_text('changed\n')
        result = commits.create(self.project, 'Must not run LFS')
        self.assertEqual((result.status, result.error, result.execution_started), ('failed', 'external_filter_configured', False))
        self.assertIn('clean/process filter', commits.render(result, self.project))
        self.assertFalse(self.marker.exists())

    def test_applied_custom_clean_filter_is_refused(self):
        self.git('config', 'filter.custom.clean', self.command)
        self.attributes('notes.txt filter=custom\n', '.git/info/attributes')
        self.assert_refused()

    def test_applied_custom_process_filter_is_refused(self):
        self.git('config', 'filter.custom.process', self.command)
        self.attributes('[attr]stored filter=custom\n*.bin stored\n', '.git/info/attributes')  # via macro
        self.assert_refused()

    def test_global_attributes_and_untracked_paths_are_inspected(self):
        self.git('config', 'filter.custom.clean', self.command)
        (self.home / 'attributes').write_text('*.new filter=custom\n')
        self.global_config('core.attributesFile', str(self.home / 'attributes'))
        self.assert_available()
        (self.repo / 'draft.new').write_text('untracked input that git add -A would clean\n')
        self.assert_refused()
        self.attributes('*.new\n', '.gitignore')
        self.assert_available()  # ignored paths are never added or cleaned

    def test_unset_and_unspecified_filter_attributes_are_allowed(self):
        self.install_global_lfs_like()
        self.attributes('* filter=lfs\n*.txt -filter\n*.bin !filter\n.gitattributes -filter\n')
        self.assert_available()

    def direct(self, *argv):
        registry = self.root / 'projects.toml'
        registry.write_text('schema_version=1\n[[projects]]\nalias="fixture"\nname="Fixture"\ntype="generic-git"\npath='
                            + json.dumps(str(self.repo)) + '\n')
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = natta.main(['--registry', str(registry), *argv])
        return code, out.getvalue(), err.getvalue()

    def same_size_edit(self):
        # Same size as the committed text: git status must hash it, i.e. run clean.
        (self.repo / 'notes.txt').write_text('TEXT\n')

    def assert_direct_workflows_refused(self, message='clean/process filter applies to repository files'):
        for argv in (('build', 'fixture'), ('test', 'fixture'), ('verify', 'fixture', '--level', '1')):
            with self.subTest(argv=argv), patch.object(natta.adapters.GenericGit, 'plan',
                                                        side_effect=AssertionError('workflow planned')):
                code, out, err = self.direct(*argv)
                self.assertEqual(code, 1)
                self.assertIn(message, err)
                self.assertNotIn(str(self.marker), out + err)
                self.assertFalse(self.marker.exists())

    def test_direct_workflows_never_execute_applied_filter(self):
        self.git('config', 'filter.custom.clean', self.command)
        self.attributes('* filter=custom\n', '.git/info/attributes')
        self.same_size_edit()
        with self.assertRaises(execution.ExternalFilterConfigured):
            execution.snapshot(self.repo)
        self.assert_direct_workflows_refused()

    def semantic(self, capability):
        """Real protected capture; mocked handlers and confinement; no provider call."""
        projects = (self.project,)
        parser, ids = natta.make_parser(), provider.authoritative_ids() - {'codex', 'route'}
        route = routing.RouteResult('matched', capability, project='fixture', arguments=(), arguments_resolved=True)
        route = replace(route, execution_policy=policy.evaluate(
            route, ids, natta.parameters.schema_for(parser, capability), projects))
        bindings = {cap: Mock(return_value=gate.HandlerOutcome(0)) for cap in policy.CATALOG}
        def fake_session(plan):
            return contextlib.nullcontext(gate.confinement.ConfinementResult(
                required=True, applied=True, mode='test_fake',
                required_effects=plan.required_effects, enforced_effects=plan.required_effects))
        with patch.object(gate.confinement, 'open_session', side_effect=fake_session), \
             patch.object(provider, 'run_provider', side_effect=AssertionError('provider call')):
            result = gate.dispatch(route, parser, lambda: projects, ids, bindings, confirm=True)
        return result, bindings

    SUBMODULE_MESSAGE = 'does not support repositories containing Git submodules'

    def assert_submodule_refused(self):
        """Every protected capture refuses from the index alone, before Git can recurse."""
        commands = []
        real_run, real_popen = subprocess.run, subprocess.Popen
        def record_run(argv, *args, **kwargs):
            commands.append(tuple(argv))
            return real_run(argv, *args, **kwargs)
        def record_popen(argv, *args, **kwargs):
            commands.append(tuple(argv))
            return real_popen(argv, *args, **kwargs)
        captures = (lambda: runtime_effects.capture(self.project), lambda: runtime_effects.capture_commit(self.project),
                    lambda: execution.protected_snapshot(self.repo), lambda: execution.snapshot(self.repo),
                    lambda: execution.snapshot(self.repo, protected=True))
        with patch.object(execution.subprocess, 'run', side_effect=record_run), \
             patch.object(execution.subprocess, 'Popen', side_effect=record_popen), \
             patch.object(execution.subprocess, 'check_output', side_effect=AssertionError('legacy Git ran')):
            for capture in captures:
                with self.assertRaises(execution.SubmoduleUnsupported) as raised:
                    capture()
                self.assertEqual(raised.exception.code, 'submodule_unsupported')
                self.assertIn(self.SUBMODULE_MESSAGE, str(raised.exception))
        operations = {next(arg for arg in argv[1:] if not arg.startswith('-') and arg not in
                           ('core.fsmonitor=false', 'core.untrackedCache=false', str(self.repo))) for argv in commands}
        # Only index/HEAD metadata reads; never status/diff/config/check-attr/add.
        self.assertLessEqual(operations, {'ls-files', 'rev-parse', 'symbolic-ref'})
        self.assertTrue(all(argv[-3:] == ('ls-files', '--stage', '-z') for argv in commands if 'ls-files' in argv))
        self.assertFalse(self.marker.exists())

    def test_filter_configured_only_in_submodule_local_config_never_executes(self):
        data = add_filtered_submodule(self.repo, self.root / 'source', 'data.txt filter=custom\n', self.command)
        require_refresh(data)
        # The driver is invisible to the outer repository's configuration query.
        self.assertEqual(subprocess.run(['git', '-C', str(self.repo), 'config', '--get-regexp', r'^filter\.'],
                                        capture_output=True).returncode, 1)
        self.assert_submodule_refused()
        self.assert_direct_workflows_refused(self.SUBMODULE_MESSAGE)
        for capability in ('build', 'test', 'commit'):
            with self.subTest(capability=capability):
                result, bindings = self.semantic(capability)
                self.assertEqual((result.status, result.error, result.execution_started),
                                 ('verification_unavailable', 'submodule_unsupported', False))
                self.assertEqual(result.effect_verification.reason, 'submodule_unsupported')
                self.assertIn(self.SUBMODULE_MESSAGE, gate.render_result(result))
                self.assertFalse(any(handler.called for handler in bindings.values()))
        with patch.object(commits, 'read', side_effect=AssertionError('commit Git ran before refusal')):
            result = commits.create(self.project, 'Must not clean the submodule')
        self.assertEqual((result.status, result.error, result.execution_started), ('failed', 'submodule_commit_unsupported', False))
        self.assertIn(self.SUBMODULE_MESSAGE, commits.render(result, self.project))
        self.assertEqual(self.git('rev-parse', 'HEAD:sub'), git_without_ambient(data.parent, 'rev-parse', 'HEAD'))
        self.assertFalse(self.marker.exists())
        # Control: the fixture is live. Ordinary Git status recurses and runs the driver.
        self.git('status', '--porcelain')
        self.assertTrue(self.marker.exists())

    def test_filter_applied_only_inside_submodule_with_outer_driver_is_refused(self):
        data = add_filtered_submodule(self.repo, self.root / 'source')
        self.install_global_lfs_like()
        require_refresh(data)
        self.assert_submodule_refused()
        self.assert_direct_workflows_refused(self.SUBMODULE_MESSAGE)

    def test_submodule_without_any_filter_is_refused(self):
        data = add_filtered_submodule(self.repo, self.root / 'source', 'data.txt filter=custom\n')  # never configured
        require_refresh(data)
        self.assert_submodule_refused()
        (data).write_text('changed inside submodule\n')
        result = commits.create(self.project, 'Submodules stay unsupported')
        self.assertEqual((result.status, result.error, result.execution_started), ('failed', 'submodule_commit_unsupported', False))

    def test_direct_inspection_with_submodule_is_unchanged(self):
        add_filtered_submodule(self.repo, self.root / 'source', 'data.txt\n')
        with patch.object(execution, 'refuse_applied_filters', side_effect=AssertionError('protected refusal')):
            for argv in (('status', 'fixture'), ('context', 'fixture'), ('diff', 'fixture')):
                with self.subTest(argv=argv):
                    code, out, err = self.direct(*argv)
                    self.assertEqual(code, 0, out + err)
                    self.assertNotIn('submodule', err)

    def test_direct_workflows_with_unused_filter_keep_integrity_checks(self):
        self.install_global_lfs_like()
        self.same_size_edit()
        code, out, err = self.direct('verify', 'fixture', '--level', '1')
        self.assertEqual(code, 0, out + err)
        self.assertIn('PASS  git diff --check (unstaged)', out)
        self.assertFalse(self.marker.exists())
        baseline = execution.snapshot(self.repo)
        (self.repo / 'notes.txt').write_text('ALSO\n')
        with self.assertRaisesRegex(execution.NattaError, 'Repository integrity changed'):
            execution.assert_integrity(self.repo, baseline, 'fixture change')
        self.assertFalse(self.marker.exists())


if __name__ == '__main__':
    unittest.main()
