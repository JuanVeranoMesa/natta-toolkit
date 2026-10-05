"""Approved-profile and comparison checks; no model loading or network."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import local_model as contract
from test_local_model import decision, download, load_module, RUNTIME
from test_routing_evaluation import e, FakeDecision

comparison = load_module('comparison', 'compare.py')
runner = load_module('comparison_runner', 'run_comparison.py')


def synthetic_baseline():
    """Exercise complete comparison validation without distributing host results."""
    cases, descriptions = e.read_inputs(e.ROOT/'corpus.json', e.ROOT/'descriptions.json')
    result = e.evaluate(cases, descriptions, FakeDecision())
    result['synthetic_fixture'] = True
    result['runtime']['python_version'] = 'synthetic Python 3.12'
    result['input_sha256'] = {name: hashlib.sha256((e.ROOT/name).read_bytes()).hexdigest()
                              for name in ('corpus.json', 'descriptions.json')}
    for row in [*result['predictions'], result['legacy_smoke']['answer']]:
        row['model'].update(id=contract.MODEL_ID, revision=contract.REVISION)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)/'synthetic.json'
        path.write_text(json.dumps(result))
        with patch.object(comparison, 'BASELINE_SHA256', hashlib.sha256(path.read_bytes()).hexdigest()):
            loaded = comparison.load_result(path, contract.DEFAULT_PROFILE)
            path.write_text(path.read_text() + ' ')
            with unittest.TestCase().assertRaisesRegex(ValueError, 'immutable evidence'):
                comparison.load_result(path, contract.DEFAULT_PROFILE)
    return loaded


class ProfileTests(unittest.TestCase):
    def test_two_exact_profiles_and_noncolliding_absolute_paths(self):
        self.assertEqual(tuple(contract.PROFILES), ('qwen3-0.6b', 'qwen3-1.7b'))
        self.assertEqual(contract.model_profile().model_id, 'Qwen/Qwen3-0.6B')
        self.assertEqual(contract.model_profile().revision, 'c1899de289a04d12100db370d81485cdf75e47ca')
        p = contract.model_profile('qwen3-1.7b')
        self.assertEqual((p.model_id, p.revision), ('Qwen/Qwen3-1.7B', '70d244cc86ccca08cf5af4e1e306ecf908b1ad5e'))
        a, b = (contract.model_path('/fixture', name) for name in contract.PROFILES)
        self.assertNotEqual(a, b)
        self.assertTrue(a.is_absolute() and b.is_absolute())
        self.assertEqual(b, Path('/fixture/Library/Application Support/NattaToolkit/models/Qwen3-1.7B')/p.revision)

    def test_unapproved_profiles_fail_before_loading_or_downloading(self):
        for name in ('Qwen/Qwen3-1.7B', 'qwen3-4b', '/tmp/model', '', None):
            with self.subTest(name=name), self.assertRaises(ValueError):
                decision.LocalDecision(name)
        with patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit):
            download.main(['--model', 'Qwen/Qwen3-4B'])

    def fixture(self, root):
        profile = 'qwen3-1.7b'
        approved = contract.model_profile(profile)
        path = contract.model_path(root, profile)
        path.mkdir(parents=True)
        config = {'model_type': 'qwen3', 'architectures': ['Qwen3ForCausalLM'],
                  'hidden_size': 2048, 'num_hidden_layers': 28, 'vocab_size': 151936, 'num_key_value_heads': 8}
        index = {'weight_map': {'a': 'model-00001-of-00002.safetensors', 'b': 'model-00002-of-00002.safetensors'}}
        for name in approved.files:
            (path/name).write_text(json.dumps(config if name == 'config.json' else index if name.endswith('index.json') else 'fixture'))
        receipt = {'model_id': approved.model_id, 'revision': approved.revision, 'source': 'https://huggingface.co',
                   'files': {name: download.hashes(path/name) for name in approved.files}}
        (path/'natta-model.json').write_text(json.dumps(receipt))
        return path

    def test_sharded_receipt_verification_and_wrong_profile(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.fixture(root)
            download.verify_local(path, 'qwen3-1.7b')
            with self.assertRaises(ValueError):
                contract.validate_model(path)
            (path/'model.safetensors.index.json').write_text(json.dumps({'weight_map': {'a': '../outside'}}))
            with self.assertRaisesRegex(ValueError, 'shard index'):
                contract.validate_model(path, 'qwen3-1.7b')

    def test_download_pins_official_identity_and_checks_revision_before_snapshot(self):
        approved = contract.model_profile('qwen3-1.7b')
        hub = ModuleType('huggingface_hub')
        api = Mock()
        api.model_info.return_value = SimpleNamespace(sha='main')
        hub.HfApi = Mock(return_value=api)
        hub.snapshot_download = Mock(side_effect=AssertionError('downloaded wrong revision'))
        with tempfile.TemporaryDirectory() as root, patch.object(contract, 'model_path', return_value=Path(root)), patch.dict(sys.modules, {'huggingface_hub': hub}), patch.dict(download.os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'different model revision'):
                download.main(['--model', 'qwen3-1.7b'])
        api.model_info.assert_called_once_with(approved.model_id, revision=approved.revision, files_metadata=True)
        hub.HfApi.assert_called_once_with(endpoint='https://huggingface.co', token=False)
        hub.snapshot_download.assert_not_called()

    def test_selected_absolute_path_passed_to_localjev_with_mps_fp32(self):
        engine = decision.LocalDecision('qwen3-1.7b')
        torch = ModuleType('torch')
        torch.float32 = 'float32'
        torch.backends = SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True))
        torch.mps = SimpleNamespace(synchronize=lambda: None)
        core = ModuleType('openjev.core')
        parameter = SimpleNamespace(device=SimpleNamespace(type='mps'), dtype='float32')
        core.LocalJev = Mock(return_value=SimpleNamespace(device='mps', model=SimpleNamespace(parameters=lambda: iter([parameter]))))
        probe = ModuleType('probe')
        probe.inspect = Mock()
        with patch.object(contract, 'validate_config'), patch.object(contract, 'validate_model') as validate, patch.dict(sys.modules, {'torch': torch, 'openjev.core': core, 'probe': probe}), patch.dict(decision.__dict__['sys'].modules, {'torch': torch}), patch.dict('os.environ', {}, clear=True):
            engine._load_engine()
            validate.assert_called_once_with(engine.model_path, 'qwen3-1.7b')
            core.LocalJev.assert_called_once_with(str(engine.model_path), device='mps', dtype='float32')

    def test_selected_identity_in_prediction_and_evaluation_schema(self):
        profile = 'qwen3-1.7b'
        answer = decision.translate({'type': 'choice', 'choice': 'a', 'probabilities': {'a': .75, 'b': .25}, 'confidence': .5}, {'a': 'first', 'b': 'second'}, contract.model_path(profile=profile), 1, .1, profile)
        self.assertEqual(answer['model']['id'], 'Qwen/Qwen3-1.7B')
        fake = FakeDecision()
        fake.profile = profile
        cases, descriptions = e.read_inputs(e.ROOT/'corpus.json', e.ROOT/'descriptions.json')
        result = e.evaluate(cases[:1], descriptions, fake)
        self.assertEqual(result['runtime']['model_revision'], contract.model_profile(profile).revision)
        self.assertIn('Qwen/Qwen3-1.7B', e.render(result))
        self.assertEqual(result['schema_version'], 1)

    def test_retained_baseline_and_frozen_inputs_metrics_still_match(self):
        result = synthetic_baseline()
        self.assertEqual(result['total_decisions'], 865)
        self.assertEqual(result['verdict'], 'NOT READY FOR ROUTING')
        self.assertEqual(hashlib.sha256((e.ROOT/'corpus.json').read_bytes()).hexdigest(), '3bb48e295c193c09509048c47ebba050971c99a710adcdd3c6d46e94101074d1')
        self.assertEqual(hashlib.sha256((e.ROOT/'descriptions.json').read_bytes()).hexdigest(), '0bfa5f8af2512b4670a5baacaf1a3c59eecc8cf20e9e4ea927c46dde3fc608ee')
        self.assertIn('pending', comparison.report(result))
        candidate = copy.deepcopy(result)
        candidate['experiment']['seed'] += 1
        with self.assertRaisesRegex(ValueError, 'not directly comparable'):
            comparison.compatible(result, candidate)

    def test_doctor_optional_second_snapshot_is_cheap_offline_and_read_only(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.fixture(root)
            original = contract.model_path
            before = {p: p.read_bytes() for p in path.iterdir()}
            def paths(home=None, profile=contract.DEFAULT_PROFILE):
                return original(root, profile)
            with patch.object(contract, 'model_path', side_effect=paths), patch.object(contract, 'inspect_runtime', return_value={}), patch.dict(sys.modules, {'torch': None, 'transformers': None, 'openjev': None}):
                checks = contract.doctor_checks()
            self.assertTrue(checks[-1][0])
            self.assertIn('1.7B', checks[-1][1])
            self.assertEqual(before, {p: p.read_bytes() for p in path.iterdir()})

    def test_smoke_failure_stops_before_benchmark_and_persistence(self):
        baseline = synthetic_baseline()
        metadata = {'python': [3, 12, 15], 'versions': {'openjev': '0.1.0', 'torch': '2.14.1', 'transformers': '5.18.0', 'huggingface-hub': '1.33.0'}}
        # Existing real comparison outputs must not affect this failure-path unit test.
        with tempfile.TemporaryDirectory() as root, patch.object(runner, 'ROOT', Path(root)):
            (Path(root)/'uv.lock').write_bytes((e.RUNTIME/'uv.lock').read_bytes())
            with patch.object(runner.compare, 'load_result', return_value=baseline), patch.object(contract, 'inspect_runtime', return_value=metadata), patch.object(contract, 'validate_model'), patch.object(runner.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='allocation failed', stderr='')) as run:
                with self.assertRaisesRegex(RuntimeError, 'benchmark not started'):
                    runner.main()
        run.assert_called_once()
        self.assertIn('--smoke', run.call_args.args[0])

    def test_complete_candidate_is_comparable_without_schema_change(self):
        baseline = synthetic_baseline()
        candidate = copy.deepcopy(baseline)
        approved = contract.model_profile('qwen3-1.7b')
        candidate['runtime'].update(model_id=approved.model_id, model_revision=approved.revision,
                                    model_path=str(contract.model_path(profile='qwen3-1.7b')))
        for row in [*candidate['predictions'], candidate['legacy_smoke']['answer']]:
            row['model'].update(id=approved.model_id, revision=approved.revision)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'candidate.json'
            path.write_text(json.dumps(candidate))
            loaded = comparison.load_result(path, 'qwen3-1.7b')
        comparison.compatible(baseline, loaded)
        text = comparison.report(baseline, loaded)
        self.assertIn('+0.0', text)
        self.assertIn('REJECTED FOR ROUTING', text)
        candidate['runtime']['uv_lock_sha256'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'uv_lock_sha256'):
            comparison.compatible(baseline, candidate)

    def test_missing_optional_model_does_not_expand_doctor(self):
        with tempfile.TemporaryDirectory() as root, patch.object(contract, 'model_path', return_value=Path(root)/'missing'), patch.object(contract, 'inspect_runtime', return_value={}):
            checks = contract.doctor_checks()
        self.assertEqual(len(checks), 2)


if __name__ == '__main__':
    unittest.main()
