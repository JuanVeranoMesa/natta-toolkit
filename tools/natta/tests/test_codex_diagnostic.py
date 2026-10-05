"""One-shot diagnostic tests. Every provider request is mocked."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE.parent / 'natta-local-model'))
import diagnose_codex as d


def events(item, kind='item.completed'):
    return '\n'.join(json.dumps(e) for e in [
        {'type':'item.completed','item':{'type':'reasoning','text':'PRIVATE_REASONING'}},
        {'type':'item.completed','item':{'type':'agent_message','text':'PRIVATE_MESSAGE'}},
        {'type':kind,'item':item}, {'type':'turn.completed'}])


class CodexDiagnosticTests(unittest.TestCase):
    def test_extract_only_structured_error_fields(self):
        item = {'type':'error','error':{'code':'model_not_available','type':'provider_error',
                'status_code':403,'model_available':False,'quota_exceeded':True,
                'rate_limited':False,'authentication_error':False,'token':'PRIVATE_TOKEN'},
                'message':'PRIVATE_MESSAGE','payload':'PRIVATE_PAYLOAD'}
        expected = {k:v for k,v in item['error'].items() if k != 'token'}
        expected['error_type'] = expected.pop('type')
        for kind in ('item.started','item.updated','item.completed'):
            self.assertEqual(d.extract(events(item, kind)), [expected])
        self.assertNotIn('PRIVATE_', json.dumps(d.extract(events(item))))

    def test_text_only_fallback_and_no_unrelated_events(self):
        item = {'type':'error','message':'Model gpt-6-luna is unavailable.'}
        self.assertEqual(d.extract(events(item)), [{'message':item['message']}])
        self.assertEqual(d.extract(events({'type':'command_execution','command':'PRIVATE_COMMAND'})), [])
        self.assertEqual(d.extract('malformed PRIVATE_TEXT\nnull\n{}'), [])
        self.assertEqual(d.extract(events({'type':'error','error':'Quota exhausted.'})),
                         [{'message':'Quota exhausted.'}])

    def test_redaction_credentials_environment_and_terminal_controls(self):
        # Deliberately fake credential strings for redaction coverage.
        secrets = ('BEARER_VALUE', 'sk-proj-123456', 'JWTSECRET', 'ACCESS_VALUE',
                   'REFRESH_VALUE', 'PASSWORD_VALUE', '/private/home', 'USER', 'PASS', 'KEY_VALUE', 'BASIC_VALUE')
        text = ('Authorization: Bearer BEARER_VALUE; Authorization: Basic BASIC_VALUE; sk-proj-123456; '
                'eyJJWTSECRET.payload.signature; "access_token": "ACCESS_VALUE"; '
                'refresh_token=REFRESH_VALUE; password=PASSWORD_VALUE; HOME=/private/home; '
                'https://USER:PASS@example.com; api key: KEY_VALUE; '
                '\x1b[31mModel unavailable\x1b[0m\n')
        output = d.redact(text)
        for secret in secrets:
            self.assertNotIn(secret, output)
        self.assertNotIn('HOME=', output)
        self.assertNotIn('\x1b', output)
        self.assertNotIn('\n', output)
        self.assertIn('Model unavailable', output)
        self.assertLessEqual(len(d.redact('x'*3000 + ' sk-proj-SECRET')), 2000)

    def test_structured_metadata_is_bounded_and_redacted(self):
        fields = d.error_fields({'type':'error','code':'sk-proj-SECRET','status_code':401,
                                'error_type':{'secret':'PRIVATE'},'message':'PRIVATE'})
        self.assertEqual(fields, {'code':'[REDACTED]','status_code':401})
        self.assertEqual(d.error_fields({'type':'error','code':'x'*1000}),
                         {'diagnostic':'error_fields_unavailable'})

    def test_error_still_fails_closed(self):
        self.assertNotIn('error', d.router.ITEM_EFFECTS)
        with self.assertRaisesRegex(d.router.OutputFailure, 'boundary_violation') as caught:
            d.router.final_message(events({'type':'error','message':'provider issue'}))
        self.assertEqual(caught.exception.boundary_item_type, 'error')

    def test_exactly_one_request_same_isolation_and_no_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / 'router'
            calls = []
            def runner(argv, cwd, prompt):
                calls.append((argv,cwd,prompt))
                return 1, events({'type':'error','message':'Quota exhausted. token=PRIVATE_TOKEN'})
            out = io.StringIO()
            with patch.object(d.router,'workspace_path',return_value=path), \
                 patch.object(d.evaluation,'metadata') as metadata, \
                 patch.object(subprocess,'Popen',side_effect=AssertionError('real request')), \
                 contextlib.redirect_stdout(out):
                self.assertEqual(d.main([],runner=runner),1)
            metadata.assert_called_once_with('gpt-6-luna')
            self.assertEqual(len(calls),1)
            argv,cwd,prompt = calls[0]
            self.assertEqual(cwd,path)
            self.assertEqual(argv,d.router.invocation(path,'gpt-6-luna'))
            self.assertEqual(argv[argv.index('--sandbox')+1],'read-only')
            self.assertEqual(argv[argv.index('-a')+1],'never')
            self.assertIn('--ignore-user-config',argv)
            self.assertIn(('-c','suppress_unstable_features_warning=true'),list(zip(argv,argv[1:])))
            self.assertIn(('--enable','skip_host_skill_discovery'),list(zip(argv,argv[1:])))
            result=json.loads(out.getvalue())
            self.assertEqual(result['evaluation_error'],'boundary_violation')
            self.assertEqual(result['boundary_item_type'],'error')
            self.assertEqual(result['provider_error_items'],[{'message':'Quota exhausted. [REDACTED]'}])
            self.assertNotIn('PRIVATE_',out.getvalue())
            self.assertEqual({p.name for p in path.iterdir()},d.router.FILES)
            self.assertEqual({p.name for p in path.parent.iterdir()},{'router'})

    def test_no_raw_stderr_or_exception_or_cli_overrides(self):
        with patch.object(d.evaluation,'inputs',side_effect=ValueError('PRIVATE_CREDENTIAL')), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(d.main([]),1)
        self.assertEqual(json.loads(out.getvalue()),{'diagnostic':'setup_or_provider_failure'})
        with patch.object(d.evaluation,'inputs',side_effect=AssertionError('called')), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(d.main(['--model','other']),2)

    def test_single_subprocess_stderr_discarded_and_timeout_no_retry(self):
        with patch.object(d.subprocess,'Popen') as popen:
            child=popen.return_value.__enter__.return_value
            child.returncode=1
            child.communicate.return_value=('safe JSONL','PRIVATE_STDERR')
            self.assertEqual(d.one_request(['codex'],Path('/sterile'),'PRIVATE_PROMPT'),(1,'safe JSONL'))
            self.assertEqual(popen.call_count,1)
            self.assertTrue(popen.call_args.kwargs['start_new_session'])
        with patch.object(d.subprocess,'Popen') as popen, patch.object(d.os,'killpg') as kill:
            child=popen.return_value.__enter__.return_value; child.pid=1234
            child.communicate.side_effect=[subprocess.TimeoutExpired('codex',120),('','')]
            with self.assertRaisesRegex(d.router.OutputFailure,'provider_timeout'):
                d.one_request(['codex'],Path('/sterile'),'PRIVATE_PROMPT')
            self.assertEqual(popen.call_count,1)
            kill.assert_called_once_with(1234,d.signal.SIGKILL)


if __name__ == '__main__':
    unittest.main()
