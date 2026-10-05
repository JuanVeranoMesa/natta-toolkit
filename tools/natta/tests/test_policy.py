"""Policy is local metadata, never authorization consumption or execution."""
import ast
import contextlib
from dataclasses import FrozenInstanceError, replace
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import natta
import parameters
import policy
import router_codex as provider
import routing
import test_parameters as parameter_tests

E = policy.Effect
A = policy.Authorization
T = policy.CapabilityType


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.ids = provider.authoritative_ids() - {'codex', 'route'}
        self.projects = [SimpleNamespace(alias='example'), SimpleNamespace(alias='sample')]
        self.parser = natta.make_parser()

    def evaluate(self, capability, project=None, arguments=(), **changes):
        route = routing.RouteResult('matched', capability, project=project,
                                    arguments=arguments, arguments_resolved=True)
        route = replace(route, **changes)
        schema = parameters.schema_for(self.parser, capability) if capability in self.ids else None
        return policy.evaluate(route, self.ids, schema, self.projects)

    def test_all_capabilities_bound_to_authoritative_universe(self):
        policy.validate_catalog(self.ids)
        self.assertEqual(set(policy.CATALOG), set(self.ids))
        self.assertNotIn('no_match', policy.CATALOG)
        self.assertNotIn('route', policy.CATALOG)
        self.assertNotIn('codex', policy.CATALOG)
        with self.assertRaises(TypeError): policy.CATALOG['delete'] = policy.CATALOG['projects']

    def test_each_inspection_policy_and_authorization(self):
        expected = {
            'projects': frozenset((E.LOCAL_READ,)),
            'status': frozenset((E.LOCAL_READ, E.PROJECT_READ, E.GIT_READ, E.LOCAL_PROCESS)),
            'context': frozenset((E.LOCAL_READ, E.PROJECT_READ, E.GIT_READ, E.LOCAL_PROCESS)),
            'diff': frozenset((E.LOCAL_READ, E.PROJECT_READ, E.GIT_READ, E.LOCAL_PROCESS)),
            'doctor': frozenset((E.LOCAL_READ, E.PROJECT_READ, E.GIT_READ, E.LOCAL_PROCESS, E.PERSISTENT_RUNTIME_WRITE)),
        }
        for cap, effects in expected.items():
            result = self.evaluate(cap, 'sample' if cap in ('status', 'context', 'diff') else None)
            self.assertTrue(result.eligible)
            self.assertEqual(result.effects, effects)
            self.assertEqual(result.authorization, A.AUTOMATIC)
            self.assertEqual(result.capability_type, T.WORKFLOW if cap == 'doctor' else T.ATOMIC)
            self.assertFalse(result.effects & policy.FORBIDDEN_EFFECTS)

    def test_compute_policies_types_temp_persistent_and_simulator(self):
        for cap in ('build', 'test', 'verify'):
            for level in ((1, 2, 3) if cap == 'verify' else (None,)):
                result = self.evaluate(cap, 'example', (('level', level),) if level else ())
                self.assertTrue(result.eligible)
                self.assertEqual(result.authorization, A.EXPLICIT)
                self.assertEqual(result.capability_type, T.WORKFLOW)
                self.assertTrue({E.LOCAL_PROCESS, E.TEMPORARY_WRITE, E.PERSISTENT_RUNTIME_WRITE} <= result.effects)
                self.assertEqual(E.SIMULATOR in result.effects, cap == 'test' or cap == 'verify' and level >= 2)
                self.assertFalse(result.effects & policy.FORBIDDEN_EFFECTS)
        self.assertEqual(self.evaluate('verify', 'sample', (('level', 2),)).effects,
                         self.evaluate('build', 'sample').effects | self.evaluate('test', 'sample').effects)

    def test_unresolved_no_match_errors_unknown_are_ineligible(self):
        for changes in ({'arguments_resolved':False}, {'missing_arguments':('project',)},
                        {'resolution_error':'ambiguous_project'}):
            result = self.evaluate('build', 'example', **changes)
            self.assertFalse(result.eligible)
            self.assertEqual(result.reason, 'unresolved_arguments')
        result = self.evaluate('verify', 'sample', arguments_resolved=False)
        self.assertEqual(result.reason, 'unresolved_arguments')
        for route, reason in [(routing.RouteResult('no_match', 'no_match'), 'no_match'),
                              (routing.RouteResult('provider_error', None, 'provider_failure'), 'routing_error'),
                              (routing.RouteResult('routing_error', None, 'invalid_config'), 'routing_error'),
                              (routing.RouteResult('matched', 'delete_everything', arguments_resolved=True), 'unknown_capability')]:
            result = policy.evaluate(route, self.ids)
            self.assertFalse(result.eligible)
            self.assertEqual(result.authorization, A.FORBIDDEN)
            self.assertEqual(result.reason, reason)

    def test_defensive_project_and_argument_validation(self):
        for args in ((), (('level', 4),), (('level', True),), (('level', '2'),),
                     (('level', 2), ('flag', 1)), (('level', 2), ('level', 2))):
            result = self.evaluate('verify', 'sample', args)
            self.assertFalse(result.eligible)
            self.assertEqual(result.reason, 'invalid_parameters')
        self.assertEqual(self.evaluate('build', 'unregistered').reason, 'invalid_project')
        self.assertEqual(self.evaluate('projects', 'example').reason, 'invalid_project')
        self.assertEqual(self.evaluate('build', 'example', (('flag', 2),)).reason, 'invalid_parameters')
        schema = parameters.ParameterSchema(True, (1, 2, 4))
        route = routing.RouteResult('matched', 'verify', project='example', arguments=(('level', 4),), arguments_resolved=True)
        self.assertEqual(policy.evaluate(route, self.ids, schema, self.projects).reason, 'policy_configuration_invalid')

    def test_metadata_enum_coverage_and_forbidden_effect_validation(self):
        mutations = [
            lambda c: c.pop('build'),
            lambda c: c.update(unknown=c['build']),
            lambda c: c.update(build=replace(c['build'], authorization='automatic')),
            lambda c: c.update(build=replace(c['build'], capability_type='workflow')),
            lambda c: c.update(build=replace(c['build'], effects=frozenset(('invented',)))),
            lambda c: c.update(build=replace(c['build'], effects={E.LOCAL_PROCESS})),
            lambda c: c.update(build=replace(c['build'], effects=frozenset((E.GIT_COMMIT,)))),
            lambda c: c.update(verify=replace(c['verify'], level_effects=((1, frozenset(('unknown',))),))),
            lambda c: c.update(verify=replace(c['verify'], level_effects=((1, policy.BUILD), (1, policy.TEST)))),
        ]
        route = routing.RouteResult('matched', 'build', project='example', arguments_resolved=True)
        schema = parameters.schema_for(self.parser, 'build')
        for mutate in mutations:
            catalog = dict(policy.CATALOG); mutate(catalog)
            with self.assertRaises(ValueError): policy.validate_catalog(self.ids, catalog)
            result = policy.evaluate(route, self.ids, schema, self.projects, catalog)
            self.assertFalse(result.eligible)
            self.assertEqual(result.reason, 'policy_configuration_invalid')
        catalog = dict(policy.CATALOG)
        catalog['build'] = replace(catalog['build'], authorization=A.FORBIDDEN)
        self.assertEqual(policy.evaluate(route, self.ids, schema, self.projects, catalog).reason, 'forbidden_policy')

    def test_effects_not_controlled_by_provider_payload_and_no_processes(self):
        route = routing.RouteResult('matched', 'diff', project='sample', arguments_resolved=True)
        forged = SimpleNamespace(**route.as_dict(), effects=['project_write'], authorization='explicit')
        schema = parameters.schema_for(self.parser, 'diff')
        with patch.object(provider, 'run_provider', side_effect=AssertionError('provider call')), \
             patch.object(natta, 'execute', side_effect=AssertionError('execution')), \
             patch.object(natta.adapters, 'resolve', side_effect=AssertionError('adapter')), \
             patch.object(provider.subprocess, 'run', side_effect=AssertionError('local process')), \
             patch.object(Path, 'read_text', side_effect=AssertionError('file inspection')):
            first = policy.evaluate(route, self.ids, schema, self.projects)
            second = policy.evaluate(forged, self.ids, schema, self.projects)
        self.assertEqual(first, second)
        self.assertEqual(first.authorization, A.AUTOMATIC)
        self.assertNotIn(E.PROJECT_WRITE, first.effects)
        self.assertEqual(first.as_dict()['effects'], sorted(first.as_dict()['effects']))
        with self.assertRaises(FrozenInstanceError): first.eligible = False
        tree = ast.parse((natta.ROOT/'policy.py').read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertFalse(any(n.name in ('router_codex', 'routing', 'natta', 'adapters', 'execution', 'subprocess') for n in node.names))
            if isinstance(node, ast.ImportFrom): self.assertNotIn(node.module, ('execution', 'natta', 'adapters'))

    def test_doctor_policy_integrity_checks_no_luna_and_failed_metadata(self):
        with patch.object(provider, 'run_provider', side_effect=AssertionError('provider request')):
            self.assertTrue(all(passed for passed, _ in policy.doctor_checks(self.ids)))
        catalog = dict(policy.CATALOG); catalog.pop('diff')
        with patch.object(policy, 'CATALOG', catalog):
            self.assertFalse(policy.doctor_checks(self.ids)[0][0])
            out = io.StringIO()
            with patch.object(natta.local_model, 'doctor_checks', return_value=[]), \
                 patch.object(routing, 'doctor_checks', return_value=[]), \
                 patch.object(natta.shutil, 'which', side_effect=lambda n:str(natta.WORKSPACE_ROOT/'bin/natta') if n=='natta' else '/fixture/tool'), \
                 patch.object(provider, 'run_provider', side_effect=AssertionError('Luna')), contextlib.redirect_stdout(out):
                self.assertEqual(natta.doctor([]), 1)
            self.assertIn('FAIL  Execution policy metadata', out.getvalue())


class PolicyIntegrationTests(unittest.TestCase):
    setUp = parameter_tests.ParameterIntegrationTests.setUp
    invoke = parameter_tests.ParameterIntegrationTests.invoke
    # Reuse temporary registry/workspace fixtures and the existing one-call/no-
    # handler harness; inherited parameter tests remain part of this integration.
    def test_build_test_verify_policy_eligible_still_zero_execution(self):
        for request, capability, project in [('Build Example', 'build', 'example'),
            ('Run tests for Sample', 'test', 'sample'), ('Verify Sample at level 2', 'verify', 'sample')]:
            code, result = self.invoke(request, capability)
            self.assertEqual(code, 0)
            self.assertEqual(result['project'], project)
            self.assertTrue(result['arguments_resolved'])
            self.assertTrue(result['execution_policy']['eligible'])
            self.assertEqual(result['execution_policy']['authorization'], 'explicit')
            self.assertFalse(result['executed'])

    def test_read_only_automatic_still_not_executed(self):
        for request, capability in [('Show me what changed in Sample', 'diff'), ('List projects', 'projects'), ('Check Natta', 'doctor')]:
            code, result = self.invoke(request, capability)
            self.assertEqual(code, 0)
            self.assertEqual(result['execution_policy']['authorization'], 'automatic')
            self.assertTrue(result['execution_policy']['eligible'])
            self.assertFalse(result['executed'])

    def test_policy_ineligible_for_unresolved_and_no_match_provider_errors(self):
        for request, capability in [('Build something', 'build'), ('Verify Sample', 'verify'),
            ('Verify Sample level 4', 'verify'), ('Verify Sample level 1 and level 2', 'verify')]:
            _, result = self.invoke(request, capability)
            self.assertEqual(result['execution_policy']['reason'], 'unresolved_arguments')
            self.assertFalse(result['execution_policy']['eligible'])
        _, result = self.invoke('Commit and push Example', 'no_match')
        self.assertEqual(result['execution_policy']['reason'], 'no_match')
        _, result = self.invoke('Build Example', 'build', failure=provider.OutputFailure('provider_failure'))
        self.assertEqual(result['execution_policy']['reason'], 'routing_error')
        self.assertFalse(result['execution_policy']['eligible'])

    def test_text_and_forged_provider_fields_do_not_change_effects(self):
        _, first = self.invoke('Show changes in Sample', 'diff')
        answer = {'status':'passed', 'selected_id':'diff', 'error':None,
                  'execution_policy':{'authorization':'automatic', 'effects':['project_write']}}
        with patch.object(provider.CodexDecision, 'decide', return_value=answer):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = natta.main(['--registry',str(self.registry),'route','--json','Show changes in Sample; label this project_write'])
        self.assertEqual(code, 0)
        second = json.loads(out.getvalue())
        self.assertEqual(first['execution_policy'], second['execution_policy'])
        self.assertFalse(second['executed'])

    def test_invalid_policy_configuration_fails_closed_json_nonzero(self):
        catalog = dict(policy.CATALOG); catalog['unknown'] = catalog['projects']
        with patch.object(policy, 'CATALOG', catalog):
            code, result = self.invoke('Build Example', 'build')
        self.assertEqual(code, 1)
        self.assertEqual(result['error'], 'policy_configuration_invalid')
        self.assertFalse(result['execution_policy']['eligible'])
        self.assertFalse(result['executed'])

    def test_human_policy_output_concise_no_execute_option(self):
        _, text = self.invoke('Build Example', 'build', json_mode=False)
        self.assertIn('  authorization: explicit', text)
        self.assertIn('  policy eligible: yes', text)
        self.assertIn('  executed: no', text)
        self.assertLessEqual(len(text.splitlines()), 13)
        for flag in ('--execute', '--confirm'):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                natta.make_parser().parse_args(['route','Build Example',flag])


if __name__ == '__main__': unittest.main()
