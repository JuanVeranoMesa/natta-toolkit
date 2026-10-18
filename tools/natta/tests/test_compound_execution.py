"""Phase 8B: mocked planning/workflows; real commits only in disposable repos."""
import contextlib
from dataclasses import FrozenInstanceError, replace
import io
import json
import unittest
from unittest.mock import Mock, patch

import authorization
import compound_execution as compound
import confinement
import natta
import planning
import planner_provider
planner = planner_provider.CURRENT
import policy
import router_codex as provider
import runtime_effects
import semantic_execution as gate
import test_authorization as auth_fixture
import test_commits as commit_fixture
import test_semantic_execution as fixture


class CompoundTests(unittest.TestCase):
    setUp = fixture.SemanticExecutionTests.setUp
    bindings = fixture.SemanticExecutionTests.bindings

    def candidate(self, caps):
        return {'status': 'planned', 'steps': [
            {'capability': c, 'project': 'example', 'arguments': {'level': 2} if c == 'verify' else {}}
            for c in caps]}

    @staticmethod
    def goal(candidate):
        """Untrusted goal text that names the candidate's projects and levels, as a
        user would; local reconciliation requires both (NTT-010)."""
        steps = candidate.get('steps', []) if isinstance(candidate, dict) else []
        projects = [s['project'] for s in steps if isinstance(s, dict) and isinstance(s.get('project'), str)]
        levels = [s['arguments']['level'] for s in steps if isinstance(s, dict) and isinstance(s.get('arguments'), dict)
                  and type(s['arguments'].get('level')) is int]
        names = ' and '.join(dict.fromkeys(projects)) or 'nothing'
        return f'Untrusted goal for {names}' + ''.join(f' at level {v}' for v in dict.fromkeys(levels)) + '; never handler data'

    def invoke(self, caps=('build','test'), *, confirm=False, json_mode=True,
               response='y\n', terminal=True, handlers=None, candidate=None, approve=False):
        """approve=True answers yes at the post-plan terminal prompt and returns the
        structured result (JSON mode never prompts, so explicit plans need this)."""
        handlers = self.bindings() if handlers is None else handlers
        stdin = auth_fixture.Terminal(response) if terminal else io.StringIO(response)
        stdout = auth_fixture.Terminal()
        candidate = self.candidate(caps) if candidate is None else candidate
        reply = planner.Reply(text=json.dumps(candidate), external_requests=1)
        args = ['--registry', str(self.registry), 'do', self.goal(candidate)]
        if confirm: args.append('--confirm')
        if json_mode and not approve: args.append('--json')
        results = []
        with contextlib.ExitStack() as stack:
            propose = stack.enter_context(patch.object(planner, 'propose', return_value=reply))
            stack.enter_context(patch.object(provider, 'run_provider', side_effect=AssertionError('per-step model call')))
            stack.enter_context(patch.object(natta, 'prepare_route', side_effect=AssertionError('routing')))
            stack.enter_context(patch.object(natta, 'handler_bindings', return_value=handlers))
            stack.enter_context(patch('sys.stdin', stdin))
            stack.enter_context(patch('sys.stdout', stdout))
            stack.enter_context(patch.object(runtime_effects, 'capture_commit', return_value=runtime_effects.capture.return_value))
            if approve:
                stack.enter_context(patch.object(authorization, 'request_approval', return_value=True))
                stack.enter_context(patch.object(compound, 'render', side_effect=lambda r: results.append(r) or ''))
            code = natta.main(args)
        propose.assert_called_once()
        if approve:
            output = results[0].as_dict()
        else:
            output = json.loads(stdout.getvalue()) if json_mode else stdout.getvalue()
        return code, output, handlers, stdin

    def test_two_three_four_steps_once_in_order(self):
        for caps in (('build','test'), ('build','test','verify'), ('diff','build','test','commit')):
            events = [];handlers = self.bindings()
            for cap in caps:
                handlers[cap].side_effect = lambda *args, cap=cap: (events.append(cap), gate.HandlerOutcome(0))[1]
            code, data, handlers, _ = self.invoke(caps, approve=True, handlers=handlers)
            self.assertEqual(code, 0);self.assertEqual(events, list(caps))
            self.assertEqual(data['status'], 'executed');self.assertTrue(data['execution_succeeded'])
            self.assertEqual([s['index'] for s in data['steps']], list(range(1,len(caps)+1)))
            for cap in caps: self.assertEqual(handlers[cap].call_count, 1)
            self.assertTrue(all(s['effect_verification']['performed'] for s in data['steps']))
            self.assertEqual(data['plan']['external_requests'], 1)
            self.assertIsNone(data['failed_step'])

    def test_actual_planner_transport_one_call_no_step_routing(self):
        import planner_provider as base
        handlers=self.bindings()
        with patch.object(base,'workspace_path',return_value=self.root/'planner'), \
             patch.object(provider,'run_provider',return_value=json.dumps(self.candidate(('diff','build','test','verify')))) as run, \
             patch.object(natta,'prepare_route',side_effect=AssertionError('per-step routing')), \
             patch.object(natta,'handler_bindings',return_value=handlers),contextlib.redirect_stdout(io.StringIO()) as out:
            with patch.object(authorization,'request_approval',return_value=True) as prompt:
                code=natta.main(['--registry',str(self.registry),'do','Show Example changes, build, test, then verify it at level 2'])
        self.assertEqual(code,0);run.assert_called_once();prompt.assert_called_once()
        self.assertIn('Result: passed',out.getvalue())
        self.assertTrue(all(handlers[c].call_count==1 for c in ('diff','build','test','verify')))

    def test_automatic_never_prompts(self):
        with patch.object(authorization,'request_approval',side_effect=AssertionError('prompt')):
            code,data,_,_ = self.invoke(('status','diff'))
        self.assertEqual(code,0);self.assertEqual(data['authorization'],'automatic')

    def test_explicit_approval_once_before_automatic_prefix(self):
        handlers=self.bindings()
        def approve(*args, **kwargs):
            self.assertFalse(any(h.called for h in handlers.values()))
            runtime_effects.capture.assert_not_called()
            return True
        with patch.object(authorization,'request_approval',side_effect=approve) as prompt:
            code,out,_,_=self.invoke(('diff','build'),json_mode=False,handlers=handlers)
        prompt.assert_called_once();self.assertEqual(code,0)
        self.assertIn('1. diff',out);self.assertIn('Example App',out)
        self.assertNotIn('"execution_policy"',out)
        self.assertEqual(out.count('Plan\n'),1)

    def test_y_yes_authorize_once(self):
        for response in ('y\n','yes\n',' YES \n'):
            code,out,handlers,_=self.invoke(json_mode=False,response=response)
            self.assertEqual(code,0);self.assertEqual(out.count('Continue? [y/N]:'),1)
            self.assertEqual(handlers['build'].call_count,1)

    def test_authorization_summary_names_every_frozen_step_and_argument(self):
        candidate=self.candidate(('diff','build','test','verify'))
        candidate['steps'][3].update(project='SAMPLE-APP',arguments={'level':3})
        code,out,handlers,_=self.invoke(candidate=candidate,json_mode=False)
        self.assertEqual(code,0)
        summary=out.split('Authorization required:',1)[1].split('Continue?',1)[0]
        self.assertIn('Pressing y authorizes execution of the displayed plan:',summary)
        for text in ('1. diff    Example App','2. build   Example App',
                     '3. test    Example App','4. verify  Sample App level=3'):
            self.assertIn(text,summary)
        self.assertNotIn('SAMPLE-APP',summary)
        self.assertNotIn('Untrusted goal',summary)
        self.assertNotIn('commit',summary.lower())
        self.assertEqual(out.count('Continue? [y/N]:'),1)
        self.assertEqual(handlers['verify'].call_args.args[1].alias,'sample')
        self.assertEqual(handlers['verify'].call_args.args[2],(('level',3),))

    def test_commit_summary_explicit_all_changes_local_no_push(self):
        code,out,_,_=self.invoke(('build','test','commit'),json_mode=False,response='no\n')
        self.assertEqual(code,1)
        summary=out.split('Authorization required:',1)[1].split('Continue?',1)[0]
        self.assertIn('3. commit  Example App',summary)
        self.assertIn('staging all current changes',summary)
        self.assertIn('creating ONE local commit',summary)
        self.assertIn('containing all current changes in Example App.',summary)
        self.assertIn('It will NOT push.',summary)
        self.assertEqual(out.count('Continue? [y/N]:'),1)

    def test_each_commit_consequence_uses_its_validated_project(self):
        candidate=self.candidate(('commit','commit'))
        candidate['steps'][1]['project']='sample'
        _,out,_,_=self.invoke(candidate=candidate,json_mode=False,response='no\n')
        summary=out.split('Authorization required:',1)[1].split('Continue?',1)[0]
        self.assertIn('Step 1 includes staging all current changes',summary)
        self.assertIn('Step 2 includes staging all current changes',summary)
        self.assertIn('containing all current changes in Example App.',summary)
        self.assertIn('containing all current changes in Sample App.',summary)

    def test_summary_rendering_does_not_mutate_frozen_plan(self):
        projects=natta.load_registry(self.registry)
        plan=planning.validate(self.candidate(('verify','commit')),natta.make_parser(),projects,self.bindings())
        before=plan.as_dict()
        summary=compound.authorization_notice(plan,projects)
        self.assertIn('verify  Example App level=2',summary)
        self.assertEqual(plan.as_dict(),before)

    def test_decline_blocks_entire_plan(self):
        for response in ('no\n','n\n','\n','','sure\n'):
            runtime_effects.capture.reset_mock()
            code,out,handlers,_=self.invoke(('diff','build'),json_mode=False,response=response)
            self.assertEqual(code,1);self.assertIn('authorization_declined',out)
            self.assertFalse(any(h.called for h in handlers.values()))
            runtime_effects.capture.assert_not_called()

    def test_confirm_cannot_preauthorize_explicit_plan(self):
        # NTT-004: --confirm predates the model's plan, so it cannot name the
        # explicit steps. Refused after the one planner call, before any prompt,
        # snapshot or handler, in both JSON and terminal modes.
        for caps in (('build','test'),('diff','commit'),('testflight',),('status','verify')):
            for json_mode in (True,False):
                runtime_effects.capture.reset_mock()
                with self.subTest(caps=caps,json_mode=json_mode), \
                     patch.object(authorization,'request_approval',side_effect=AssertionError('prompt')):
                    code,data,handlers,stdin=self.invoke(caps,confirm=True,json_mode=json_mode)
                    self.assertEqual(code,1);self.assertEqual(stdin.tell(),0)
                    if json_mode:
                        self.assertEqual((data['status'],data['error']),('authorization_required','compound_preauthorization_unsupported'))
                        self.assertFalse(data['execution_started']);self.assertFalse(data['authorization_satisfied'])
                        self.assertTrue(all(s['status']=='not_started' for s in data['steps']))
                    else:
                        self.assertIn('compound_preauthorization_unsupported',data)
                        self.assertNotIn('Continue?',data)
                    self.assertFalse(any(h.called for h in handlers.values()))
                    runtime_effects.capture.assert_not_called()

    def test_confirm_on_automatic_plan_grants_nothing_and_never_prompts(self):
        with patch.object(authorization,'request_approval',side_effect=AssertionError('prompt')):
            code,data,handlers,stdin=self.invoke(('status','diff'),confirm=True)
        self.assertEqual(code,0);self.assertEqual(stdin.tell(),0)
        self.assertEqual(data['authorization'],'automatic')
        self.assertEqual(handlers['status'].call_count,1);self.assertEqual(handlers['diff'].call_count,1)

    def test_json_never_prompts(self):
        with patch.object(authorization,'request_approval',side_effect=AssertionError('prompt')):
            code,data,handlers,stdin=self.invoke(('diff','build'))
        self.assertEqual(code,1);self.assertEqual(data['status'],'authorization_required')
        self.assertFalse(data['execution_started']);self.assertIsNone(data['execution_succeeded'])
        self.assertTrue(all(s['status']=='not_started' for s in data['steps']))
        self.assertFalse(any(h.called for h in handlers.values()));self.assertEqual(stdin.tell(),0)

    def test_non_tty_never_prompts(self):
        code,out,handlers,stdin=self.invoke(json_mode=False,terminal=False)
        self.assertEqual(code,1);self.assertIn('authorization_required',out)
        self.assertNotIn('Continue?',out);self.assertEqual(stdin.tell(),0)
        self.assertFalse(any(h.called for h in handlers.values()))

    def test_each_failure_position_skips_suffix(self):
        caps=('diff','build','test','commit')
        for index,cap in enumerate(caps,1):
            handlers=self.bindings();handlers[cap].return_value=gate.HandlerOutcome(7)
            code,data,_,_=self.invoke(caps,approve=True,handlers=handlers)
            self.assertEqual(code,7);self.assertEqual(data['failed_step'],index)
            self.assertFalse(data['execution_succeeded'])
            for pos,c in enumerate(caps,1):self.assertEqual(handlers[c].call_count,int(pos<=index))
            self.assertTrue(all(s['status']=='not_started' for s in data['steps'][index:]))

    def test_handler_exception_stops(self):
        handlers=self.bindings();handlers['build'].side_effect=ValueError('bad handler')
        code,data,_,_=self.invoke(approve=True,handlers=handlers)
        self.assertEqual(code,1);self.assertEqual(data['error'],'handler_error')
        handlers['test'].assert_not_called()

    def test_effect_violation_stops_commit(self):
        violation=runtime_effects.VerificationResult(performed=True,passed=False,
            violations=frozenset({policy.Effect.PROJECT_WRITE}),reason='effect_violation')
        with patch.object(runtime_effects,'compare',return_value=violation):
            code,data,handlers,_=self.invoke(('build','test','commit'),approve=True)
        self.assertEqual(code,1);self.assertEqual(data['status'],'effect_violation')
        self.assertEqual(data['failed_step'],1);handlers['commit'].assert_not_called()
        self.assertEqual(data['steps'][0]['effect_verification']['violations'],['project_write'])

    def test_verification_unavailable_stops(self):
        with patch.object(runtime_effects,'capture',side_effect=OSError('unavailable')):
            code,data,handlers,_=self.invoke(approve=True)
        self.assertEqual(code,1);self.assertEqual(data['failed_step'],1)
        self.assertFalse(data['execution_started']);self.assertFalse(any(h.called for h in handlers.values()))

    def test_inspection_confinement_per_step_workflows_unconfined(self):
        with patch.object(confinement,'open_session',wraps=confinement.open_session) as session:
            code,data,_,_=self.invoke(('status','diff','build','verify'),approve=True)
        self.assertEqual(code,0);self.assertEqual(session.call_count,2)
        self.assertTrue(all(s['confinement']['applied'] for s in data['steps'][:2]))
        self.assertTrue(all(not s['confinement']['required'] and not s['confinement']['applied'] for s in data['steps'][2:]))

    def test_loss_of_inspection_validation_stops(self):
        with patch.object(confinement,'open_session',side_effect=confinement.ConfinementUnavailable('validation_stale')):
            code,data,handlers,_=self.invoke(('diff','build'),approve=True)
        self.assertEqual(code,1);self.assertEqual(data['status'],'confinement_unavailable')
        self.assertFalse(any(h.called for h in handlers.values()))

    def test_whole_plan_local_rejections(self):
        candidates=[self.candidate(('diff',)*5), {'status':'no_match','steps':[]},
            self.candidate(('shell',)),self.candidate(('push',)),self.candidate(('doctor',))]
        unknown=self.candidate(('diff','build'));unknown['steps'][1]['project']='invented';candidates.append(unknown)
        for candidate in candidates:
            with patch.object(authorization,'request_approval',side_effect=AssertionError('prompt')):
                code,data,handlers,_=self.invoke(candidate=candidate,approve=True)
            self.assertEqual(code,1);self.assertFalse(data['execution_started'])
            self.assertFalse(any(h.called for h in handlers.values()))

    def test_forbidden_policy_cannot_enter_execution(self):
        catalog=dict(policy.CATALOG);catalog['build']=replace(catalog['build'],authorization=policy.Authorization.FORBIDDEN)
        with patch.object(policy,'CATALOG',catalog):
            code,data,handlers,_=self.invoke(approve=True)
        self.assertEqual(code,1);self.assertEqual(data['status'],'invalid_plan')
        self.assertFalse(any(h.called for h in handlers.values()))

    def test_frozen_plan_and_arguments_unchanged(self):
        original=planning.validate(self.candidate(('diff','verify')),natta.make_parser(),natta.load_registry(self.registry),self.bindings())
        before=original.as_dict();handlers=self.bindings()
        with patch.object(planning,'propose',return_value=original),contextlib.redirect_stdout(io.StringIO()), \
             patch.object(authorization,'request_approval',return_value=True):
            result=compound.run('Show Example changes then verify it at level 2',natta.make_parser(),lambda:natta.load_registry(self.registry),handlers)
        self.assertIs(result.plan,original);self.assertEqual(original.as_dict(),before)
        self.assertEqual(handlers['verify'].call_args.args[2],(('level',2),))
        with self.assertRaises(FrozenInstanceError):original.steps[0].capability='build'
        exposed=result.as_dict();exposed['plan']['steps'][1]['arguments']['level']=3
        self.assertEqual(original.as_dict(),before)

    def test_target_change_during_approval_blocks_prefix(self):
        handlers=self.bindings()
        def approve(*args,**kwargs):
            text=self.registry.read_text().replace('Example App','Changed Example')
            self.registry.write_text(text)
            return True
        with patch.object(authorization,'request_approval',side_effect=approve):
            code,out,_,_=self.invoke(('diff','build'),json_mode=False,handlers=handlers)
        self.assertEqual(code,1);self.assertIn('authorization_target_changed',out)
        self.assertFalse(any(h.called for h in handlers.values()))

    def test_later_handler_change_during_approval_blocks_prefix(self):
        handlers=self.bindings()
        def approve(*args,**kwargs):handlers['build']=Mock();return True
        with patch.object(authorization,'request_approval',side_effect=approve):
            code,out,_,_=self.invoke(('diff','build'),json_mode=False,handlers=handlers)
        self.assertEqual(code,1);self.assertFalse(any(h.called for h in handlers.values()))

    def test_registry_change_between_steps_stops(self):
        handlers=self.bindings()
        def build(*args):
            self.registry.write_text(self.registry.read_text().replace('Example App','Changed Example'))
            return gate.HandlerOutcome(0)
        handlers['build'].side_effect=build
        code,data,_,_=self.invoke(approve=True,handlers=handlers)
        self.assertEqual(code,1);self.assertEqual(data['failed_step'],2)
        self.assertEqual(data['error'],'authorization_target_changed');handlers['test'].assert_not_called()

    def test_tampered_plan_summary_rejected(self):
        original=planning.validate(self.candidate(('build','test')),natta.make_parser(),natta.load_registry(self.registry),self.bindings())
        handlers=self.bindings()
        with patch.object(planning,'propose',return_value=replace(original,authorization=policy.Authorization.AUTOMATIC)):
            result=compound.run('ignored',natta.make_parser(),lambda:natta.load_registry(self.registry),handlers,confirm=True)
        self.assertEqual(result.status,'invalid_plan');self.assertFalse(any(h.called for h in handlers.values()))

    def test_single_and_explicit_duplicate_occurrences(self):
        for caps in (('build',),('build','build')):
            code,data,handlers,_=self.invoke(caps,approve=True)
            self.assertEqual(code,0);self.assertEqual(handlers['build'].call_count,len(caps))
            self.assertTrue(all(not s['confinement']['required'] for s in data['steps']))

    def test_unresolved_project_and_invalid_arguments_block_whole_plan(self):
        for project,arguments in ((None,{}),('example',{'level':True}),('example',{'level':4})):
            candidate=self.candidate(('diff','verify'))
            candidate['steps'][1].update(project=project,arguments=arguments)
            code,data,handlers,_=self.invoke(candidate=candidate,approve=True)
            self.assertEqual(code,1);self.assertEqual(data['status'],'invalid_plan')
            self.assertFalse(any(h.called for h in handlers.values()))

    def test_provider_error_starts_nothing(self):
        with patch.object(planning,'propose',return_value=planning.PlanResult('provider_error',error='provider_failure')):
            result=compound.run('ignored',natta.make_parser(),lambda:natta.load_registry(self.registry),self.bindings(),confirm=True)
        self.assertEqual(result.status,'provider_error');self.assertFalse(result.execution_started)
        self.assertEqual(result.as_dict()['steps'],[])

    def test_declined_result_contract(self):
        handlers=self.bindings()
        plan=planning.validate(self.candidate(('diff','build')),natta.make_parser(),natta.load_registry(self.registry),handlers)
        with patch.object(planning,'propose',return_value=plan), \
             patch.object(authorization,'request_approval',return_value=False),contextlib.redirect_stdout(io.StringIO()):
            result=compound.run('Show Example changes then build it',natta.make_parser(),lambda:natta.load_registry(self.registry),handlers)
        data=result.as_dict()
        self.assertEqual(data['status'],'authorization_declined');self.assertFalse(data['execution_started'])
        self.assertFalse(data['authorization_satisfied']);self.assertIsNone(data['failed_step'])
        self.assertTrue(all(s['status']=='not_started' for s in data['steps']))

    def test_do_not_a_routable_capability(self):
        parser=natta.make_parser()
        self.assertNotIn('do',provider.authoritative_ids())
        with self.assertRaises(ValueError):natta.parameters.schema_for(parser,'do')
        self.assertTrue(parser.parse_args(['do','--confirm','--json','goal']).confirm)


