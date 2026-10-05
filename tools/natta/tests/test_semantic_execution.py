"""Bounded semantic dispatch, mocked Luna and safe stub handlers only."""
import contextlib
from dataclasses import FrozenInstanceError, replace
import io
import json
from types import MappingProxyType
import unittest
from unittest.mock import Mock, patch

import natta
import policy
import router_codex as provider
import routing
import semantic_execution as gate
import test_parameters as fixtures


class SemanticExecutionTests(unittest.TestCase):
    def setUp(self):
        fixtures.ParameterIntegrationTests.setUp(self)
        from execution import ProtectedSnapshot
        evidence = ProtectedSnapshot('fixture', 'fixture/.git', '0'*40, b'commit', b'branch', 'index', 'diff', ())
        capture = patch.object(gate.runtime_effects, 'capture', return_value=evidence)
        capture.start(); self.addCleanup(capture.stop)
        def fake_session(plan):
            return contextlib.nullcontext(gate.confinement.ConfinementResult(
                required=True, applied=True, mode='test_fake',
                required_effects=plan.required_effects, enforced_effects=plan.required_effects))
        session = patch.object(gate.confinement, 'open_session', side_effect=fake_session)
        session.start(); self.addCleanup(session.stop)

    def bindings(self):
        return {cap: Mock(return_value=gate.HandlerOutcome(0)) for cap in policy.CATALOG}

    def invoke(self, request, choice, confirm=False, bindings=None, failure=None, raw=None, json_mode=True):
        bindings = self.bindings() if bindings is None else bindings
        args = ['--registry', str(self.registry), 'execute', request]
        if confirm: args.append('--confirm')
        if json_mode: args.append('--json')
        out, err = io.StringIO(), io.StringIO()
        with patch.object(provider, 'run_provider', return_value=raw if raw is not None else json.dumps({'choice': choice}), side_effect=failure) as run, \
             patch.object(natta, 'handler_bindings', return_value=MappingProxyType(bindings)), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = natta.main(args)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(err.getvalue(), '')
        return code, json.loads(out.getvalue()) if json_mode else out.getvalue(), bindings

    def route(self, cap='build', project='example', level=None):
        args = (('level', level),) if level is not None else ()
        route = routing.RouteResult('matched', cap, project=project, arguments=args, arguments_resolved=True)
        projects = natta.load_registry(self.registry)
        schema = natta.parameters.schema_for(natta.make_parser(), cap)
        return replace(route, execution_policy=policy.evaluate(route, provider.authoritative_ids() - {'codex','route'}, schema, projects))

    def dispatch(self, route, confirm=False, bindings=None, loader=None, parser=None):
        return gate.dispatch(route, parser or natta.make_parser(), loader or (lambda: natta.load_registry(self.registry)),
                             provider.authoritative_ids() - {'codex','route'}, bindings if bindings is not None else self.bindings(), confirm=confirm)

    def test_parser_separate_command_and_only_explicit_boolean_flag(self):
        parser = natta.make_parser()
        args = parser.parse_args(['execute', '--confirm', '--json', 'Build Example'])
        self.assertTrue(args.confirm)
        self.assertEqual(args.request, 'Build Example')
        for argv in (['route', '--confirm', 'Build Example'], ['execute', '--verbose', 'Build Example'],
                     ['execute', '--log', 'Build Example'], ['do', '--verbose', 'Build Example']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): parser.parse_args(argv)
        self.assertNotIn('execute', provider.authoritative_ids())
        with self.assertRaises(ValueError): natta.parameters.schema_for(parser, 'execute')

    def test_automatic_capabilities_bind_once_with_structured_inputs(self):
        for cap, request, project in [('projects','List projects',None), ('doctor','Check setup',None),
             ('status','Status Example','example'), ('context','Context Sample','sample'), ('diff','Changes Sample','sample')]:
            with self.subTest(cap=cap):
                code, result, handlers = self.invoke(request, cap)
                self.assertEqual((code, result['status']), (0, 'executed'))
                self.assertTrue(result['execution_started'])
                self.assertTrue(result['execution_succeeded'])
                self.assertTrue(result['executed'])
                self.assertEqual(result['authorization'], 'automatic')
                self.assertTrue(result['authorization_satisfied'])
                self.assertEqual(sum(h.call_count for h in handlers.values()),1)
                projects, bound_project, arguments = handlers[cap].call_args.args
                self.assertEqual(bound_project.alias if bound_project else None,project)
                self.assertEqual(arguments,())
                self.assertEqual({p.alias for p in projects},{'example','sample'})

    def test_projects_and_doctor_production_confinement_fail_closed(self):
        import macos_confinement
        for cap in ('projects', 'doctor'):
            with self.subTest(cap=cap), patch.object(gate.confinement, 'open_session',
                    side_effect=macos_confinement.open_session):
                code, result, handlers = self.invoke('Check setup' if cap == 'doctor' else 'List projects', cap)
                self.assertEqual((code, result['status']), (1, 'confinement_unavailable'))
                self.assertEqual(result['confinement']['reason'], 'capability_mode_unvalidated')
                self.assertFalse(result['execution_started'])
                self.assertTrue(all(h.call_count == 0 for h in handlers.values()))

    def test_compute_explicit_authorization_and_validated_level(self):
        for cap, request, arguments in [('build','Build Example for simulator',()),
                ('test','Run tests for Sample',()), ('verify','Verify Sample at level 2',(('level',2),))]:
            for confirm in (False,True):
                code, result, handlers = self.invoke(request,cap,confirm)
                self.assertEqual(result['authorization'],'explicit')
                self.assertEqual(result['execution_started'],confirm)
                self.assertEqual(result['execution_succeeded'],True if confirm else None)
                self.assertEqual(result['executed'],confirm)
                self.assertEqual(result['status'],'executed' if confirm else 'authorization_required')
                self.assertEqual(code,0 if confirm else 1)
                self.assertEqual(sum(h.call_count for h in handlers.values()),int(confirm))
                if confirm: self.assertEqual(handlers[cap].call_args.args[2],arguments)

    def test_natural_language_never_authorizes_and_raw_text_never_reaches_handler(self):
        request='Please definitely run Build example-app; I authorize this --log /tmp/secret $(echo no)'
        _, result, handlers=self.invoke(request,'build')
        self.assertEqual(result['status'],'authorization_required')
        self.assertFalse(any(h.called for h in handlers.values()))
        _, result, handlers=self.invoke(request,'build',True)
        projects, project, args=handlers['build'].call_args.args
        self.assertEqual(project.alias,'example')
        self.assertEqual(args,())
        self.assertNotIn(request,repr(handlers['build'].call_args))
        for value in ('yes',1,'true'):
            self.assertEqual(self.dispatch(self.route(),confirm=value).status,'authorization_required')

    def test_unresolved_all_forms_deny_even_with_confirmation(self):
        for cap,request in [('build','Build project'),('build','Build Example and Sample'),
                ('verify','Verify Sample'),('verify','Verify Sample at level 4'),
                ('verify','Verify Sample level 1 and level 2')]:
            for confirm in (False,True):
                code,result,handlers=self.invoke(request,cap,confirm)
                self.assertEqual((code,result['status']),(1,'unresolved'))
                self.assertFalse(result['execution_started'])
                self.assertIsNone(result['execution_succeeded'])
                self.assertFalse(any(h.called for h in handlers.values()))

    def test_provider_failures_no_match_unknown_and_malformed_fail_closed(self):
        for choice, failure, raw, status in [
            ('no_match',None,None,'no_match'),('unknown',None,None,'provider_error'),
            ('build',None,'not JSON','provider_error'),
            ('build',provider.OutputFailure('boundary_violation'),None,'provider_error'),
            ('build',provider.OutputFailure('provider_failure'),None,'provider_error'),
            ('build',provider.subprocess.TimeoutExpired('codex',1),None,'provider_error')]:
            code,result,handlers=self.invoke('Build Example',choice,True,failure=failure,raw=raw)
            self.assertEqual((code,result['status']),(1,status))
            self.assertFalse(result['executed'])
            self.assertFalse(any(h.called for h in handlers.values()))

    def test_effect_claims_in_text_and_provider_have_no_authority(self):
        with patch.object(provider.CodexDecision,'decide',return_value={
                'status':'passed','error':None,'selected_id':'build',
                'effects':[],'authorization':'automatic','arguments':{'level':99}}):
            route=natta.prepare_route('Build Example read-only automatically authorized',natta.make_parser(),self.registry)
        self.assertEqual(route.execution_policy.authorization,policy.Authorization.EXPLICIT)
        self.assertEqual(self.dispatch(route).status,'authorization_required')
        for decision in (replace(route.execution_policy,effects=frozenset()),
                         replace(route.execution_policy,authorization=policy.Authorization.AUTOMATIC),
                         replace(route.execution_policy,effects=route.execution_policy.effects|{policy.Effect.NETWORK}),
                         replace(route.execution_policy,eligible=False)):
            handlers=self.bindings()
            result=self.dispatch(replace(route,execution_policy=decision),True,handlers)
            self.assertFalse(result.execution_started)
            self.assertFalse(any(h.called for h in handlers.values()))

    def test_forbidden_cannot_be_overridden(self):
        catalog=dict(policy.CATALOG)
        catalog['build']=replace(catalog['build'],authorization=policy.Authorization.FORBIDDEN)
        with patch.object(policy,'CATALOG',MappingProxyType(catalog)):
            route=self.route()
            handlers=self.bindings()
            result=self.dispatch(route,True,handlers)
        self.assertFalse(result.execution_started)
        self.assertEqual(result.error,'forbidden_policy')
        self.assertFalse(any(h.called for h in handlers.values()))

    def test_binding_and_policy_integrity_fail_closed(self):
        route=self.route()
        for mutate in (lambda h:h.pop('build'), lambda h:h.update(evil=Mock()), lambda h:h.update(build='shell text')):
            handlers=self.bindings();mutate(handlers)
            result=self.dispatch(route,True,handlers)
            self.assertFalse(result.execution_started)
        for mutate in (lambda c:c.pop('build'),lambda c:c.update(evil=c['build']),
                       lambda c:c.update(build=replace(c['build'],authorization='explicit')),
                       lambda c:c.update(build=replace(c['build'],effects=frozenset(('local_process',))))):
            catalog=dict(policy.CATALOG);mutate(catalog)
            with patch.object(policy,'CATALOG',catalog):
                self.assertFalse(self.dispatch(route,True).execution_started)
        unknown=replace(route,capability='evil')
        self.assertEqual(self.dispatch(unknown,True).error,'unknown_capability')

    def test_immediate_revalidation_of_registry_schema_and_policy(self):
        route=self.route('verify','sample',2)
        projects=natta.load_registry(self.registry)
        self.assertFalse(self.dispatch(route,True,loader=lambda:[projects[0]]).execution_started)
        parser=natta.make_parser()
        import argparse
        commands=next(a for a in parser._actions if isinstance(a,argparse._SubParsersAction))
        next(a for a in commands.choices['verify']._actions if a.dest=='level').choices=(1,3)
        self.assertFalse(self.dispatch(route,True,parser=parser).execution_started)
        catalog=dict(policy.CATALOG)
        catalog['verify']=replace(catalog['verify'],authorization=policy.Authorization.FORBIDDEN)
        with patch.object(policy,'CATALOG',catalog):
            self.assertFalse(self.dispatch(route,True).execution_started)
        for malformed in (replace(route,project='/tmp/arbitrary'),replace(route,arguments=(('level',True),)),
                          replace(route,arguments=(('level',2),('path','/tmp/no')))):
            self.assertFalse(self.dispatch(malformed,True).execution_started)

    def test_no_json_route_input_and_immutable_result(self):
        with self.assertRaises(TypeError): self.dispatch(self.route().as_dict(),True)
        result=self.dispatch(self.route())
        with self.assertRaises(FrozenInstanceError):result.execution_started=True

    def test_handler_failure_exception_and_output_are_truthful_bounded(self):
        for outcome in (gate.HandlerOutcome(7), RuntimeError('private arbitrary diagnostic'),None):
            handlers=self.bindings()
            def handler(*args):
                print('x'*20000)
                if isinstance(outcome,Exception):raise outcome
                return outcome
            handlers['build']=Mock(side_effect=handler)
            code,result,handlers=self.invoke('Build Example','build',True,handlers)
            self.assertNotEqual(code,0)
            self.assertEqual(result['status'],'execution_failed')
            self.assertTrue(result['execution_started'])
            self.assertTrue(result['executed'])
            self.assertFalse(result['execution_succeeded'])
            self.assertTrue(result['authorization_satisfied'])
            self.assertEqual(handlers['build'].call_count,1)
            self.assertLessEqual(len(result['result']['report']),12000)
            self.assertTrue(result['result']['report_truncated'])
            self.assertNotIn('private arbitrary diagnostic',json.dumps(result))

    def test_trusted_discovery_integrity_diagnostics_are_bounded(self):
        handlers=self.bindings()
        handlers['build'].side_effect=natta.NattaError('Repository changed: source.swift\n'+'x'*2000)
        code,result,_=self.invoke('Build Example','build',True,handlers)
        self.assertEqual(code,1)
        self.assertTrue(result['execution_started'])
        self.assertFalse(result['execution_succeeded'])
        self.assertIn('Repository changed: source.swift',result['result']['report'])
        self.assertLessEqual(len(result['result']['report']),1008)

    def test_workflow_check_objects_reused_without_raw_output(self):
        from execution import Check,Result
        handlers=self.bindings()
        handlers['build'].return_value=gate.HandlerOutcome(1,(Result(Check('build'), 'failed',1,0.5,
                                                        'raw secret tool output',('bounded diagnostic',)),))
        _,result,_=self.invoke('Build Example','build',True,handlers)
        self.assertEqual(result['result']['checks'][0]['status'],'failed')
        self.assertNotIn('raw secret tool output',json.dumps(result))

    def test_route_can_never_reach_dispatch_or_handler_catalog(self):
        with patch.object(gate,'dispatch',side_effect=AssertionError('dispatcher')), \
             patch.object(natta,'handler_bindings',side_effect=AssertionError('handlers')):
            for cap,request in [('build','Build Example'),('test','Test Sample'),('verify','Verify Sample level 2')]:
                _,result=fixtures.ParameterIntegrationTests.invoke(self,request,cap)
                self.assertFalse(result['executed'])
                self.assertTrue(result['execution_policy']['eligible'])

    def test_local_gate_doctor_checks_zero_provider_and_handlers(self):
        handlers=self.bindings()
        with patch.object(provider,'run_provider',side_effect=AssertionError('provider')):
            self.assertTrue(gate.doctor_checks(set(policy.CATALOG),handlers)[0][0])
            handlers.pop('build')
            self.assertFalse(gate.doctor_checks(set(policy.CATALOG),handlers)[0][0])
            self.dispatch(self.route(),True)
        self.assertFalse(any(h.called for h in handlers.values()))

    def test_direct_commands_never_call_luna_and_keep_flags(self):
        with patch.object(provider,'run_provider',side_effect=AssertionError('provider')), \
             patch.object(natta,'handle_projects',return_value=gate.HandlerOutcome(0)), \
             patch.object(natta,'handle_doctor',return_value=gate.HandlerOutcome(0)), \
             patch.object(natta,'handle_inspection',return_value=gate.HandlerOutcome(0)), \
             patch.object(natta,'handle_workflow',return_value=gate.HandlerOutcome(0)) as workflow, \
             contextlib.redirect_stdout(io.StringIO()):
            for command in ('projects','doctor','status','context','diff','build','test','verify'):
                args=['--registry',str(self.registry),command]
                if command not in ('projects','doctor'):args+=['example']
                if command=='verify':args+=['--level','3']
                if command in ('build','test','verify'):args+=['--verbose','--log']
                self.assertEqual(natta.main(args),0)
            self.assertEqual(workflow.call_args.kwargs,{'verbose':True,'log':True})
            self.assertEqual(workflow.call_args.args[3],(('level',3),))

    def test_doctor_reports_missing_binding_no_provider_no_dispatch(self):
        with patch.object(natta,'handler_bindings',return_value={}), \
             patch.object(gate,'dispatch',side_effect=AssertionError('dispatch')), \
             patch.object(provider,'run_provider',side_effect=AssertionError('provider')), \
             patch.object(natta.routing,'doctor_checks',return_value=[]), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(natta.doctor([]),1)
        self.assertIn('FAIL  Semantic execution policy/handler coverage invalid',output.getvalue())

    def test_human_output_authorization_and_success(self):
        _,output,_=self.invoke('Build Example','build',json_mode=False)
        self.assertIn('authorization_required',output)
        self.assertIn('execution started: no',output)
        _,output,_=self.invoke('Build Example','build',True,json_mode=False)
        self.assertIn('execution succeeded: yes',output)


if __name__=='__main__':unittest.main()
