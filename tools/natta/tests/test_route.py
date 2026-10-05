"""Production selection boundaries; all Codex model calls and CLI probes mocked."""
import ast
import contextlib
from dataclasses import FrozenInstanceError
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import natta
import routing
import router_codex as p


def events(choice='build', extra=()):
    return '\n'.join(json.dumps(e) for e in [
        {'type':'thread.started'}, {'type':'turn.started'}, *extra,
        {'type':'item.completed','item':{'type':'agent_message','text':json.dumps({'choice':choice})}},
        {'type':'turn.completed'}])


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name).resolve()/'router'
        for target,value in (('workspace_path',self.path),('probe_cli','codex-cli 0.160.0')):
            mock = patch.object(p,target,return_value=value)
            mock.start(); self.addCleanup(mock.stop)
        self.calls = []

    def invoke(self,*args):
        out,err = io.StringIO(),io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err): code = natta.main(list(args))
        return code,out.getvalue(),err.getvalue()

    def runner(self,value):
        def run(argv,cwd,text,timeout):
            self.calls.append((argv,cwd,text,timeout))
            return p.final_message(value)
        return run

    def test_parser_json_and_no_execution_options(self):
        for args in (['route','--json','Build Example'],['route','Build Example','--json']):
            parsed=natta.make_parser().parse_args(args)
            self.assertEqual((parsed.command,parsed.request,parsed.json),('route','Build Example',True))
            self.assertFalse(hasattr(parsed,'project'))
        for flag in ('--model','--level','--log','--verbose','--execute'):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                natta.make_parser().parse_args(['route','request',flag,'x'])

    def test_build_route_never_calls_build_or_project_inspection(self):
        with patch.object(p,'run_provider',side_effect=self.runner(events())), \
             patch.object(natta,'load_registry',return_value=[natta.Project('example', ('example-app',), 'Example App', Path('/uninspected/example'), 'generic-git', {})]), \
             patch.object(natta,'lookup',side_effect=AssertionError('lookup')), \
             patch.object(natta,'git_state',side_effect=AssertionError('app inspection')), \
             patch.object(natta,'orientation',side_effect=AssertionError('app contents')), \
             patch.object(natta.adapters,'resolve',side_effect=AssertionError('build handler')), \
             patch.object(natta,'execute',side_effect=AssertionError('execution')), \
             patch.object(natta,'launch_codex',side_effect=AssertionError('handoff')), \
             patch.object(natta,'ExecutionDirectory',side_effect=AssertionError('workflow')), \
             patch.object(natta.local_model,'doctor_checks',side_effect=AssertionError('Qwen')):
            code,out,err=self.invoke('--registry','/missing','route','--json','Build Example for the simulator')
        self.assertEqual((code,err),(0,''))
        result = json.loads(out)
        decision = result.pop('execution_policy')
        self.assertTrue(decision['eligible'])
        self.assertEqual(decision['authorization'], 'explicit')
        self.assertEqual(result,{'status':'matched','capability':'build','provider':'gpt-6-luna',
            'executed':False,'arguments_resolved':True,'error':None,'project':'example',
            'arguments':{},'missing_arguments':[],'resolution_error':None})
        self.assertEqual(len(self.calls),1)

    def test_all_known_choices_and_no_match(self):
        for choice in p.TOOLKIT_IDS:
            with patch.object(p,'run_provider',side_effect=self.runner(events(choice))): result=routing.select('request')
            self.assertEqual(result.capability,choice)
            self.assertEqual(result.status,'no_match' if choice=='no_match' else 'matched')
            self.assertIs(result.executed,False); self.assertIs(result.arguments_resolved,False)
        self.assertEqual(len(self.calls),len(p.TOOLKIT_IDS))

    def test_human_no_match_output_exit_zero(self):
        with patch.object(p,'run_provider',side_effect=self.runner(events('no_match'))):
            code,out,err=self.invoke('route','Commit and push Example')
        self.assertEqual((code,err),(0,''))
        self.assertEqual(out,'Route\n  status: no_match\n  capability: no_match\n  project: none\n  provider: gpt-6-luna\n  resolved: no\n  executed: no\n  authorization: forbidden\n  policy eligible: no\n  policy reason: no_match\n')

    def test_unknown_choices_and_arbitrary_arguments_fail_closed(self):
        for choice in ('delete_everything','codex','route'):
            with patch.object(p,'run_provider',side_effect=self.runner(events(choice))): result=routing.select('request')
            self.assertEqual((result.status,result.error,result.capability),('provider_error','unknown_choice',None))
        for text in ('garbage','{}','{"choice":"build","project":"example","level":2}',
                     '{"choice":"build","arguments":["rm"]}','{"choice":"build","choice":"test"}'):
            with patch.object(p,'run_provider',return_value=text): result=routing.select('request')
            self.assertEqual(result.error,'malformed_output'); self.assertIs(result.executed,False)
        answer={'status':'passed','selected_id':'build','error':None,'project':'example','arguments':['dangerous'],'reasoning':'PRIVATE'}
        with patch.object(p.CodexDecision,'decide',return_value=answer): result=routing.select('request')
        self.assertEqual(set(result.as_dict()),{'status','capability','provider','executed','arguments_resolved','error','project','arguments','missing_arguments','resolution_error','execution_policy'})
        self.assertNotIn('PRIVATE',json.dumps(result.as_dict()))
        with patch.object(p.CodexDecision,'decide',return_value={**answer,'selected_id':'unknown'}):
            self.assertEqual(routing.select('request').error,'unknown_choice')

    def test_failures_nonzero_no_fallback_retry_or_fabricated_no_match(self):
        for error in routing.ERRORS:
            with patch.object(p,'run_provider',side_effect=p.OutputFailure(error)) as run:
                code,out,err=self.invoke('route','--json','request')
            run.assert_called_once(); result=json.loads(out)
            self.assertEqual((code,err,result['status'],result['error']),(1,'','provider_error',error))
            self.assertIsNone(result['capability']); self.assertIs(result['executed'],False)
        for failure in (FileNotFoundError('PRIVATE'),subprocess.CalledProcessError(1,'PRIVATE')):
            with patch.object(p,'run_provider',side_effect=failure) as run: result=routing.select('request')
            run.assert_called_once(); self.assertEqual(result.error,'provider_failure')
            self.assertNotIn('PRIVATE',json.dumps(result.as_dict()))

    def test_prohibited_items_unknown_events_fail_closed(self):
        for item in ('error','command_execution','file_change','mcp_tool_call','web_search','unknown_item'):
            for kind in ('item.started','item.updated','item.completed'):
                stream=events(extra=[{'type':kind,'item':{'type':item,'payload':'PRIVATE'}}])
                with patch.object(p,'run_provider',side_effect=self.runner(stream)): result=routing.select('request')
                self.assertEqual(result.error,'boundary_violation'); self.assertIsNone(result.capability)
                self.assertIs(result.executed,False)
        for kind in ('unknown_event','item.future_action'):
            with patch.object(p,'run_provider',side_effect=self.runner(events(extra=[{'type':kind}]))):
                self.assertEqual(routing.select('request').error,'boundary_violation')
        for kind in ('error','turn.failed'):
            with patch.object(p,'run_provider',side_effect=self.runner(events(extra=[{'type':kind}]))):
                self.assertEqual(routing.select('request').error,'provider_failure')

    def test_exact_isolation_minimal_content_no_request_persistence(self):
        request='Build Example for the simulator'
        with patch.object(p,'run_provider',side_effect=self.runner(events())): routing.select(request)
        argv,cwd,text,timeout=self.calls[0]
        self.assertEqual(argv[argv.index('--model')+1],'gpt-6-luna')
        self.assertEqual((cwd,timeout),(self.path,120))
        for flag in ('--ignore-user-config','--ignore-rules','--strict-config','--ephemeral','--json','--no-daemon','--output-schema'): self.assertIn(flag,argv)
        pairs=list(zip(argv,argv[1:]))
        for feature in p.DISABLED: self.assertIn(('--disable',feature),pairs)
        for pair in (('--enable','skip_host_skill_discovery'),('--sandbox','read-only'),('-a','never'),
                     ('-c','web_search="disabled"'),('-c','shell_environment_policy.inherit="none"'),
                     ('-c','suppress_unstable_features_warning=true'),('-c','project_doc_max_bytes=0')): self.assertIn(pair,pairs)
        self.assertIn(json.dumps(request),text)
        for forbidden in ('expected_choice','projects.toml',str(natta.WORKSPACE_ROOT),os.environ.get('HOME','/fixture/private')): self.assertNotIn(forbidden,text)
        candidates=json.loads((self.path/'capabilities.json').read_text())
        self.assertEqual([c['id'] for c in candidates],list(p.TOOLKIT_IDS))
        self.assertEqual({c['id']:c['description'] for c in candidates},p.toolkit_definitions(routing_revision=True)['concise'])
        self.assertEqual({f.name for f in self.path.iterdir()},p.FILES)
        self.assertTrue(all(request not in f.read_text() for f in self.path.iterdir()))

    def test_missing_stale_regeneration_private_permissions(self):
        with patch.object(p,'run_provider',side_effect=self.runner(events())):
            self.assertEqual(routing.select('request').status,'matched')
            (self.path/'AGENTS.md').write_text('stale'); (self.path/'capabilities.json').chmod(0o644); self.path.chmod(0o755)
            self.assertEqual(routing.select('request').status,'matched')
        self.assertEqual(self.path.stat().st_mode & 0o777,0o700)
        self.assertTrue(all(f.stat().st_mode & 0o777==0o600 for f in self.path.iterdir()))
        p.validate_workspace(self.path,p.workspace_contents('concise',p.TOOLKIT_IDS))

    def test_unsafe_workspace_and_provider_mutation_rejected(self):
        p.generate(self.path,'concise',p.TOOLKIT_IDS)
        extra=self.path/'extra'; extra.write_text('keep')
        with patch.object(p,'run_provider',side_effect=AssertionError('model call')): result=routing.select('request')
        self.assertEqual(result.status,'routing_error'); self.assertEqual(extra.read_text(),'keep'); extra.unlink()
        file=self.path/'AGENTS.md'; file.unlink(); file.symlink_to(p.DEFINITIONS)
        with patch.object(p,'run_provider',side_effect=AssertionError('model call')): self.assertEqual(routing.select('request').status,'routing_error')
        file.unlink(); file.write_text('restored ordinary file'); p.generate(self.path,'concise',p.TOOLKIT_IDS)
        def mutate(*args):
            (self.path/'AGENTS.md').write_text('tampered'); return '{"choice":"build"}'
        with patch.object(p,'run_provider',side_effect=mutate): result=routing.select('request')
        self.assertEqual(result.status,'routing_error'); self.assertIsNone(result.capability); self.assertIs(result.executed,False)

    def test_invalid_request_cli_failures_zero_model_calls(self):
        with patch.object(p,'run_provider',side_effect=AssertionError('model call')):
            self.assertEqual(routing.select(' ').status,'invalid_request')
            for failure,status in ((FileNotFoundError(),'provider_error'),(ValueError(),'routing_error'),(subprocess.TimeoutExpired('codex',10),'provider_error')):
                with patch.object(p,'probe_cli',side_effect=failure): self.assertEqual(routing.select('request').status,status)

    def test_no_evaluation_dependency_or_dispatch_and_frozen_definition_parity(self):
        for name in ('routing.py','router_codex.py'):
            for node in ast.walk(ast.parse((natta.ROOT/name).read_text())):
                if isinstance(node,ast.Import): self.assertFalse(any(n.name in ('evaluate','evaluate_codex','local_model','execution','adapters','natta') for n in node.names))
                elif isinstance(node,ast.ImportFrom): self.assertNotIn(node.module,('evaluate_codex','execution','adapters','natta'))
        self.assertEqual(p.DEFINITIONS.read_bytes(),(natta.ROOT.parent/'natta-local-model/evaluation/descriptions.json').read_bytes())
        with self.assertRaises(FrozenInstanceError): routing.RouteResult('matched','build').executed=True


class RouterDoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name).resolve()/'router'
        mock=patch.object(p,'workspace_path',return_value=self.path); mock.start(); self.addCleanup(mock.stop)

    def test_read_only_missing_valid_stale_unsafe_workspace(self):
        with patch.object(p,'probe_cli',return_value='mock') as probe,patch.object(routing.shutil,'which',return_value='/fixture/codex'),patch.object(p,'run_provider',side_effect=AssertionError('model call')):
            self.assertTrue(all(ok for ok,_ in routing.doctor_checks())); self.assertFalse(self.path.exists())
            p.generate(self.path,'concise',p.TOOLKIT_IDS); before={f:f.read_bytes() for f in self.path.iterdir()}
            self.assertTrue(all(ok for ok,_ in routing.doctor_checks())); self.assertEqual(before,{f:f.read_bytes() for f in before})
            (self.path/'AGENTS.md').write_text('stale')
            self.assertTrue(any('stale' in label for _,label in routing.doctor_checks())); self.assertEqual((self.path/'AGENTS.md').read_text(),'stale')
            (self.path/'extra').write_text('keep')
            self.assertTrue(any(not ok and 'unsafe' in label for ok,label in routing.doctor_checks())); self.assertTrue((self.path/'extra').exists())
            self.assertEqual(probe.call_count,4)

    def test_missing_cli_invalid_definitions_and_compatibility(self):
        with patch.object(routing.shutil,'which',return_value=None),patch.object(p,'probe_cli',side_effect=AssertionError('probe')):
            self.assertIn((False,'Semantic router Codex CLI missing'),routing.doctor_checks())
        with patch.object(p,'definitions',side_effect=ValueError('PRIVATE')): self.assertEqual(routing.doctor_checks(),[(False,'Semantic router definitions invalid')])
        with patch.object(p,'probe_cli',side_effect=ValueError()),patch.object(routing.shutil,'which',return_value='codex'):
            self.assertTrue(any(not ok and 'compatibility' in label for ok,label in routing.doctor_checks()))

    def test_doctor_no_provider_call_optional_core_health(self):
        out=io.StringIO()
        with patch.object(p,'probe_cli',return_value='mock'),patch.object(natta.local_model,'doctor_checks',return_value=[]), \
             patch.object(natta.shutil,'which',side_effect=lambda name:str(natta.WORKSPACE_ROOT/'bin/natta') if name=='natta' else '/fixture/tool'), \
             patch.object(p,'run_provider',side_effect=AssertionError('model call')),contextlib.redirect_stdout(out): self.assertEqual(natta.doctor([]),0)
        self.assertIn('Semantic router CLI supports isolation',out.getvalue()); self.assertFalse(self.path.exists())
        with patch.object(routing,'doctor_checks',return_value=[(False,'Semantic router unavailable')]),patch.object(natta.local_model,'doctor_checks',return_value=[]), \
             patch.object(natta.shutil,'which',side_effect=lambda name:str(natta.WORKSPACE_ROOT/'bin/natta') if name=='natta' else '/fixture/tool'),contextlib.redirect_stdout(out): self.assertEqual(natta.doctor([]),0)
        self.assertIn('UNAVAILABLE  Semantic router unavailable',out.getvalue())

    def test_shared_cli_probe_flags_features_timeout(self):
        help_text='--output-schema --json --ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check --strict-config --sandbox'
        results=[SimpleNamespace(stdout='codex-cli 0.160.0'),SimpleNamespace(stdout=help_text),SimpleNamespace(stdout='\n'.join((*p.DISABLED,'skip_host_skill_discovery')))]
        with patch.object(p.subprocess,'run',side_effect=results) as run: self.assertEqual(p.probe_cli(self.path),'codex-cli 0.160.0')
        self.assertEqual([c.args[0] for c in run.call_args_list],[['codex','--version'],['codex','exec','--help'],['codex','features','list']])
        self.assertTrue(all(c.kwargs['timeout']==10 and c.kwargs['cwd']==self.path for c in run.call_args_list))
        for broken in ([results[0],SimpleNamespace(stdout='old')],[*results[:2],SimpleNamespace(stdout='shell_tool')]):
            with patch.object(p.subprocess,'run',side_effect=broken),self.assertRaises(ValueError): p.probe_cli(self.path)


if __name__=='__main__': unittest.main()