class CompoundCommitTests(unittest.TestCase):
    setUp=commit_fixture.CommitTests.setUp
    git=commit_fixture.CommitTests.git
    dirty=commit_fixture.CommitTests.dirty

    def test_disposable_commit_uses_existing_handler_and_never_pushes(self):
        self.dirty();bindings=natta.handler_bindings();handlers=dict(bindings)
        handlers['build']=Mock(return_value=gate.HandlerOutcome(0))
        handlers['test']=Mock(return_value=gate.HandlerOutcome(0))
        handlers['commit']=Mock(wraps=bindings['commit'])
        candidate={'status':'planned','steps':[{'capability':c,'project':'example','arguments':{}} for c in ('build','test','commit')]}
        reply=planner.Reply(text=json.dumps(candidate),external_requests=1)
        commands=[];results=[]
        import subprocess
        real_run=subprocess.run
        def record(argv,*args,**kwargs):
            commands.append(argv);return real_run(argv,*args,**kwargs)
        with patch.object(planner,'propose',return_value=reply) as propose, \
             patch.object(provider,'run_provider',side_effect=AssertionError('Luna')), \
             patch.object(subprocess,'run',side_effect=record), \
             patch.object(natta,'handler_bindings',return_value=handlers),contextlib.redirect_stdout(io.StringIO()), \
             patch.object(authorization,'request_approval',return_value=True), \
             patch.object(compound,'render',side_effect=lambda r:results.append(r) or ''):
            code=natta.main(['--registry',str(self.registry),'do','build test commit fixture'])
        data=results[0].as_dict();self.assertEqual(code,0);propose.assert_called_once()
        commit=data['steps'][2]['result']['commit']
        self.assertTrue(commit['committed']);self.assertFalse(commit['pushed'])
        self.assertEqual(self.git('rev-parse','HEAD^').strip().decode(),self.initial)
        self.assertEqual(self.git('status','--porcelain'),b'')
        self.assertTrue(data['steps'][2]['effect_verification']['passed'])
        self.assertEqual(set(data['steps'][2]['effect_verification']['observed_effects']),{'git_index_write','git_commit'})
        self.assertFalse(any('push' in argv for argv in commands))
        self.assertEqual(handlers['commit'].call_count,1)
