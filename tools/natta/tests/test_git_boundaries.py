"""Legacy snapshot scope/streaming, fsmonitor closure and staged-gitlink commit refusal.

Disposable repositories with an isolated HOME; harmless marker hooks/filters only.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import commits
import execution
import natta
import test_git_filters as filters


class LegacySnapshotScopeTests(unittest.TestCase):
    setUp = filters.GitFilterTests.setUp
    git = filters.GitFilterTests.git
    direct = filters.GitFilterTests.direct

    def ignore(self, text='build/\n*.cache\n'):
        (self.repo / '.gitignore').write_text(text)
        self.git('add', '.gitignore'); self.git('commit', '-q', '-m', 'Ignore outputs')

    def test_tracked_and_nonignored_untracked_changes_are_detected(self):
        self.ignore()
        before = execution.snapshot(self.repo)
        (self.repo / 'notes.txt').write_text('changed\n')
        with self.assertRaisesRegex(natta.NattaError, 'notes.txt'):
            execution.assert_integrity(self.repo, before, 'fixture')
        (self.repo / 'notes.txt').write_text('text\n')
        execution.assert_integrity(self.repo, before, 'restored')
        (self.repo / 'new.txt').write_text('new\n')
        self.assertIn(b'new.txt', execution.snapshot(self.repo)['files'])
        with self.assertRaisesRegex(natta.NattaError, 'new.txt'):
            execution.assert_integrity(self.repo, before, 'fixture')

    def test_ignored_files_are_outside_the_snapshot(self):
        self.ignore()
        (self.repo / 'build').mkdir(); (self.repo / 'build/output.o').write_bytes(b'object')
        (self.repo / 'data.cache').write_text('cache')
        before = execution.snapshot(self.repo)
        self.assertEqual(set(before['files']), {b'.gitignore', b'notes.txt', b'image.bin'})
        (self.repo / 'build/output.o').write_bytes(b'rebuilt')
        (self.repo / 'build/new.o').write_bytes(b'new')
        (self.repo / 'data.cache').unlink()
        execution.assert_integrity(self.repo, before, 'ignored churn')

    def test_large_files_are_hashed_in_chunks_without_read_bytes(self):
        payload = os.urandom(10 * 1024 + 3)
        (self.repo / 'large.bin').write_bytes(payload)
        self.git('add', 'large.bin'); self.git('commit', '-q', '-m', 'Large')
        sizes = []
        real = hashlib.sha256
        class Recording:
            def __init__(self, data=b''):
                self.inner = real(data)
            def update(self, chunk):
                sizes.append(len(chunk)); self.inner.update(chunk)
            def hexdigest(self):
                return self.inner.hexdigest()
        with patch.object(execution, 'HASH_CHUNK', 1024), \
             patch.object(Path, 'read_bytes', side_effect=AssertionError('whole-file read')), \
             patch.object(execution.hashlib, 'sha256', Recording):
            self.assertEqual(execution.file_digest(self.repo / 'large.bin'), real(payload).hexdigest())
            self.assertEqual(max(sizes), 1024); self.assertGreater(len(sizes), 10)
            snapshot = execution.snapshot(self.repo)
        self.assertEqual(snapshot['files'][b'large.bin'], real(payload).hexdigest())

    def test_repeated_workflow_snapshots_preserve_integrity(self):
        self.ignore()
        quiet = execution.Check('Writes ignored output', (sys.executable, '-c',
            "import pathlib; p=pathlib.Path('build'); p.mkdir(exist_ok=True); (p/'out').write_text('x')"))
        mutate = execution.Check('Edits tracked file', (sys.executable, '-c',
            "import pathlib; pathlib.Path('notes.txt').write_text('mutated')"))
        with patch.object(natta.adapters.GenericGit, 'plan', return_value=[quiet, quiet]):
            code, out, err = self.direct('verify', 'fixture', '--level', '1')
        self.assertEqual(code, 0, err); self.assertIn('Result: passed', out)
        with patch.object(natta.adapters.GenericGit, 'plan', return_value=[quiet, mutate]):
            code, out, err = self.direct('verify', 'fixture', '--level', '1')
        self.assertEqual(code, 1)
        self.assertIn("Repository integrity changed after workflow: ['notes.txt']", err)

    def test_filters_and_submodules_remain_refused_before_sensitive_git(self):
        self.git('config', 'filter.custom.clean', self.command)
        (self.repo / '.gitattributes').write_text('*.txt filter=custom\n')
        with self.assertRaises(execution.ExternalFilterConfigured):
            execution.snapshot(self.repo)
        (self.repo / '.gitattributes').unlink()
        filters.add_filtered_submodule(self.repo, self.root / 'source')
        with self.assertRaises(execution.SubmoduleUnsupported):
            execution.snapshot(self.repo)
        self.assertFalse(self.marker.exists())


class FsmonitorTests(unittest.TestCase):
    setUp = filters.GitFilterTests.setUp
    git = filters.GitFilterTests.git
    direct = filters.GitFilterTests.direct

    def install_hook(self):
        hook = self.root / 'fsmonitor-hook'
        hook.write_text('#!/bin/sh\ntouch ' + json.dumps(str(self.marker)) + '\nexit 1\n')
        hook.chmod(0o755)
        self.git('config', 'core.fsmonitor', str(hook))

    def test_plain_git_status_would_execute_the_hook(self):
        self.install_hook()
        self.git('status', '--porcelain')
        self.assertTrue(self.marker.exists())

    def test_legacy_snapshot_and_direct_workflows_never_execute_it(self):
        self.install_hook()
        before = execution.snapshot(self.repo)
        self.assertFalse(self.marker.exists())
        for argv in (('build', 'fixture'), ('test', 'fixture'), ('verify', 'fixture', '--level', '1'),
                     ('status', 'fixture'), ('diff', 'fixture')):
            with self.subTest(argv=argv):
                code, out, err = self.direct(*argv)
                # generic-git build/test report an unavailable workflow (exit 1), never an error.
                self.assertEqual((code, err), (1 if argv[0] in ('build', 'test') else 0, ''))
                self.assertFalse(self.marker.exists())
        # Integrity detection still works with the hook configured.
        (self.repo / 'notes.txt').write_text('changed\n')
        with self.assertRaisesRegex(natta.NattaError, 'notes.txt'):
            execution.assert_integrity(self.repo, before, 'fixture')
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.git('config', 'core.fsmonitor').decode().strip(), str(self.root / 'fsmonitor-hook'))


class EmbeddedRepositoryCommitTests(unittest.TestCase):
    """P1F-001: git add -A stages an untracked nested repository as a gitlink."""
    setUp = filters.GitFilterTests.setUp
    git = filters.GitFilterTests.git

    def nested_repository(self):
        nested = self.repo / 'nested'
        nested.mkdir()
        filters.git_without_ambient(nested, 'init', '-q', '-b', 'main')
        (nested / 'inner.txt').write_text('inner\n')
        (nested / '.gitattributes').write_text('* filter=custom\n')
        filters.git_without_ambient(nested, 'add', '.')
        filters.git_without_ambient(nested, 'commit', '-q', '-m', 'Nested')
        # Only the nested repository's own config/hooks would run these.
        filters.git_without_ambient(nested, 'config', 'filter.custom.clean', self.command)
        for hook in ('pre-commit', 'post-checkout', 'post-index-change'):
            path = nested / '.git/hooks' / hook
            path.write_text('#!/bin/sh\ntouch ' + json.dumps(str(self.marker)) + '\n'); path.chmod(0o755)
        os.utime(nested / 'inner.txt', (1, 1))
        return nested

    def test_staged_gitlink_refused_before_git_commit(self):
        self.nested_repository()
        (self.repo / 'notes.txt').write_text('changed\n')
        head = self.git('rev-parse', 'HEAD').strip()
        calls = []
        real = commits.git
        def record(project, *args):
            calls.append(args); return real(project, *args)
        with patch.object(commits, 'git', side_effect=record):
            result = commits.create(self.project)
        self.assertEqual((result.status, result.error), ('failed', 'submodule_commit_unsupported'))
        self.assertIs(result.committed, False)
        self.assertIsNone(result.commit_hash)
        self.assertTrue(result.execution_started); self.assertFalse(result.git_commit_started)
        self.assertEqual(result.exit_code, 1)
        self.assertIn('index was left staged', result.detail)
        self.assertEqual(result.effect_verification.reason, 'submodule_unsupported')
        self.assertIn(('add', '-A', '--', '.'), calls)
        self.assertFalse(any(args[:1] == ('commit',) for args in calls))
        # No commit; the index truthfully holds the staged gitlink and change.
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), head)
        staged = self.git('ls-files', '--stage', '-z').split(b'\0')
        self.assertTrue(any(entry.startswith(b'160000 ') and entry.endswith(b'\tnested') for entry in staged))
        self.assertIn(b'notes.txt', self.git('diff', '--cached', '--name-only'))
        self.assertFalse(self.marker.exists())
        rendered = commits.render(result, self.project)
        self.assertIn('FAIL  submodule_commit_unsupported', rendered)
        self.assertIn('Natta does not roll back', rendered)
        self.assertNotIn('Commit created', rendered)

    def test_cli_reports_refusal_and_uncommitted_state(self):
        self.nested_repository()
        code, out, err = filters.GitFilterTests.direct(self, 'commit', 'fixture', '--json')
        data = json.loads(out)
        self.assertEqual((code, data['error'], data['committed'], data['git_commit_started']),
                         (1, 'submodule_commit_unsupported', False, False))
        self.assertFalse(self.marker.exists())
        code, out, err = filters.GitFilterTests.direct(self, 'commit', 'fixture')
        self.assertEqual(code, 1)
        self.assertIn('submodule_commit_unsupported', out)
        self.assertNotIn('Commit created', out)


if __name__ == '__main__':
    unittest.main()
