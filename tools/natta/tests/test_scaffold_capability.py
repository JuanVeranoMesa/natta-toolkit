"""scaffold_capability.py writes only new placeholder files that cannot pass parity."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import capability_integration
import natta
import scaffold_capability


class ScaffoldTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / 'config').mkdir(); (self.root / 'tests').mkdir()

    def scaffold(self, name, kind, **options):
        out = io.StringIO()
        paths = scaffold_capability.scaffold(name, kind, root=self.root, out=out, **options)
        return paths, out.getvalue()

    def test_writes_placeholders_and_prints_the_checklist(self):
        (routing, test), out = self.scaffold('lint', 'inspection', planner=True)
        data = json.loads(routing.read_text())
        self.assertEqual(set(data), {'schema_version', 'capability', 'description'})
        self.assertEqual((data['schema_version'], data['capability']), (1, 'lint'))
        self.assertIn('TODO(scaffold)', data['description'])
        self.assertEqual(sorted(p.name for p in self.root.rglob('*') if p.is_file()),
                         ['routing-lint-v1.json', 'test_capability_lint.py'])
        for expected in ("'lint': Capability('inspection', planner=True)",
                         'CapabilityPolicy(CapabilityType.ATOMIC, Authorization.AUTOMATIC, PROJECT_INSPECTION)',
                         "('lint', 'config/routing-lint-v1.json', 1, ())",
                         'INSPECTION_TARGETS', 'inspection_compatibility.json', 'planner_provider.py',
                         "'lint': run_lint", "'lint': handle_lint", 'capability_integration.py'):
            self.assertIn(expected, out)
        # The skeleton is a real, failing test until it is written.
        proc = subprocess.run([sys.executable, '-B', str(test)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn('TODO(scaffold): write the lint capability tests', proc.stderr)

    def test_kind_defaults(self):
        _, out = self.scaffold('rebuild', 'workflow')
        self.assertIn('Authorization.EXPLICIT, BUILD', out)
        self.assertIn('WORKFLOW_TARGETS', out)
        self.assertIn('authorization.action_notice', out)
        self.assertNotIn('planner_provider.py', out)
        _, out = self.scaffold('about', 'unvalidated')
        self.assertIn("'natta about', 'none')", out)
        self.assertIn('Authorization.AUTOMATIC, READ', out)
        self.assertNotIn('confinement.py', out)

    def test_refusals(self):
        for args, message in ((('diff', 'inspection'), 'already a command'),
                              (('Bad-Name', 'inspection'), 'Invalid capability name'),
                              (('about', 'unvalidated'), 'planner-eligible')):
            with self.subTest(args=args), self.assertRaisesRegex(SystemExit, message):
                self.scaffold(*args, **({'planner': True} if args[0] == 'about' else {}))
        self.scaffold('lint', 'inspection')
        with self.assertRaisesRegex(SystemExit, 'Refusing to overwrite'):
            self.scaffold('lint', 'inspection')

    def test_parity_rejects_an_unreviewed_placeholder(self):
        (routing, _), _ = self.scaffold('lint', 'inspection')
        with patch.object(capability_integration, 'routing_files', return_value=(str(routing),)):
            with self.assertRaisesRegex(ValueError, 'unreviewed scaffold placeholder'):
                capability_integration.validate(natta.make_parser(), natta.handler_bindings(), natta.DIRECT_HANDLERS)

    def test_shipped_sources_contain_no_placeholder(self):
        source = Path(capability_integration.__file__).resolve().parent
        for path in (*source.glob('config/*.json'), *source.glob('tests/test_capability_*.py')):
            self.assertNotIn(b'TODO(scaffold)', path.read_bytes(), path)


if __name__ == '__main__':
    unittest.main()
