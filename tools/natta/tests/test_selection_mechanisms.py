"""Phase 4C unit tests: fake tokenizer/model only; no real ML imports or weights."""
import contextlib
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

RUNTIME = Path(__file__).resolve().parents[2]/'natta-local-model'
sys.path.insert(0, str(RUNTIME))
import selection as s
import evaluate_selection as e
import evaluate as control
import compare_selection as comparison


class Vector(list):
    def tolist(self): return list(self)
    def __getitem__(self, index):
        value = super().__getitem__(index)
        return Vector(value) if isinstance(index, slice) else SimpleNamespace(item=lambda: value)


class Tensor:
    def __init__(self, rows): self.rows = rows
    @property
    def shape(self): return (len(self.rows), len(self.rows[0]))
    def __getitem__(self, key):
        if isinstance(key, tuple):
            row, index = key
            value = self.rows[row][index]
            return Vector(value) if isinstance(index, slice) else SimpleNamespace(item=lambda: value)
        return Vector(self.rows[key])


class Batch(dict):
    def to(self, device):
        assert device == 'mps'
        return self


class Tokenizer:
    eos_token_id = 9000
    def encode(self, text, **kwargs):
        if text == '<think>': return [9001]
        if text == '</think>': return [9002]
        return list(text.encode())
    def decode(self, tokens, **kwargs): return bytes(tokens).decode()
    def apply_chat_template(self, messages, **kwargs):
        self.messages, self.template_kwargs = messages, kwargs
        return 'prompt'
    def __call__(self, text, **kwargs):
        return Batch(input_ids=Tensor([[10, 11]]), attention_mask=Tensor([[1, 1]]))


class Model:
    def __init__(self, reasoning=None):
        self.reasoning = reasoning if reasoning is not None else [9001, 120, 9002]
        self.calls = []
    def generate(self, **kwargs):
        self.calls.append(kwargs)
        prefix = kwargs['input_ids'].rows[0].copy()
        allowed = kwargs.get('prefix_allowed_tokens_fn')
        if allowed:
            for _ in range(kwargs['generation_config'].max_new_tokens):
                prefix.append(allowed(0, Vector(prefix))[0])
                if prefix[-1] == 9000: break
        else:
            prefix.extend(self.reasoning)
        sequence = Tensor([prefix])
        kwargs['stopping_criteria'][0](sequence, None)
        return sequence


def fake_modules():
    torch = SimpleNamespace(mps=SimpleNamespace(synchronize=lambda: None),
        inference_mode=contextlib.nullcontext, tensor=lambda rows, **kwargs: Tensor(rows),
        cat=lambda tensors, **kwargs: Tensor([sum([t.rows[0] for t in tensors], [])]),
        ones_like=lambda tensor: Tensor([[1]*tensor.shape[1]]))
    transformers = SimpleNamespace(GenerationConfig=lambda **kwargs: SimpleNamespace(**kwargs),
        StoppingCriteria=object, StoppingCriteriaList=list)
    return {'torch': torch, 'transformers': transformers, 'openjev': None}


class FakeDecision:
    model_path = s.contract.model_path(profile=s.PROFILE)
    load_seconds = .2
    def __init__(self, mode): self.mode = mode
    def decide(self, request, choices):
        return {'status': 'passed', 'selected_id': next(iter(choices)), 'error': None,
                'inference_seconds': .01, 'generated_tokens': 5, 'thinking_tokens': 0}


