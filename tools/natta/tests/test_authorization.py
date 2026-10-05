"""Interactive semantic authorization, mocked provider/handlers and terminal IO."""
import contextlib
from dataclasses import FrozenInstanceError, replace
import io
import json
import unittest
from unittest.mock import patch

import authorization
import natta
import policy
import router_codex as provider
import runtime_effects
import semantic_execution as gate
import test_semantic_execution as fixtures


class Terminal(io.StringIO):
    def isatty(self):
        return True


class InteractiveAuthorizationTests(unittest.TestCase):
    setUp = fixtures.SemanticExecutionTests.setUp
    bindings = fixtures.SemanticExecutionTests.bindings
    route = fixtures.SemanticExecutionTests.route

    def invoke(self, request='Build Example', capability='build', response='y\n', *,
               json_mode=False, confirm=False, terminal=True):
        stdin = Terminal(response) if terminal else io.StringIO(response)
        stdout = Terminal()
        handlers = self.bindings()
        args = ['--registry', str(self.registry), 'execute', request]
        if json_mode: args.append('--json')
        if confirm: args.append('--confirm')
        evidence = gate.runtime_effects.capture.return_value
        with patch.object(provider, 'run_provider', return_value=json.dumps({'choice':capability})) as run, \
             patch.object(natta, 'handler_bindings', return_value=handlers), \
             patch('sys.stdin',stdin), patch('sys.stdout',stdout), \
             patch.object(runtime_effects,'capture_commit',return_value=evidence) as commit_capture:
            code = natta.main(args)
        self.assertEqual(run.call_count,1)
        return code, stdout.getvalue(), handlers, stdin, commit_capture

    def test_affirmatives_continue_once_without_reroute(self):
        for response in ('y\n','yes\n','Y\n','YES\n',' yes \n'):
            with self.subTest(response=response):
                code,out,handlers,_,_=self.invoke(response=response)
                self.assertEqual(code,0)
                self.assertEqual(sum(h.call_count for h in handlers.values()),1)
                self.assertEqual(handlers['build'].call_args.args[1].alias,'example')
                self.assertEqual(handlers['build'].call_args.args[2],())
                self.assertIn('build  Example App',out)
                self.assertIn('Continue? [y/N]:',out)
                self.assertIn('protected state: passed',out)

    def test_single_action_notice_identifies_exact_validated_action(self):
        cases=(('Build Example','build','Authorize build of Example App?'),
               ('Test Sample','test','Authorize test of Sample App?'),
               ('Verify Sample at level 2','verify','Authorize verify of Sample App at level 2?'))
        for request,cap,notice in cases:
            code,out,handlers,_,_=self.invoke(request,cap)
            self.assertEqual(code,0);self.assertIn(notice,out)
            self.assertNotIn('local commit',out)
            self.assertEqual(out.count('Continue? [y/N]:'),1)
            self.assertEqual(handlers[cap].call_count,1)

    def test_single_commit_notice_explicit_staging_all_local_no_push(self):
        code,out,handlers,_,_=self.invoke('Commit Example','commit',response='no\n')
        self.assertEqual(code,1)
        self.assertIn('Authorize staging all current changes and creating ONE local commit',out)
        self.assertIn('containing all current changes in Example App?',out)
        self.assertIn('It will NOT push.',out)
        self.assertEqual(out.count('Continue? [y/N]:'),1)
        self.assertFalse(any(h.called for h in handlers.values()))

    def test_single_notice_is_derived_from_frozen_route_and_local_project(self):
        route=self.route('verify',project='sample',level=3)
        project=next(p for p in natta.load_registry(self.registry) if p.alias=='sample')
        with patch.object(authorization,'request_approval',return_value=False) as prompt:
            self.assertFalse(natta.authorize_resolved(route,project))
        self.assertEqual(prompt.call_args.kwargs['notice'],'Authorize verify of Sample App at level 3?')
        self.assertEqual(route.arguments,(('level',3),))

    def test_declines_never_start_handler_or_snapshot(self):
        for response in ('\n','n\n','no\n','definitely run it\n',''):
            for capability in ('build','commit'):
                with self.subTest(response=response,capability=capability):
                    gate.runtime_effects.capture.reset_mock()
                    code,out,handlers,_,commit_capture=self.invoke(
                        capability.title()+' Example',capability,response)
                    self.assertEqual(code,1)
                    self.assertIn('authorization_declined',out)
                    self.assertIn('execution started: no',out)
                    self.assertIn('Authorization declined.',out)
                    self.assertEqual(sum(h.call_count for h in handlers.values()),0)
                    gate.runtime_effects.capture.assert_not_called()
                    commit_capture.assert_not_called()

    def test_verify_target_and_arguments_are_frozen(self):
        _,out,handlers,_,_=self.invoke('Verify Sample at level 2','verify')
        self.assertIn('verify  Sample App level=2',out)
        self.assertEqual(handlers['verify'].call_args.args[1].alias,'sample')
        self.assertEqual(handlers['verify'].call_args.args[2],(('level',2),))
        with self.assertRaises(FrozenInstanceError): self.route().capability='test'

    def test_json_never_prompts_even_with_terminal_streams(self):
        with patch.object(authorization,'request_approval',side_effect=AssertionError('prompt')):
            code,out,handlers,stdin,_=self.invoke(json_mode=True)
        result=json.loads(out)
        self.assertEqual(code,1)
        self.assertEqual(result['status'],'authorization_required')
        self.assertFalse(result['executed'])
        self.assertFalse(result['authorization_satisfied'])
        self.assertEqual(stdin.tell(),0)
        self.assertEqual(sum(h.call_count for h in handlers.values()),0)

    def test_confirm_skips_prompt_and_keeps_one_call(self):
        with patch.object(authorization,'request_approval',side_effect=AssertionError('prompt')):
            code,_,handlers,stdin,_=self.invoke(confirm=True)
        self.assertEqual(code,0)
        self.assertEqual(handlers['build'].call_count,1)
        self.assertEqual(stdin.tell(),0)

    def test_non_tty_does_not_read_or_prompt(self):
        code,out,handlers,stdin,_=self.invoke(terminal=False)
        self.assertEqual(code,1)
        self.assertIn('authorization_required',out)
        self.assertNotIn('Continue?',out)
        self.assertEqual(stdin.tell(),0)
        self.assertEqual(sum(h.call_count for h in handlers.values()),0)

    def test_redirected_stdout_never_prompts(self):
        source=Terminal('yes\n');out=io.StringIO()
        self.assertIsNone(authorization.request_approval(('build Example',),stdin=source,stdout=out))
        self.assertEqual(source.tell(),0)
        self.assertEqual(out.getvalue(),'')

    def test_eof_error_declines_and_interrupt_never_dispatches(self):
        source=Terminal();out=Terminal()
        with patch.object(source,'readline',side_effect=EOFError):
            self.assertFalse(authorization.request_approval(('build Example',),stdin=source,stdout=out))
        with patch.object(authorization,'request_approval',side_effect=KeyboardInterrupt),self.assertRaises(KeyboardInterrupt):
            self.invoke()
        gate.runtime_effects.capture.assert_not_called()

    def test_automatic_actions_never_prompt(self):
        for cap in ('status','context','diff'):
            with patch.object(authorization,'request_approval',side_effect=AssertionError('prompt')):
                code,_,handlers,_,_=self.invoke(cap.title()+' Sample',cap)
            self.assertEqual(code,0)
            self.assertEqual(handlers[cap].call_count,1)

    def test_invalid_routes_never_prompt(self):
        for cap,request in [('no_match','Commit and push Example'),('build','Build the project'),
                            ('verify','Verify Sample'),('verify','Verify Sample level 4')]:
            with patch.object(authorization,'request_approval',side_effect=AssertionError('prompt')):
                code,_,handlers,_,_=self.invoke(request,cap)
            self.assertEqual(code,1)
            self.assertEqual(sum(h.call_count for h in handlers.values()),0)

    def dispatch(self, route, callback, *, loader=None, handlers=None):
        return gate.dispatch(route,natta.make_parser(),loader or (lambda:natta.load_registry(self.registry)),
            provider.authoritative_ids()-{'codex','route'},handlers or self.bindings(),authorize=callback)

    def test_registry_target_change_after_yes_fails_closed(self):
        projects=natta.load_registry(self.registry);calls=iter([projects,[replace(projects[0],name='Changed name'),projects[1]]])
        handlers=self.bindings()
        result=self.dispatch(self.route(),lambda route,project:True,loader=lambda:next(calls),handlers=handlers)
        self.assertEqual(result.error,'authorization_target_changed')
        self.assertFalse(result.execution_started)
        self.assertEqual(sum(h.call_count for h in handlers.values()),0)
        gate.runtime_effects.capture.assert_not_called()

    def test_handler_change_after_yes_fails_closed(self):
        handlers=self.bindings()
        def approve(route,project):
            handlers['build']=handlers['test']
            return True
        result=self.dispatch(self.route(),approve,handlers=handlers)
        self.assertEqual(result.error,'authorization_target_changed')
        self.assertFalse(result.execution_started)

    def test_forbidden_policy_never_prompts(self):
        route=self.route()
        route=replace(route,execution_policy=replace(route.execution_policy,authorization=policy.Authorization.FORBIDDEN))
        result=self.dispatch(route,lambda *args:self.fail('prompt'))
        self.assertFalse(result.execution_started)

    def test_effect_violation_overrides_approved_handler_success(self):
        failed=runtime_effects.VerificationResult(performed=True,passed=False,
            observed_effects=frozenset({policy.Effect.PROJECT_WRITE}),violations=frozenset({policy.Effect.PROJECT_WRITE}))
        with patch.object(runtime_effects,'compare',return_value=failed):
            code,out,_,_,_=self.invoke()
        self.assertEqual(code,1)
        self.assertIn('effect_violation',out)
        self.assertIn('execution succeeded: no',out)

    def test_approved_commit_keeps_default_arguments_and_commit_verifier(self):
        code,out,handlers,_,capture=self.invoke('Commit Example','commit','yes\n')
        self.assertEqual(code,0)
        self.assertIn('commit  Example App',out)
        self.assertEqual(handlers['commit'].call_count,1)
        self.assertEqual(handlers['commit'].call_args.args[2],())
        self.assertEqual(capture.call_count,2)
        gate.runtime_effects.capture.assert_not_called()

    def test_policy_change_after_yes_cannot_override_validation(self):
        route=self.route()
        def approve(route,project):
            return True
        with patch.object(policy,'evaluate',side_effect=[route.execution_policy,
                replace(route.execution_policy,eligible=False,reason='forbidden_policy')]):
            result=self.dispatch(route,approve)
        self.assertFalse(result.execution_started)
        self.assertEqual(result.error,'authorization_target_changed')

    def test_direct_commands_never_prompt_or_route(self):
        with patch.object(authorization,'request_approval',side_effect=AssertionError('prompt')), \
             patch.object(natta,'prepare_route',side_effect=AssertionError('Luna')), \
             patch.object(natta,'handle_workflow',return_value=gate.HandlerOutcome(0)), \
             patch.object(natta.commits,'create') as create, \
             patch.object(natta.commits,'render',return_value='fixture commit'), \
             contextlib.redirect_stdout(io.StringIO()):
            create.return_value.exit_code=0
            for args in (['build','example'],['test','example'],['verify','sample','--level','2'],['commit','example']):
                self.assertEqual(natta.main(['--registry',str(self.registry),*args]),0)
