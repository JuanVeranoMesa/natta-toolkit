"""Canonical reference pass: mocked provider only, frozen corpus and protocol."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE.parent / 'natta-local-model'))
import evaluate_codex as e


class Engine:
    timeout = 120

    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure
        self.gold = {c['request']: c['expected_choice'] for c in e.inputs()[0]}

    def decide(self, request, name, order):
        self.calls.append((request, name, list(order)))
        error = self.failure if len(self.calls) == 1 else None
        choice = None if error else self.gold[request]
        return {'status': 'failed' if error else 'passed', 'selected_id': choice,
                'error': error, 'inference_seconds': len(self.calls) / 100}


def info():
    return {'model': 'gpt-6-luna', 'source_sha256': e.router.source_hashes()}


class CanonicalReferenceTests(unittest.TestCase):
    def test_plan_all_72_frozen_cases_exactly_once(self):
        cases, _, _ = e.inputs()
        plan = e.decisions(cases, 'reference')
        self.assertEqual(len(plan), 72)
        self.assertEqual([c for c, *_ in plan], cases)
        self.assertEqual(len({c['id'] for c, *_ in plan}), 72)
        for _, name, include, order_name, order in plan:
            self.assertEqual((name, include, order_name), ('concise', True, 'canonical'))
            self.assertEqual(order, e.control.orders()['canonical'])
            self.assertIn('no_match', order)

    def test_full_reference_metrics_without_untested_dimensions(self):
        engine = Engine()
        with patch.object(e.subprocess, 'Popen', side_effect=AssertionError('external usage')), \
             patch.object(e.scoring, 'summarize', side_effect=AssertionError('full summary')), \
             patch.object(e.control, 'stability', side_effect=AssertionError('single-order stability')):
            result = e.evaluate('reference', engine, info(), progress=lambda _: None)
        self.assertEqual(len(engine.calls), 72)
        self.assertTrue(result['complete'])
        self.assertEqual(result['total_cases'], 72)
        self.assertEqual(result['total_decisions'], 72)
        self.assertEqual(result['planned_decisions'], 72)
        self.assertEqual(result['evaluation_kind'], 'canonical reference pass')
        self.assertEqual(result['runtime']['model'], 'gpt-6-luna')
        self.assertEqual(set(result['experiment']['descriptions']), {'concise'})
        self.assertEqual(result['experiment']['orders'], {'with_no_match': {'canonical': e.control.orders()['canonical']}})
        summary = result['summaries']
        self.assertEqual(summary['accuracy'], 1)
        self.assertEqual(summary['no_match']['precision'], 1)
        self.assertEqual(summary['no_match']['recall'], 1)
        self.assertTrue(all(v['accuracy'] == 1 for v in summary['per_capability'].values()))
        self.assertTrue(all(v['accuracy'] == 1 for v in summary['per_category'].values()))
        self.assertEqual(summary['incorrect_cases'], [])
        self.assertEqual([r['case_id'] for r in summary['regression_cases']], ['regression-01', 'regression-02'])
        self.assertTrue(all(r['routes_to_build'] for r in summary['regression_cases']))
        self.assertNotIn('order_stability', summary)
        self.assertNotIn('description_sensitivity', summary)
        self.assertEqual(result['timing']['minimum_inference_seconds'], .01)
        self.assertEqual(result['timing']['maximum_inference_seconds'], .72)
        self.assertAlmostEqual(result['timing']['average_inference_seconds'], .365)
        self.assertAlmostEqual(result['timing']['median_inference_seconds'], .365)
        self.assertIn('unmeasured', e.render_reference(result))
        with self.assertRaises(ValueError): e.validate_full(result)

    def test_infrastructure_failure_stops_without_retries(self):
        for error in ('boundary_violation', 'provider_failure', 'provider_timeout'):
            engine = Engine(error)
            result = e.evaluate('reference', engine, info(), progress=lambda _: None)
            self.assertEqual(len(engine.calls), 1)
            self.assertFalse(result['complete'])
            self.assertEqual(result['stop_reason'], error)
            self.assertEqual(result['failure_counts'][error], 1)
            self.assertIsNone(result['summaries'])
            self.assertIsNone(result['predictions'][0]['selected_id'])
            self.assertIn('Incomplete pass', e.render_reference(result))

    def test_invalid_outputs_count_as_incorrect_without_retry(self):
        for error in ('malformed_output', 'unknown_choice'):
            engine = Engine(error)
            result = e.evaluate('reference', engine, info(), progress=lambda _: None)
            self.assertEqual(len(engine.calls), 72)
            self.assertTrue(result['complete'])
            self.assertEqual(result['failure_counts'][error], 1)
            self.assertEqual(result['summaries']['accuracy'], 71/72)
            self.assertEqual(result['summaries']['incorrect_cases'][0]['case_id'], 'clear_positive-01')
            self.assertEqual(result['summaries']['incorrect_cases'][0]['error'], error)

    def test_exact_incorrect_cases_and_regression_outcomes(self):
        cases, _, _ = e.inputs()
        engine = Engine()
        original = engine.decide
        def wrong(request, name, order):
            answer = original(request, name, order)
            if request == next(c['request'] for c in cases if c['id'] == 'ambiguous-01'):
                answer['selected_id'] = 'context'
            return answer
        engine.decide = wrong
        result = e.evaluate('reference', engine, info(), progress=lambda _: None)
        summary = result['summaries']
        self.assertEqual(summary['accuracy'], 71/72)
        self.assertEqual(summary['incorrect_cases'], [{'case_id': 'ambiguous-01', 'request': 'Check Example.',
                          'expected': 'no_match', 'selected_id': 'context', 'error': None}])
        self.assertIn({'expected': 'no_match', 'selected': 'context', 'count': 1}, summary['confusion'])
        self.assertEqual(summary['per_category']['ambiguous']['accuracy'], 7/8)

    def test_usage_and_model_guard_before_any_provider_or_workspace_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = str(Path(tmp) / 'reference.json')
            with patch.object(e.router, 'generate', side_effect=AssertionError('workspace')), \
                 patch.object(e, 'metadata', side_effect=AssertionError('provider')), \
                 contextlib.redirect_stderr(io.StringIO()):
                for count in (None, 6, 71, 73, 864):
                    argv = ['reference', '--model', 'gpt-6-luna', '--output', output]
                    if count is not None:
                        argv += ['--allow-external-requests', str(count)]
                    self.assertEqual(e.main(argv), 1)
                for model in (None, 'other-model'):
                    argv = ['reference', '--allow-external-requests', '72', '--output', output]
                    if model:
                        argv += ['--model', model]
                    self.assertEqual(e.main(argv), 1)
            self.assertFalse(Path(output).exists())
        with self.assertRaisesRegex(ValueError, 'explicit gpt-6-luna'):
            e.evaluate('reference', Engine(), {'model': None}, progress=lambda _: None)

    def test_authorized_cli_additive_result_unchanged_provider_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            output = root / 'reference.json'
            engine = Engine()
            argv = ['reference', '--allow-external-requests', '72', '--model', 'gpt-6-luna', '--output', str(output)]
            with patch.object(e.router, 'workspace_path', return_value=root / 'router'), \
                 patch.object(e, 'metadata', return_value={'model': 'gpt-6-luna'}) as metadata, \
                 patch.object(e.router, 'CodexDecision', return_value=engine) as factory, \
                 patch.object(e.subprocess, 'Popen', side_effect=AssertionError('external usage')), \
                 contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(e.main(argv), 0)
            metadata.assert_called_once_with('gpt-6-luna')
            factory.assert_called_once_with(model='gpt-6-luna', timeout=120)
            self.assertEqual(len(engine.calls), 72)
            self.assertIn('Canonical reference pass', out.getvalue())
            result = json.loads(output.read_text())
            self.assertEqual(result['runtime']['model'], 'gpt-6-luna')
            saved = output.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(e.main(argv), 1)
            self.assertEqual(output.read_bytes(), saved)

    def test_existing_modes_and_frozen_artifacts_unchanged(self):
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in e.control.ROOT.iterdir() if p.is_file()}
        cases, _, _ = e.inputs()
        pilot = e.decisions(cases, 'pilot')
        self.assertEqual(len(pilot), 6)
        self.assertEqual([c['id'] for c, *_ in pilot], list(e.PILOT_IDS))
        expected_full = [(case, name, include, order_name, order)
                         for name in ('concise', 'precise') for include in (True, False)
                         for order_name, order in e.control.orders(include).items() for case in cases]
        self.assertEqual(e.decisions(cases, 'full'), expected_full)
        e.evaluate('reference', Engine(), info(), progress=lambda _: None)
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before})
        self.assertEqual(next(c for c in e.inputs()[0] if c['id'] == 'ambiguous-01')['expected_choice'], 'no_match')


if __name__ == '__main__':
    unittest.main()