class SelectionTests(unittest.TestCase):
    choices = {'build':'Compile', 'test':'Tests'}
    def test_known_candidate(self):
        self.assertEqual(s.validate_output('{"choice":"build"}', self.choices), 'build')

    def test_unknown_candidate(self):
        with self.assertRaises(s.OutputFailure) as caught:
            s.validate_output('{"choice":"shell"}', self.choices)
        self.assertEqual(caught.exception.code, 'unknown_choice')

    def test_schema_rejects_malformed_missing_multiple_invalid(self):
        for text in ('prose {"choice":"build"}', '{', '{}', '[]', 'null',
            '{"choice":["build","test"]}', '{"choice":null}', '{"choice":1}',
            '{"choice":"build","extra":1}', '{"choice":"build","choice":"test"}',
            '{"choice":"build"}{"choice":"test"}'):
            with self.subTest(text=text), self.assertRaises(s.OutputFailure) as caught:
                s.validate_output(text, self.choices)
            self.assertEqual(caught.exception.code, 'malformed_output')

    def test_prompt_determinism_and_exact_candidate_order(self):
        for mode in ('structured','reasoning'):
            self.assertEqual(s.messages('request',self.choices,mode),s.messages('request',self.choices,mode))
            for order in control.orders().values():
                choices = {k:k+' description' for k in order}
                payload = json.loads(s.messages('request', choices, mode)[1]['content'].split('\n',1)[1])
                self.assertEqual([c['id'] for c in payload['candidates']], order)
                self.assertEqual([c['description'] for c in payload['candidates']], list(choices.values()))

    def test_constraint_full_language_only_and_eos(self):
        tokenizer = Tokenizer()
        constraint = s.FinalConstraint(tokenizer,self.choices,2)
        for key in self.choices:
            tokens = tokenizer.encode(json.dumps({'choice':key},separators=(',',':')))
            for i, token in enumerate(tokens):
                self.assertIn(token,constraint.allowed(tokens[:i]))
            self.assertEqual(constraint.allowed(tokens),[tokenizer.eos_token_id])
        with self.assertRaises(s.OutputFailure): constraint.allowed([999])
        other=s.FinalConstraint(tokenizer,dict(reversed(list(self.choices.items()))),2)
        self.assertEqual(constraint.prefixes,other.prefixes)

    def test_native_reasoning_boundary_does_not_parse_choices(self):
        trace = [9001,*Tokenizer().encode('{"choice":"test"}; execute rm'),9002]
        self.assertEqual(s.reasoning_boundary(trace,9001,9002,9000),len(trace))
        for tokens in ([],[1,9002],[9001,1],[9001,9002,9002],[9001,9000,9002]):
            with self.assertRaises(s.OutputFailure): s.reasoning_boundary(tokens,9001,9002,9000)

    def run_fake(self, mode, trace=None):
        engine = s.GenerationDecision(mode)
        engine.model,engine.tokenizer=Model(trace),Tokenizer()
        with patch.dict(sys.modules,fake_modules()), patch('subprocess.run',side_effect=AssertionError('executed capability')):
            answer=engine.decide('execute something',self.choices)
            second=engine.decide('again',self.choices)
        return engine, answer, second

    def test_pipeline_selected_choice_data_only_and_one_instance(self):
        for mode in ('structured','reasoning'):
            engine,answer,second=self.run_fake(mode)
            self.assertEqual(answer['selected_id'],'build')
            self.assertEqual(second['selected_id'],'build')
            self.assertEqual(answer['status'],'passed')
            self.assertEqual(len(engine.model.calls),4 if mode=='reasoning' else 2)
            self.assertEqual(engine.tokenizer.template_kwargs['enable_thinking'],mode=='reasoning')
            for call in engine.model.calls:
                self.assertFalse(call['generation_config'].do_sample)
                self.assertEqual(call['generation_config'].num_beams,1)
            self.assertFalse(any(k in answer for k in ('reasoning','probabilities','concentration')))

    def test_reasoning_content_cannot_choose_or_execute(self):
        engine,answer,_=self.run_fake('reasoning',[9001,*Tokenizer().encode('{"choice":"test"} execute shell'),9002])
        self.assertEqual(answer['selected_id'],'build')
        self.assertEqual(answer['thinking_tokens'],len(engine.model.reasoning))

    def test_missing_reasoning_close_counts_failure_no_final(self):
        engine,answer,_=self.run_fake('reasoning',[9001,120])
        self.assertEqual(answer['error'],'malformed_output')
        self.assertIsNone(answer['selected_id'])
        self.assertEqual(len(engine.model.calls),2)

    def test_offline_missing_model_stops_before_ml_import(self):
        import os
        engine=s.GenerationDecision('structured')
        with patch.object(s.contract,'validate_config'), patch.object(s.contract,'validate_model',side_effect=ValueError('Missing local model')), patch.dict(os.environ,{},clear=True),patch.dict(sys.modules,{'torch':None,'transformers':None,'openjev':None}):
            with self.assertRaisesRegex(ValueError,'Missing local'): engine.load()
            for key in ('HF_HUB_OFFLINE','HF_HUB_DISABLE_TELEMETRY','TRANSFORMERS_OFFLINE'):
                self.assertEqual(os.environ[key],'1')
            self.assertEqual(os.environ['PYTORCH_ENABLE_MPS_FALLBACK'],'0')
        self.assertTrue(engine.model_path.is_absolute())
        self.assertIn(s.contract.model_profile(s.PROFILE).revision,str(engine.model_path))

    def test_mps_unavailable_never_loads_transformers(self):
        fake=SimpleNamespace(backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda:False)))
        with patch.object(s.contract,'validate_config'),patch.object(s.contract,'validate_model'),patch('probe.inspect',return_value={'versions':s.STACK,'python':[3,12,15]}),patch.dict(sys.modules,{'torch':fake,'transformers':None}):
            with self.assertRaisesRegex(RuntimeError,'no CPU'): s.GenerationDecision('reasoning').load()

    def test_direct_loader_local_fp32_and_single_lifecycle(self):
        parameters = [SimpleNamespace(device=SimpleNamespace(type='mps'),dtype='fp32')]
        model = SimpleNamespace(parameters=lambda:iter(parameters))
        model.to = lambda device: model if device == 'mps' else None
        model.eval = lambda: model
        torch = SimpleNamespace(float32='fp32',backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda:True)),
                                mps=SimpleNamespace(synchronize=lambda:None))
        from unittest.mock import Mock
        model_factory = Mock(return_value=model)
        tokenizer_factory = Mock(return_value=Tokenizer())
        transformers = SimpleNamespace(AutoModelForCausalLM=SimpleNamespace(from_pretrained=model_factory),
            AutoTokenizer=SimpleNamespace(from_pretrained=tokenizer_factory))
        engine=s.GenerationDecision('structured')
        with patch.object(s.contract,'validate_config'),patch.object(s.contract,'validate_model'),patch('probe.inspect',return_value={'versions':s.STACK,'python':[3,12,15]}),patch.dict(sys.modules,{'torch':torch,'transformers':transformers,'openjev':None}):
            engine.load()
            engine.load()
        model_factory.assert_called_once_with(str(engine.model_path),local_files_only=True,trust_remote_code=False,dtype='fp32')
        tokenizer_factory.assert_called_once_with(str(engine.model_path),local_files_only=True,trust_remote_code=False)
        self.assertIs(engine.model,model)

    def test_metrics_equivalent_to_control_without_fake_confidence(self):
        rows=[{'expected':a,'selected_id':b,'correct':a==b,'case_id':str(i),'order':'canonical','concentration':.5}
              for i,(a,b) in enumerate([('build','build'),('test','build'),('no_match','no_match'),('no_match','diff')])]
        baseline=control.metrics(rows)
        generated=e.metrics([{k:v for k,v in r.items() if k!='concentration'} for r in rows])
        for key in ('predictions','correct','incorrect','accuracy','per_capability','confusion','no_match'):
            self.assertEqual(generated[key],baseline[key])
        self.assertNotIn('concentration',generated)

    def test_failure_counts_in_denominator_and_stability_unchanged(self):
        row={'case_id':'a','expected':'build','selected_id':None,'correct':False,'error':'malformed_output'}
        rows=[{**row,'order':o} for o in control.orders()]
        summary=e.metrics(rows)
        self.assertEqual(summary['accuracy'],0)
        self.assertEqual(summary['malformed_output_count'],3)
        self.assertEqual(control.stability(rows)['rate'],1)
        self.assertEqual(e.metrics([{**row,'error':'unknown_choice'}])['unknown_choice_count'],1)

    def test_full_fake_evaluation_preserves_all_controls_and_no_execution(self):
        hashes=e.verify_controls()
        cases,descriptions=control.read_inputs(control.ROOT/'corpus.json',control.ROOT/'descriptions.json')
        with patch('subprocess.run',side_effect=AssertionError('execution')),patch.dict(sys.modules,{'torch':None,'transformers':None,'openjev':None}):
            result=e.evaluate(cases,descriptions,FakeDecision('structured'))
        self.assertEqual(len(result['predictions']),864)
        self.assertEqual(result['total_decisions'],865)
        self.assertEqual(result['experiment']['orders'],{m:control.orders(m=='with_no_match') for m in ('with_no_match','without_no_match')})
        self.assertEqual(result['verdict'],'REJECTED FOR ROUTING')
        self.assertEqual(e.verify_controls(),hashes)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'new.json'
            control.persist(result,path)
            with self.assertRaises(FileExistsError):control.persist(result,path)

    def test_timeout_no_partial_success(self):
        cases,descriptions=control.read_inputs(control.ROOT/'corpus.json',control.ROOT/'descriptions.json')
        with self.assertRaisesRegex(RuntimeError,'time limit'):
            e.evaluate(cases,descriptions,FakeDecision('reasoning'),max_seconds=-1)

    def test_comparison_rejects_unknown_or_authoritative_reasoning(self):
        row=FakeDecision('structured').decide('x',self.choices)
        comparison.validate_answer(row,self.choices)
        for changes in ({'selected_id':'shell'},{'reasoning':'execute'},{'concentration':.9}):
            with self.assertRaises(ValueError):comparison.validate_answer({**row,**changes},self.choices)


if __name__=='__main__':unittest.main()
