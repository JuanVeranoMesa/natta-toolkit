"""Evaluation tests use fake decisions, never real Qwen."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
RUNTIME = Path(__file__).resolve().parents[2] / 'natta-local-model'
spec = importlib.util.spec_from_file_location('evaluation', RUNTIME/'evaluate.py')
e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e)


def answer(choice, choices, concentration=1):
    maximum=(concentration*(len(choices)-1)+1)/len(choices)
    return dict(status='passed', selected_id=choice, probabilities={k:maximum if k==choice else (1-maximum)/(len(choices)-1) for k in choices}, concentration=concentration, model={'path':'/local/model'},load_seconds=0,inference_seconds=.01)


class FakeDecision:
    model_path=Path('/local/model')
    def decide(self,state,instructions,choices): return answer(next(iter(choices)),choices)


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.cases,self.descriptions=e.read_inputs(e.ROOT/'corpus.json',e.ROOT/'descriptions.json')
        self.case=self.cases[0]
        self.choices=self.descriptions['concise']

    def scored(self,expected,selected,concentration=1):
        return e.score({**self.case,'expected_choice':expected},answer(selected,self.choices,concentration),self.choices)

    def test_schema(self):
        self.assertEqual(len(self.cases),72)
        self.assertEqual({c['expected_choice'] for c in self.cases},set(e.CAPABILITIES))
        self.assertEqual(sum(c['expected_choice']=='no_match' for c in self.cases),22)
        for corpus in ([],{'schema_version':2,'cases':self.cases},{'schema_version':1,'cases':[]}):
            with self.assertRaises(ValueError): e.validate_corpus(corpus)
        for changes in ({'request':''},{'extra':'invalid'}):
            with self.assertRaises(ValueError): e.validate_corpus({'schema_version':1,'cases':[{**self.case,**changes}]})

    def test_invalid_expected(self):
        with self.assertRaisesRegex(ValueError,'expected capability'):
            e.validate_corpus({'schema_version':1,'cases':[{**self.case,'expected_choice':'codex'}]})

    def test_duplicate_ids(self):
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            e.validate_corpus({'schema_version':1,'cases':[self.case,self.case]})

    def test_descriptions(self):
        for change in ('empty','missing','unknown'):
            d=copy.deepcopy({'schema_version':1,**self.descriptions})
            if change=='empty': d['precise']['build']=''
            if change=='missing': del d['concise']['no_match']
            if change=='unknown': d['concise']['codex']='launch'
            with self.assertRaises(ValueError): e.validate_descriptions(d)

    def test_orders(self):
        self.assertEqual(e.orders(),e.orders())
        self.assertEqual(e.orders()['canonical'],list(e.CAPABILITIES))
        self.assertEqual(e.orders()['shuffle-20260404'],['projects','no_match','doctor','test','diff','status','build','context','verify'])
        self.assertEqual(e.orders(False)['shuffle-20260404'],['context','doctor','projects','diff','test','build','status','verify'])
        self.assertEqual(e.orders()['reversed'],list(reversed(e.CAPABILITIES)))
        for include in (True,False):
            for order in e.orders(include).values():
                self.assertEqual(set(order),set(e.CAPABILITIES if include else e.CAPABILITIES[:-1]))
                self.assertEqual(len(order),len(set(order)))

    def test_scoring(self):
        self.assertTrue(self.scored('build','build')['correct'])
        self.assertFalse(self.scored('build','test')['correct'])
        bad=answer('build',self.choices)
        bad['probabilities']['build']=float('nan')
        with self.assertRaises(ValueError): e.score(self.case,bad,self.choices)

    def test_accuracy_confusion_no_match(self):
        s=e.metrics([self.scored('build','build'),self.scored('test','build'),self.scored('no_match','no_match'),self.scored('no_match','diff')])
        self.assertEqual((s['correct'],s['incorrect'],s['accuracy']),(2,2,.5))
        self.assertIn({'expected':'test','selected':'build','count':1},s['confusion'])
        self.assertEqual(s['per_capability']['test']['accuracy'],0)
        self.assertEqual((s['no_match']['precision'],s['no_match']['recall']),(1,.5))
        self.assertIsNone(e.metrics([])['accuracy'])
        self.assertIsNone(e.metrics([self.scored('build','build')])['no_match']['recall'])

    def test_stability(self):
        rows=[{**self.scored('build','build'),'case_id':'a','order':'canonical'},{**self.scored('build','test'),'case_id':'a','order':'reversed'},{**self.scored('test','test'),'case_id':'b','order':'canonical'},{**self.scored('test','test'),'case_id':'b','order':'reversed'}]
        self.assertEqual(e.stability(rows)['rate'],.5)
        self.assertEqual(e.stability(rows)['changed_cases'],{'a':{'canonical':'build','reversed':'test'}})

    def test_thresholds_wrong_detection(self):
        s=e.metrics([self.scored('build','build',.99),self.scored('test','build',.95),self.scored('no_match','diff',.8)])
        t=next(t for t in s['thresholds'] if t['threshold']==.9)
        self.assertEqual((t['accepted_cases'],t['accepted_coverage'],t['accepted_accuracy'],t['confidently_wrong']),(2,2/3,.5,1))
        self.assertEqual(len(s['confidently_wrong']),1)
        self.assertEqual(s['concentration']['incorrect']['count'],2)

    def test_retained_format_default_no_persistence_and_no_execution(self):
        with patch('subprocess.run',side_effect=AssertionError('executed capability')),patch.dict(sys.modules,{'torch':None,'transformers':None,'openjev':None}):
            result=e.evaluate(self.cases[:2],self.descriptions,FakeDecision())
        self.assertEqual(result['total_decisions'],25)
        self.assertTrue(result['evaluation_only'])
        self.assertEqual(len(result['predictions']),24)
        self.assertEqual(result['verdict'],'NOT READY FOR ROUTING')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'result.json'
            e.persist(result,None)
            self.assertEqual(list(Path(tmp).iterdir()),[])
            e.persist(result,path)
            self.assertEqual(json.loads(path.read_text())['schema_version'],1)
            with self.assertRaises(FileExistsError): e.persist(result,path)

    def test_default_cli(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(e,'ROOT',Path(tmp)):
            for filename in ('corpus.json','descriptions.json'):
                (Path(tmp)/filename).write_bytes((RUNTIME/'evaluation'/filename).read_bytes())
            before={p.name:p.read_bytes() for p in Path(tmp).iterdir()}
            with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(e.main([],engine_factory=FakeDecision),0)
            self.assertEqual(before,{p.name:p.read_bytes() for p in Path(tmp).iterdir()})

    def test_local_offline_boundary(self):
        import os
        import decision
        engine=decision.LocalDecision()
        with patch.object(decision.contract,'validate_config'),patch.object(decision.contract,'validate_model',side_effect=ValueError('Missing local model artifact')),patch.dict(sys.modules,{'torch':None,'openjev':None}),patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(ValueError,'Missing local'): engine._load_engine()
            for key in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_HUB_DISABLE_IMPLICIT_TOKEN','HF_HUB_DISABLE_TELEMETRY'):
                self.assertEqual(os.environ[key],'1')

    def test_time_limit(self):
        with self.assertRaisesRegex(RuntimeError,'time limit'): e.evaluate(self.cases,self.descriptions,FakeDecision(),max_seconds=-1)


if __name__=='__main__': unittest.main()
