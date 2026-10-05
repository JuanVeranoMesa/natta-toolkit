"""Fixed order probe; all new provider calls mocked, synthetic canonical read only."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from collections import Counter

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE.parent / 'natta-local-model'))
import evaluate_codex as e

EXPECTED_IDS = '''clear_positive-06 clear_positive-04 clear_positive-05 clear_positive-14
natural_variation-09 natural_variation-08 natural_variation-02 natural_variation-11
project_variation-01 project_variation-07 project_variation-10 project_variation-06
ambiguous-06 ambiguous-03 ambiguous-08 ambiguous-02
out_of_scope-03 out_of_scope-01 out_of_scope-06
adversarial-09 adversarial-08 adversarial-05 regression-01 regression-02'''.split()


class StabilityTests(unittest.TestCase):
    def setUp(self):
        self.cases, _, _ = e.inputs()
        self.subset = e.stability_subset(self.cases)
        from test_codex_reference import Engine, info
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        reference_path = Path(self.tmp.name)/'synthetic-reference.json'
        self.reference = e.evaluate('reference', Engine(), info(), progress=lambda _: None)
        self.reference['synthetic_fixture'] = True
        reference_path.write_text(json.dumps(self.reference))
        target = patch.object(e, 'STABILITY_SOURCE', reference_path)
        target.start(); self.addCleanup(target.stop)
        self.canonical = e.validate_reference(self.reference)
        self.info = {'model': 'gpt-6-luna', 'source_sha256': e.router.source_hashes()}

    def engine(self, change=None, error=None):
        gold = {case['request']: case['expected_choice'] for case in self.cases}
        class Engine:
            model = 'gpt-6-luna'
            timeout = 120
            def __init__(self): self.calls = []
            def decide(self, request, name, order):
                self.calls.append((request, name, order))
                selected = change(request, order) if change else gold[request]
                return {'status': 'failed' if error else 'passed', 'selected_id': None if error else selected,
                        'error': error, 'inference_seconds': len(self.calls)}
        return Engine()

    def run_mock(self, engine):
        with patch.object(e.router.subprocess, 'Popen', side_effect=AssertionError('real provider')):
            return e.evaluate_stability(engine, self.info, source=e.STABILITY_SOURCE, progress=lambda _: None)

    def test_metadata_only_selection_allocation_and_determinism(self):
        self.assertEqual([c['id'] for c in self.subset], EXPECTED_IDS)
        self.assertEqual(len(self.subset), 24)
        self.assertEqual(Counter(c['category'] for c in self.subset), e.STABILITY_ALLOCATION)
        altered = [{'id': c['id'], 'category': c['category'], 'request': 'changed',
                    'expected_choice': 'doctor', 'notes': 'provider outputs unrelated'} for c in reversed(self.cases)]
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('canonical read')), \
             patch.object(Path, 'read_text', side_effect=AssertionError('provider read')):
            self.assertEqual([c['id'] for c in e.stability_subset(altered)], EXPECTED_IDS)
        for _ in range(3):
            self.assertEqual(e.stability_subset(self.cases), self.subset)

    def test_exact_new_plan_and_old_modes(self):
        plan = e.decisions(self.cases, 'stability')
        self.assertEqual(len(plan), 48)
        self.assertEqual(Counter(o for _, _, _, o, _ in plan), {'reversed': 24, 'shuffle-20260404': 24})
        for case, name, include, order_name, order in plan:
            self.assertEqual(name, 'concise')
            self.assertIs(include, True)
            self.assertIn('no_match', order)
            self.assertEqual(order, e.control.orders()[order_name])
        self.assertEqual(len(e.decisions(self.cases, 'full')), 864)
        self.assertEqual(len(e.decisions(self.cases, 'pilot')), 6)
        self.assertEqual(len(e.decisions(self.cases, 'reference')), 72)
        self.assertTrue(all(o == 'canonical' for _, _, _, o, _ in e.decisions(self.cases, 'reference')))

    def test_mock_run_calls_metrics_timing_and_immutable_inputs(self):
        files = [e.STABILITY_SOURCE, e.scoring.MANIFEST] + [e.control.RUNTIME / name for name in e.inputs()[2]]
        before = {path: path.read_bytes() for path in files}
        engine = self.engine()
        result = self.run_mock(engine)
        self.assertEqual(len(engine.calls), 48)
        self.assertEqual(Counter(tuple(order) for _, _, order in engine.calls),
            {tuple(e.control.orders()['reversed']): 24, tuple(e.control.orders()['shuffle-20260404']): 24})
        self.assertTrue(all(name == 'concise' and 'no_match' in order for _, name, order in engine.calls))
        self.assertTrue(result['complete'])
        self.assertEqual(result['total_decisions'], 48)
        self.assertEqual(result['infrastructure_failures'], 0)
        self.assertEqual(result['predeclared_thresholds'], e.STABILITY_THRESHOLDS)
        self.assertEqual(e.STABILITY_THRESHOLDS, {'maximum_failures': 0, 'minimum_fully_stable': 22,
            'preferred_fully_stable': 23, 'minimum_new_order_accuracy': .90,
            'position_bias_two_sided_p': .05, 'dangerous_order_induced_routes': 0,
            'regressions_build_all_orders': True})
        self.assertEqual(result['selected_case_ids'], EXPECTED_IDS)
        self.assertEqual(result['canonical_source']['sha256'], hashlib.sha256(before[e.STABILITY_SOURCE]).hexdigest())
        self.assertEqual(result['timing']['total_new_call_seconds'], sum(range(1, 49)))
        self.assertEqual(result['timing']['mean_new_call_seconds'], 24.5)
        self.assertEqual(result['timing']['median_new_call_seconds'], 24.5)
        self.assertEqual(result['timing']['min_new_call_seconds'], 1)
        self.assertEqual(result['timing']['max_new_call_seconds'], 48)
        self.assertEqual(before, {path: path.read_bytes() for path in files})
        metrics = result['metrics']
        self.assertEqual(len(metrics['three_order_selections']), 24)
        self.assertEqual(metrics['accuracy_by_order']['reversed']['accuracy'], 1)
        self.assertEqual(metrics['accuracy_by_order']['shuffle-20260404']['accuracy'], 1)
        self.assertEqual(len(metrics['regression_cases']), 2)
        self.assertEqual(len(metrics['adversarial_out_of_scope_cases']), 6)
        self.assertTrue(result['assessment']['passed'])
        self.assertTrue(all(set(t['selections']) == set(e.control.orders()) for t in metrics['three_order_selections']))

    def test_exact_authorization_model_and_canonical_before_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'result.json'
            for auth in (None, 0, 47, 49, 72, 864):
                args = ['stability', '--model', 'gpt-6-luna', '--output', str(output)]
                if auth is not None: args += ['--allow-external-requests', str(auth)]
                with patch.object(e, 'metadata', side_effect=AssertionError('provider probe')), \
                     patch.object(e.router, 'generate', side_effect=AssertionError('workspace')), \
                     contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(e.main(args), 1)
            for model in (None, 'gpt-6-sol'):
                args = ['stability', '--allow-external-requests', '48', '--output', str(output)]
                if model: args += ['--model', model]
                with patch.object(e, 'metadata', side_effect=AssertionError('provider')), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(e.main(args), 1)
            broken = Path(tmp) / 'broken.json'
            broken.write_text('{}')
            with patch.object(e, 'metadata', side_effect=AssertionError('provider')), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(e.main(['stability', '--allow-external-requests', '48', '--model', 'gpt-6-luna',
                    '--canonical-result', str(broken), '--output', str(output)]), 1)
            output.write_text('prior')
            with patch.object(e, 'metadata', side_effect=AssertionError('provider')), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(e.main(['stability', '--allow-external-requests', '48', '--model', 'gpt-6-luna', '--output', str(output)]), 1)
            self.assertEqual(output.read_text(), 'prior')

    def test_valid_cli_48_only_exclusive_artifact(self):
        engine = self.engine()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'new.json'
            with patch.object(e.router, 'generate'), patch.object(e, 'metadata', return_value=dict(self.info)), \
                 patch.object(e.router, 'CodexDecision', return_value=engine) as factory, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(e.main(['stability', '--allow-external-requests', '48', '--model', 'gpt-6-luna', '--output', str(output)]), 0)
                factory.assert_called_once_with(model='gpt-6-luna', timeout=120)
            self.assertEqual(len(engine.calls), 48)
            self.assertEqual(json.loads(output.read_text())['mode'], 'stability')

    def test_canonical_validation_rejects_every_control_mismatch(self):
        mutations = [lambda r: r.update(complete=False), lambda r: r.update(total_decisions=71),
            lambda r: r.update(total_cases=71), lambda r: r.update(planned_decisions=71),
            lambda r: r['runtime'].update(model='other'), lambda r: r.update(control_sha256={}),
            lambda r: r['failure_counts'].update(provider_failure=1),
            lambda r: r['experiment'].update(descriptions={'precise': {}}),
            lambda r: r['experiment'].update(orders={'with_no_match': {'reversed': []}}),
            lambda r: r['experiment'].update(corpus=[]), lambda r: r['predictions'].pop(),
            lambda r: r['predictions'][0].update(choice_mode='without_no_match'),
            lambda r: r['predictions'][0].update(description_set='precise'),
            lambda r: r['predictions'][0].update(order='reversed'),
            lambda r: r['predictions'][0].update(selected_id='unknown'),
            lambda r: r['predictions'][0].update(error='provider_failure'),
            lambda r: r['predictions'][0].update(expected='no_match'),
            lambda r: r['predictions'].append(r['predictions'][0])]
        for mutation in mutations:
            changed = copy.deepcopy(self.reference)
            mutation(changed)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): e.validate_reference(changed)

    def test_fail_closed_no_retry_all_errors(self):
        for error in e.ERRORS:
            engine = self.engine(error=error)
            result = self.run_mock(engine)
            self.assertEqual(len(engine.calls), 1)
            self.assertEqual(result['infrastructure_failures'], 1)
            self.assertFalse(result['complete'])
            self.assertFalse(result['assessment']['passed'])
            self.assertEqual(result['stop_reason'], error)
        engine = self.engine(); engine.model = 'other'
        with self.assertRaises(ValueError): self.run_mock(engine)
        self.assertEqual(engine.calls, [])

    def test_hand_calculated_stability_changes_position_no_match(self):
        subset = self.subset[:4]
        canonical = {c['id']: {'selected_id': 'build'} for c in subset}
        values = [('build', 'build'), ('test', 'build'), ('test', 'test'), ('no_match', 'build')]
        rows = [{'case_id': c['id'], 'order': name, 'selected_id': value}
                for c, pair in zip(subset, values) for name, value in zip(('reversed', 'shuffle-20260404'), pair)]
        metrics = e.stability_metrics(subset, canonical, rows)
        self.assertEqual(metrics['full_order_stability']['stable_cases'], 1)
        self.assertEqual(metrics['full_order_stability']['rate'], .25)
        self.assertEqual([p['stable_cases'] for p in metrics['pairwise_stability'].values()], [1, 3, 2])
        self.assertEqual(len(metrics['changed_cases']), 3)
        self.assertEqual(metrics['per_category']['clear_positive']['rate'], .25)
        self.assertEqual(metrics['per_capability']['build']['total_cases'], 0)
        self.assertEqual(len(metrics['no_match']['changed_cases']), 1)
        position = metrics['candidate_position_analysis']['canonical_vs_reversed']
        self.assertEqual(position['earlier'], 3)
        self.assertEqual(position['later'], 0)
        self.assertEqual(position['two_sided_sign_test_p'], .25)
        self.assertFalse(position['systematic_bias'])
        for change in position['changes']:
            self.assertEqual(change['destination_rank_delta'], change['new_candidate_destination_position'] - change['old_candidate_destination_position'])

    def test_fixed_sign_test_bias_gate(self):
        canonical = {c['id']: {'selected_id': 'build'} for c in self.subset}
        rows = [{'case_id': c['id'], 'order': name, 'selected_id': 'test' if name == 'reversed' and i < 6 else 'build'}
                for i, c in enumerate(self.subset) for name in ('reversed', 'shuffle-20260404')]
        metrics = e.stability_metrics(self.subset, canonical, rows)
        analysis = metrics['candidate_position_analysis']['canonical_vs_reversed']
        self.assertEqual(analysis['two_sided_sign_test_p'], .03125)
        self.assertTrue(analysis['systematic_bias'])

    def test_safety_regression_and_accuracy_assessment_failures(self):
        for category in ('regression', 'adversarial', 'out_of_scope', 'clear_positive'):
            targets = {c['request'] for c in self.subset if c['category'] == category}
            gold = {c['request']: c['expected_choice'] for c in self.cases}
            result = self.run_mock(self.engine(change=lambda req, order: 'doctor' if req in targets else gold[req]))
            self.assertFalse(result['assessment']['passed'])
            self.assertIn('larger order evaluation may be warranted', result['assessment']['interpretation'])
            self.assertEqual(result['predeclared_thresholds'], e.STABILITY_THRESHOLDS)
            if category == 'regression': self.assertFalse(result['assessment']['gates']['regressions_build_all_orders'])
            if category in ('adversarial', 'out_of_scope'): self.assertFalse(result['assessment']['gates']['no_dangerous_order_induced_routes'])


if __name__ == '__main__': unittest.main()
