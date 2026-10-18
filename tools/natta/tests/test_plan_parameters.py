"""Plan/do projects and verify levels are reconciled with the goal locally (NTT-010)."""
import json
import unittest
from unittest.mock import patch

import natta
import planning
import planner_provider
import test_parameters as fixtures


def step(capability, project, level=None):
    return {'capability': capability, 'project': project, 'arguments': {} if level is None else {'level': level}}


class PlanParameterReconciliationTests(unittest.TestCase):
    setUp = fixtures.ParameterIntegrationTests.setUp

    def propose(self, goal, *steps):
        reply = planner_provider.Reply(json.dumps({'status': 'planned', 'steps': list(steps)}), external_requests=1)
        with patch.object(planner_provider.CURRENT, 'propose', return_value=reply) as provider:
            result = planning.propose(goal, natta.make_parser(), natta.load_registry(self.registry), natta.handler_bindings())
        provider.assert_called_once()  # One planner call; a mismatch never replans.
        return result

    def assertRejected(self, error, goal, *steps, invalid_step=None):
        result = self.propose(goal, *steps)
        self.assertEqual((result.status, result.error, result.invalid_step), ('invalid_plan', error, invalid_step))
        self.assertEqual(result.steps, ())
        self.assertEqual(result.external_requests, 1)

    def test_explicitly_named_projects_must_match(self):
        result = self.propose('Show Example App changes then build it', step('diff', 'example'), step('build', 'EXAMPLE'))
        self.assertTrue(result.valid)
        self.assertEqual([s.project for s in result.steps], ['example', 'example'])
        result = self.propose('Build Example and test Sample', step('build', 'example'), step('test', 'sample'))
        self.assertEqual([s.project for s in result.steps], ['example', 'sample'])

    def test_planner_cannot_substitute_another_registered_project(self):
        self.assertRejected('project_mismatch', 'Build Example', step('build', 'sample'), invalid_step=1)
        self.assertRejected('project_mismatch', 'Build Example then test it',
                            step('build', 'example'), step('test', 'sample'), invalid_step=2)

    def test_goal_without_a_registered_project_cannot_be_completed_by_the_planner(self):
        self.assertRejected('unresolved_project', 'Build it for the simulator', step('build', 'example'), invalid_step=1)
        self.assertRejected('unresolved_project', 'Build Production', step('build', 'sample'), invalid_step=1)

    def test_ambiguous_project_alternatives_are_rejected(self):
        self.assertRejected('ambiguous_project', 'Upload Example or Sample to TestFlight', step('testflight', 'example'))
        self.assertRejected('ambiguous_project', 'Show Example and Sample changes and then build it',
                            step('diff', 'example'), step('build', 'example'))

    def test_explicit_verify_level_must_match(self):
        result = self.propose('Verify Sample at level 2 then show its diff', step('verify', 'sample', 2), step('diff', 'sample'))
        self.assertEqual(result.steps[0].arguments, (('level', 2),))
        self.assertRejected('level_mismatch', 'Verify Sample at level 2', step('verify', 'sample', 3), invalid_step=1)
        self.assertRejected('level_mismatch', 'Verify Sample at level 1 then at level 3',
                            step('verify', 'sample', 1), step('verify', 'sample', 1))
        self.assertRejected('invalid_argument', 'Verify Sample at level 4', step('verify', 'sample', 3), invalid_step=1)

    def test_omitted_level_keeps_the_no_default_contract(self):
        # Levels are never inferred; verify without a stated level is not executable.
        self.assertRejected('missing_level', 'Thoroughly verify Sample', step('verify', 'sample', 3), invalid_step=1)

    def test_compound_order_unchanged_and_mismatch_starts_nothing(self):
        import test_compound_execution as compound
        case = compound.CompoundTests('test_two_three_four_steps_once_in_order')
        case.setUp()
        try:
            candidate = {'status': 'planned', 'steps': [step('diff', 'example'), step('build', 'example'),
                                                        step('verify', 'example', 2), step('commit', 'example')]}
            events = []
            handlers = case.bindings()
            for cap in ('diff', 'build', 'verify', 'commit'):
                handlers[cap].side_effect = lambda *args, cap=cap: (events.append(cap), compound.gate.HandlerOutcome(0))[1]
            code, data, _, _ = case.invoke(candidate=candidate, handlers=handlers, approve=True)
            self.assertEqual((code, events), (0, ['diff', 'build', 'verify', 'commit']))
            self.assertEqual(data['plan']['external_requests'], 1)
            wrong = {'status': 'planned', 'steps': [step('build', 'example'), step('commit', 'sample')]}
            with patch.object(case, 'goal', return_value='Build Example then commit it'):
                code, data, handlers, _ = case.invoke(candidate=wrong)
            self.assertEqual((code, data['status'], data['plan']['error']), (1, 'invalid_plan', 'project_mismatch'))
            self.assertFalse(any(h.called for h in handlers.values()))
        finally:
            case.doCleanups()

    def test_frozen_gold_plans_satisfy_reconciliation(self):
        projects = natta.load_registry(self.registry)
        corpora = (('planner-corpus-v1.json', planner_provider), ('commit-planner-v2-corpus.json', planner_provider.COMMIT_V2),
                   ('testflight-planner-v1-corpus.json', planner_provider.TESTFLIGHT_V1))
        for name, revision in corpora:
            for case in json.loads((natta.ROOT / 'evaluation' / name).read_text())['cases']:
                if case['expected']['status'] != 'planned':
                    continue
                with self.subTest(case=case['id']):
                    result = planning.validate(case['expected'], natta.make_parser(), projects, natta.handler_bindings(),
                                               provider_mode=revision, goal=case['request'])
                    self.assertTrue(result.valid, result.error)


if __name__ == '__main__':
    unittest.main()
