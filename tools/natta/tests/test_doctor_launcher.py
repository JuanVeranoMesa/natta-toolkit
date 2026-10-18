"""Launcher resolution, doctor grouping and registry adapter validation (NTT-007/NTT-011)."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import adapters
import natta

LAUNCHER = natta.WORKSPACE_ROOT / 'bin/natta'


class LauncherDoctorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.home = self.root / 'home'; self.home.mkdir()
        self.repo = self.root / 'repo'; self.repo.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True, env=natta.git_environment())
        self.registry = self.root / 'projects.toml'
        self.registry.write_text('schema_version = 1\n[[projects]]\nalias = "example"\nname = "Example App"\n'
                                 'type = "generic-git"\npath = ' + json.dumps(str(self.repo)) + '\n')
        # PATH with Python and Git but no natta launcher and no semantic provider.
        self.tools = self.root / 'tools'; self.tools.mkdir()
        (self.tools / 'python3').symlink_to(sys.executable)
        self.base_path = os.pathsep.join((str(self.tools), '/usr/bin', '/bin'))

    def run_launcher(self, launcher, *args, extra_path=None, extra_env=None):
        env = {**natta.git_environment(), 'HOME': str(self.home),
               'PATH': os.pathsep.join(filter(None, (extra_path, self.base_path))), **(extra_env or {})}
        return subprocess.run([str(launcher), '--registry', str(self.registry), *args],
                              capture_output=True, text=True, env=env, timeout=120)

    def doctor(self, launcher, extra_path=None):
        proc = self.run_launcher(launcher, 'doctor', '--json', extra_path=extra_path)
        self.assertEqual(proc.stderr, '')
        data = json.loads(proc.stdout)
        row = next(r for r in data['checks'] if r['name'].startswith('Launcher on PATH'))
        return proc.returncode, data, row

    def test_full_path_invocation_without_path_install_is_core_ready(self):
        code, data, row = self.doctor(LAUNCHER)
        self.assertEqual((code, data['core_ready'], data['error']), (0, True, None))
        self.assertEqual((row['group'], row['optional'], row['status']), ('integration', True, 'unavailable'))
        self.assertTrue(row['name'].endswith('missing'))
        self.assertTrue(all(r['status'] == 'passed' for r in data['checks'] if r['group'] == 'core'))
        self.assertEqual({r['group'] for r in data['checks']}, {'core', 'provider', 'integration'})
        self.assertTrue(all(r['optional'] == (r['group'] != 'core') for r in data['checks']))

    def test_launcher_on_path_is_recognized(self):
        code, data, row = self.doctor('natta', extra_path=str(LAUNCHER.parent))
        self.assertEqual((code, data['core_ready'], row['status']), (0, True, 'passed'))

    def test_symlinked_launcher_resolves_checkout(self):
        bindir = self.root / 'local-bin'; bindir.mkdir()
        chain = self.root / 'chain'; chain.mkdir()
        (chain / 'natta').symlink_to(LAUNCHER)
        (bindir / 'natta').symlink_to(Path('..') / 'chain' / 'natta')  # relative, two hops
        proc = self.run_launcher(bindir / 'natta', 'projects')
        self.assertEqual((proc.returncode, proc.stderr), (0, ''))
        self.assertIn('Example App', proc.stdout)
        code, data, row = self.doctor('natta', extra_path=str(bindir))
        self.assertEqual((code, data['core_ready'], row['status']), (0, True, 'passed'))

    def test_foreign_launcher_on_path_is_integration_only(self):
        other = self.root / 'other-bin'; other.mkdir()
        (other / 'natta').write_text('#!/bin/sh\nexit 0\n'); (other / 'natta').chmod(0o755)
        code, data, row = self.doctor(LAUNCHER, extra_path=str(other))
        self.assertEqual((code, data['core_ready'], row['status']), (0, True, 'unavailable'))
        self.assertIn(str(other / 'natta'), row['name'])

    def test_broken_launcher_target_fails_truthfully(self):
        copy = self.root / 'copied' / 'natta'; copy.parent.mkdir()
        shutil.copy2(LAUNCHER, copy)
        proc = self.run_launcher(copy, '--help')
        self.assertEqual(proc.returncode, 1)
        self.assertIn('cannot find the toolkit checkout', proc.stderr)
        self.assertEqual(proc.stdout, '')
        # A symlink is followed to its real target, so a link to a stray copy
        # still reports the copy's missing checkout, not a Python traceback.
        linked = self.root / 'linked'; linked.mkdir()
        (linked / 'natta').symlink_to(copy)
        proc = self.run_launcher(linked / 'natta', '--help')
        self.assertEqual(proc.returncode, 1)
        self.assertIn('cannot find the toolkit checkout', proc.stderr)
        self.assertIn(str(copy.parent / '../tools/natta/natta.py'), proc.stderr)

    def fake_python(self, version):
        fake = self.root / 'old-python' / 'python3'; fake.parent.mkdir()
        # Mimics the launcher's version probe of an interpreter below the minimum.
        fake.write_text(f'#!/bin/sh\necho {version}\nexit 1\n'); fake.chmod(0o755)
        return fake

    def test_old_python_fails_with_a_message_not_a_traceback(self):
        fake = self.fake_python('3.9')
        proc = self.run_launcher(LAUNCHER, '--help', extra_path=str(fake.parent))
        self.assertEqual((proc.returncode, proc.stdout), (1, ''))
        self.assertIn(f'natta: Python 3.11+ is required; found 3.9 at {fake}', proc.stderr)
        self.assertNotIn('Traceback', proc.stderr)

    def test_natta_python_selects_the_interpreter(self):
        fake = self.fake_python('3.9')  # first python3 on PATH is too old
        proc = self.run_launcher(LAUNCHER, 'projects', extra_path=str(fake.parent),
                                 extra_env={'NATTA_PYTHON': sys.executable})
        self.assertEqual((proc.returncode, proc.stderr), (0, ''))
        self.assertIn('Example App', proc.stdout)
        proc = self.run_launcher(LAUNCHER, '--help', extra_env={'NATTA_PYTHON': str(self.root / 'missing')})
        self.assertEqual((proc.returncode, proc.stdout), (1, ''))
        self.assertIn('was not found (set NATTA_PYTHON', proc.stderr)

    def test_homebrew_formula_layout(self):
        """Reproduce Formula/natta.rb's install without Homebrew: inreplace, libexec copy, bin symlink."""
        import re
        formula = (natta.WORKSPACE_ROOT / 'Formula/natta.rb').read_text()
        anchor, = re.findall(r'inreplace "bin/natta", "([^"]+)"', formula)
        self.assertEqual(LAUNCHER.read_text().count(anchor), 1)
        libexec = self.root / 'Cellar/natta/1.1.0/libexec'; libexec.mkdir(parents=True)
        for name in re.search(r'libexec\.install (.+)', formula).group(1).replace('"', '').split(', '):
            source = natta.WORKSPACE_ROOT / name
            if source.is_dir():
                shutil.copytree(source, libexec / name, ignore=shutil.ignore_patterns('__pycache__'))
            else:
                shutil.copy2(source, libexec / name)
        launcher = libexec / 'bin/natta'
        launcher.write_text(launcher.read_text().replace(anchor, 'NATTA_PYTHON=${NATTA_PYTHON:-' + sys.executable + '}'))
        brew_bin = self.root / 'brew-bin'; brew_bin.mkdir()
        (brew_bin / 'natta').symlink_to(launcher)
        fake = self.fake_python('3.9')  # the PATH python3 is ignored by the installed default
        env = {**natta.git_environment(), 'HOME': str(self.home),
               'PATH': os.pathsep.join((str(brew_bin), str(fake.parent), '/usr/bin', '/bin'))}
        proc = subprocess.run(['natta', '--registry', str(self.registry), 'doctor', '--json'],
                              capture_output=True, text=True, env=env, timeout=120)
        data = json.loads(proc.stdout)
        row = next(r for r in data['checks'] if r['name'].startswith('Launcher on PATH'))
        self.assertEqual((proc.returncode, data['core_ready'], row['status']), (0, True, 'passed'))

    def test_human_doctor_sections(self):
        proc = self.run_launcher(LAUNCHER, 'doctor')
        self.assertEqual(proc.returncode, 0, proc.stdout)
        out = proc.stdout
        self.assertLess(out.index('Core\n'), out.index('Provider and model (optional)\n'))
        self.assertLess(out.index('Provider and model (optional)\n'), out.index('Platform and integration (optional)\n'))
        self.assertIn('UNAVAILABLE  Launcher on PATH resolves to this checkout: missing (optional)', out)
        self.assertIn('Core Natta: ready', out)


class RegistryAdapterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.registry = self.root / 'projects.toml'

    def write(self, type_line):
        self.registry.write_text('schema_version = 1\n[[projects]]\nalias = "app"\nname = "App"\npath = "app"\n' + type_line + '\n')

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = natta.main(['--registry', str(self.registry), *args])
        return code, out.getvalue(), err.getvalue()

    def test_each_supported_adapter_loads(self):
        self.assertEqual(set(adapters.ADAPTERS), {'generic-git', 'ios-xcode'})
        for kind in adapters.ADAPTERS:
            self.write(f'type = "{kind}"')
            self.assertEqual(natta.load_registry(self.registry)[0].type, kind)

    def test_unknown_typo_missing_and_malformed_types_fail_at_load(self):
        for line, message in (('type = "pyhton"', "unsupported adapter type 'pyhton'"),
                              ('type = "unknown"', "unsupported adapter type 'unknown'"),
                              ('type = "Generic-Git"', "unsupported adapter type 'Generic-Git'"),
                              ('', 'missing required or unknown fields'),
                              ('type = 3', 'type must be a nonempty string'),
                              ('type = ""', 'type must be a nonempty string')):
            self.write(line)
            with self.subTest(line=line), self.assertRaisesRegex(natta.NattaError, message):
                natta.load_registry(self.registry)
        self.write('type = "pyhton"')
        with self.assertRaisesRegex(natta.NattaError, 'supported: generic-git, ios-xcode'):
            natta.load_registry(self.registry)

    def test_project_commands_refuse_invalid_registry(self):
        self.write('type = "pyhton"')
        for args in (('projects',), ('status', 'app'), ('context', 'app'), ('build', 'app')):
            code, out, err = self.invoke(*args)
            self.assertEqual((code, out), (1, ''))
            self.assertIn("unsupported adapter type 'pyhton'", err)
        code, out, err = self.invoke('commit', 'app', '--json')
        self.assertEqual((code, json.loads(out)['error']), (1, 'cli_configuration_invalid'))

    def test_doctor_reports_the_configuration_problem(self):
        self.write('type = "pyhton"')
        code, out, err = self.invoke('doctor')
        self.assertEqual((code, err), (1, ''))
        self.assertIn("FAIL  Registry: Project app: unsupported adapter type 'pyhton'", out)
        self.assertIn('Core Natta: not ready', out)
        code, out, err = self.invoke('doctor', '--json')
        data = json.loads(out)
        self.assertEqual((code, data['core_ready'], data['error']), (1, False, 'registry_invalid'))
        first = data['checks'][0]
        self.assertEqual((first['group'], first['status']), ('core', 'failed'))
        self.assertIn('pyhton', first['name'])


if __name__ == '__main__':
    unittest.main()
