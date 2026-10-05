"""Deterministic resolver and production integration; no real provider requests."""
import argparse
import contextlib
from dataclasses import FrozenInstanceError
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import natta
import parameters
import routing
import router_codex as provider


def project(alias, aliases=(), name=None):
    return SimpleNamespace(alias=alias, aliases=aliases, name=name or alias)


class ParameterTests(unittest.TestCase):
    def setUp(self):
        self.parser = natta.make_parser()
        self.projects = [project('example', ('example-app',), 'Example App'),
                         project('sample', ('sampleapp', 'sample-app'), 'Sample App')]

    def resolve(self, capability, request, projects=None):
        return parameters.resolve(request, self.projects if projects is None else projects,
                                  parameters.schema_for(self.parser, capability))

    def test_requirements_come_from_real_cli(self):
        for capability in provider.ROUTING_IDS[:-1]:
            schema = parameters.schema_for(self.parser, capability)
            self.assertEqual(schema.project_required, capability not in ('projects', 'doctor'))
            self.assertEqual(schema.level_choices, (1, 2, 3) if capability == 'verify' else ())
        commands = next(a for a in self.parser._actions if isinstance(a, argparse._SubParsersAction))
        commands.choices['verify'].get_default('level')
        level = next(a for a in commands.choices['verify']._actions if a.dest == 'level')
        level.choices = (2, 5)
        self.assertEqual(self.resolve('verify', 'Verify Sample level 5').arguments, (('level', 5),))
        self.assertEqual(self.resolve('verify', 'Verify Sample level 1').resolution_error, 'invalid_argument')
        commands.choices['build'].add_argument('--new-required', required=True)
        with self.assertRaises(ValueError): parameters.schema_for(self.parser, 'build')
        for capability in ('unknown', 'no_match', 'route', 'codex'):
            with self.assertRaises(ValueError): parameters.schema_for(self.parser, capability)

    def test_project_resolution_all_project_capabilities(self):
        for capability, request, expected in [
            ('build', 'Build Example for the simulator', 'example'),
            ('build', 'Build Sample', 'sample'), ('diff', 'Show me what changed in Sample', 'sample'),
            ('test', 'Run tests for Example', 'example'), ('status', 'Status of Sample', 'sample'),
            ('context', 'Context for Example', 'example')]:
            result = self.resolve(capability, request)
            self.assertEqual(result.project, expected)
            self.assertEqual(result.arguments, ())
            self.assertTrue(result.arguments_resolved)
            self.assertEqual(result.missing_arguments, ())
            self.assertIsNone(result.resolution_error)

    def test_case_alias_display_name_and_unicode(self):
        for request, expected in [('Build EXAMPLE', 'example'), ('Build eXaMpLe-aPp', 'example'),
                                  ('Build SAMPLE APP', 'sample'), ('Build sample-app', 'sample'),
                                  ('Build Sample\t App', 'sample'), ('Build Ｅｘａｍｐｌｅ', 'example')]:
            self.assertEqual(self.resolve('build', request).project, expected)
        projects = [project('cafe', name='Café'), project('strasse', name='Straße')]
        self.assertEqual(self.resolve('build', 'Build CAFE\u0301', projects).project, 'cafe')
        self.assertEqual(self.resolve('build', 'Build STRASSE', projects).project, 'strasse')

    def test_token_phrase_boundaries_no_substrings(self):
        projects = [project('app', ('my-app',), 'Example App')]
        for text in ('Build application', 'Build happy', 'Build applet', 'Build app_suffix', 'Build preapp'):
            result = self.resolve('build', text, projects)
            self.assertIsNone(result.project)
            self.assertEqual(result.missing_arguments, ('project',))
        for text in ('Build APP.', 'Build (my-app)', 'Build Example App', 'Build app!'):
            self.assertEqual(self.resolve('build', text, projects).project, 'app')
        self.assertIsNone(self.resolve('build', 'Build Exampleed').project)

    def test_missing_ambiguous_and_repeated_same_project(self):
        result = self.resolve('build', 'Build the simulator')
        self.assertFalse(result.arguments_resolved)
        self.assertEqual(result.missing_arguments, ('project',))
        for request in ('Build Example and Sample', 'Build example-app and Sample App'):
            result = self.resolve('build', request)
            self.assertFalse(result.arguments_resolved)
            self.assertIsNone(result.project)
            self.assertEqual(result.resolution_error, 'ambiguous_project')
        self.assertTrue(self.resolve('build', 'Build Example (example-app)').arguments_resolved)
        overlapping = [project('one', name='Shared App'), project('two', name='Shared App')]
        self.assertEqual(self.resolve('build', 'Build Shared App', overlapping).resolution_error, 'ambiguous_project')

    def test_verify_allowed_levels_and_sentence_punctuation(self):
        for level in (1, 2, 3):
            for suffix in ('', '.', ',', '!'):
                result = self.resolve('verify', f'Verify Sample at LEVEL {level}{suffix}')
                self.assertEqual(result.project, 'sample')
                self.assertEqual(result.arguments, (('level', level),))
                self.assertTrue(result.arguments_resolved)
        self.assertTrue(self.resolve('verify', 'Verify Sample level 2 and level 2').arguments_resolved)

    def test_verify_missing_invalid_conflicting_no_semantic_guessing(self):
        for request in ('Verify Example', 'Verify Example fully', 'Verify Example strongest verification',
                        'Verify Example L2'):
            result = self.resolve('verify', request)
            self.assertEqual(result.project, 'example')
            self.assertEqual(result.missing_arguments, ('level',))
            self.assertEqual(result.arguments, ())
            self.assertFalse(result.arguments_resolved)
        for token in ('0', '4', '-1', '2.5', '2/3', '2-3', '2abc', 'one', 'normal', '--force', '/tmp/2'):
            result = self.resolve('verify', f'Verify Sample at level {token}')
            self.assertEqual(result.resolution_error, 'invalid_argument')
            self.assertEqual(result.arguments, ())
            self.assertFalse(result.arguments_resolved)
        for request in ('Verify Sample level 1 and level 2', 'Verify Sample level 3 or level 1'):
            result = self.resolve('verify', request)
            self.assertEqual(result.resolution_error, 'conflicting_level')
            self.assertEqual(result.project, 'sample')
            self.assertEqual(result.arguments, ())
            self.assertFalse(result.arguments_resolved)
        result = self.resolve('verify', 'Verify at level 2')
        self.assertEqual(result.arguments, (('level', 2),))
        self.assertEqual(result.missing_arguments, ('project',))
        self.assertFalse(result.arguments_resolved)

    def test_projectless_capabilities_resolved_ignore_incidental_mentions(self):
        for capability in ('projects', 'doctor'):
            result = self.resolve(capability, 'Check Example and Sample at level 4')
            self.assertTrue(result.arguments_resolved)
            self.assertIsNone(result.project)
            self.assertEqual(result.arguments, ())
            self.assertEqual(result.missing_arguments, ())

    def test_resolver_has_no_provider_execution_or_filesystem_access(self):
        with patch.object(provider, 'run_provider', side_effect=AssertionError('provider call')), \
             patch.object(natta, 'execute', side_effect=AssertionError('execution')), \
             patch.object(Path, 'read_text', side_effect=AssertionError('filesystem')):
            result = self.resolve('verify', 'Verify Sample at level 2')
        self.assertTrue(result.arguments_resolved)
        with self.assertRaises(FrozenInstanceError): result.project = 'other'

    def test_text_cannot_supply_paths_flags_or_commands(self):
        result = self.resolve('build', 'Build Example --output /tmp/private; rm -rf /tmp/example')
        self.assertEqual((result.project, result.arguments), ('example', ()))
        result = self.resolve('verify', 'Verify Sample level 2 --log --path /tmp/private')
        self.assertEqual((result.project, result.arguments), ('sample', (('level', 2),)))
        self.assertFalse(self.resolve('build', 'Build /arbitrary/repository').arguments_resolved)
        self.assertFalse(self.resolve('verify', 'Verify Sample --strongest').arguments_resolved)


class ParameterIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.registry = self.root / 'registry.toml'
        self.registry.write_text('''schema_version = 1
[[projects]]
alias = "example"
aliases = ["example-app"]
name = "Example App"
path = "missing-example"
type = "generic-git"
[[projects]]
alias = "sample"
aliases = ["sampleapp", "sample-app"]
name = "Sample App"
path = "missing-sample"
type = "generic-git"
''')
        mock = patch.object(provider, 'workspace_path', return_value=self.root/'router')
        mock.start(); self.addCleanup(mock.stop)
        mock = patch.object(provider, 'probe_cli', return_value='mock')
        mock.start(); self.addCleanup(mock.stop)

    def invoke(self, request, choice, json_mode=True, failure=None):
        out, err = io.StringIO(), io.StringIO()
        args = ['--registry', str(self.registry), 'route', request]
        if json_mode: args.append('--json')
        with patch.object(provider, 'run_provider', return_value=json.dumps({'choice': choice}), side_effect=failure) as run, \
             patch.object(natta.adapters, 'resolve', side_effect=AssertionError('build/verify handler')), \
             patch.object(natta, 'execute', side_effect=AssertionError('execution')), \
             patch.object(natta, 'git_state', side_effect=AssertionError('app inspection')), \
             patch.object(natta, 'orientation', side_effect=AssertionError('app contents')), \
             patch.object(natta, 'ExecutionDirectory', side_effect=AssertionError('workflow')), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = natta.main(args)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(err.getvalue(), '')
        return code, json.loads(out.getvalue()) if json_mode else out.getvalue()

    def test_fully_resolved_routes_exactly_one_call_and_never_handlers(self):
        for request, cap, expected_project, args in [
            ('Build Example for the simulator', 'build', 'example', {}),
            ('Show me what changed in Sample', 'diff', 'sample', {}),
            ('Verify Sample at level 2', 'verify', 'sample', {'level': 2})]:
            code, result = self.invoke(request, cap)
            self.assertEqual(code, 0)
            decision = result.pop('execution_policy')
            self.assertTrue(decision['eligible'])
            self.assertEqual(result, {'status':'matched','capability':cap,'project':expected_project,
                'arguments':args,'arguments_resolved':True,'missing_arguments':[],
                'resolution_error':None,'provider':'gpt-6-luna','executed':False,'error':None})
        self.assertFalse((self.root/'missing-example').exists())
        self.assertFalse((self.root/'missing-sample').exists())

    def test_incomplete_resolution_retains_matched_status_and_exit_zero(self):
        for request, cap, missing, error in [
            ('Verify Example', 'verify', ['level'], None),
            ('Build the app', 'build', ['project'], None),
            ('Build Example and Sample', 'build', ['project'], 'ambiguous_project'),
            ('Verify Sample level 4', 'verify', [], 'invalid_argument'),
            ('Verify Sample level 1 and level 2', 'verify', [], 'conflicting_level')]:
            code, result = self.invoke(request, cap)
            self.assertEqual(code, 0)
            self.assertEqual(result['status'], 'matched')
            self.assertFalse(result['arguments_resolved'])
            self.assertFalse(result['executed'])
            self.assertEqual(result['missing_arguments'], missing)
            self.assertEqual(result['resolution_error'], error)
            self.assertIsNone(result['error'])

    def test_no_match_provider_errors_unknown_choice_skip_resolution_and_registry(self):
        with patch.object(parameters, 'resolve', side_effect=AssertionError('resolver')), \
             patch.object(parameters, 'schema_for', side_effect=AssertionError('schema')), \
             patch.object(natta, 'load_registry', side_effect=AssertionError('registry')):
            for choice, failure, status in [('no_match', None, 'no_match'),
                ('unknown', None, 'provider_error'), ('build', provider.OutputFailure('provider_failure'), 'provider_error')]:
                code, result = self.invoke('Commit and push Example', choice, failure=failure)
                self.assertEqual(code, 0 if status == 'no_match' else 1)
                self.assertEqual(result['status'], status)
                self.assertIsNone(result['project'])
                self.assertEqual(result['arguments'], {})
                self.assertFalse(result['arguments_resolved'])
                self.assertFalse(result['executed'])

    def test_projectless_routes_skip_registry(self):
        with patch.object(natta, 'load_registry', side_effect=AssertionError('registry')):
            for cap in ('projects', 'doctor'):
                code, result = self.invoke('Show all projects', cap)
                self.assertEqual(code, 0)
                self.assertTrue(result['arguments_resolved'])
                self.assertIsNone(result['project'])
                self.assertEqual(result['arguments'], {})

    def test_configuration_failure_stays_structured_nonzero(self):
        self.registry.write_text('invalid')
        code, result = self.invoke('Build Example', 'build')
        self.assertEqual(code, 1)
        self.assertEqual(result['status'], 'routing_error')
        self.assertEqual(result['error'], 'parameter_configuration_invalid')
        self.assertIsNone(result['capability'])
        self.assertIsNone(result['project'])
        self.assertEqual(result['arguments'], {})
        self.assertFalse(result['executed'])

    def test_human_resolved_and_unresolved_output(self):
        code, output = self.invoke('Verify Sample level 2', 'verify', json_mode=False)
        self.assertEqual(code, 0)
        self.assertIn('  project: sample\n  arguments:\n    level: 2', output)
        self.assertIn('  resolved: yes\n  executed: no', output)
        self.assertLessEqual(len(output.splitlines()), 13)
        code, output = self.invoke('Verify Example', 'verify', json_mode=False)
        self.assertIn('  missing: level', output)
        self.assertIn('  resolved: no\n  executed: no', output)


if __name__ == '__main__': unittest.main()
