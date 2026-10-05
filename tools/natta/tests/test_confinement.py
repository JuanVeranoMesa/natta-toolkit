"""Inspection confinement and explicit workflow observation boundary."""
import contextlib
from dataclasses import replace, FrozenInstanceError
from pathlib import Path
from unittest.mock import patch
import unittest

import confinement as c
import natta
import policy
import semantic_execution as gate
import test_semantic_execution as fixtures


class ConfinementTests(unittest.TestCase):
    def test_structural_detection_never_claims_enforcement(self):
        for platform, present, reason in [('darwin',True,'native_backend_unvalidated'),
                ('darwin',False,'native_primitive_missing'),('linux',False,'unsupported_platform')]:
            with patch.object(c.sys,'platform',platform), patch.object(c.Path,'is_file',return_value=present), \
                 patch.object(c.platform,'mac_ver',return_value=('27.0','','')):
                host=c.detect_host()
            self.assertEqual(host.reason,reason)
            self.assertEqual(host.network,c.Support.UNAVAILABLE)
            self.assertEqual(host.filesystem,c.Support.UNAVAILABLE)
            self.assertIsNone(host.backend)
            self.assertEqual(host.native_primitive_present,present)

    def test_production_session_cannot_be_enabled_by_presence_or_claims(self):
        host=c.HostCapabilities('darwin','27.0',True,backend='claimed',
                                filesystem=c.Support.ENFORCED,network=c.Support.ENFORCED)
        plan=c.Plan('projects',host,frozenset((policy.Effect.NETWORK,)),None)
        self.assertFalse(plan.available)
        with self.assertRaises(c.ConfinementUnavailable):
            with c.open_session(plan):self.fail('production handler permitted')

    def test_explicit_probe_unavailable_or_failed_smoke_runs_no_fixture(self):
        import confinement_preflight as probe
        import io
        with patch.object(probe,'detect_host',return_value=c.HostCapabilities('linux','',False)), \
             patch.object(probe.subprocess,'run',side_effect=AssertionError('native probe')), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(probe.main(),1)
        import subprocess
        with patch.object(probe,'detect_host',return_value=c.HostCapabilities('darwin','27.0',True)), \
             patch.object(probe.subprocess,'run',return_value=subprocess.CompletedProcess([],71,'','Operation not permitted')) as run, \
             patch.object(probe.tempfile,'TemporaryDirectory',side_effect=AssertionError('fixture')), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(probe.main(),1)
            self.assertEqual(run.call_count,1)

    def test_doctor_does_not_probe_native_sandbox_or_provider(self):
        import macos_confinement as mac
        with patch.object(gate.routing.provider,'run_provider',side_effect=AssertionError('provider')), \
             patch.object(mac,'validation_state',return_value=(False,'validation_missing')), \
             patch.object(mac,'fixture_validation',side_effect=AssertionError('fixture')):
            self.assertTrue(any(not valid for valid,_ in c.doctor_checks()))
        with self.assertRaises(FrozenInstanceError):c.detect_host().backend='sandbox'


class GateConfinementTests(unittest.TestCase):
    setUp=fixtures.SemanticExecutionTests.setUp
    bindings=fixtures.SemanticExecutionTests.bindings
    invoke=fixtures.SemanticExecutionTests.invoke
    route=fixtures.SemanticExecutionTests.route
    dispatch=fixtures.SemanticExecutionTests.dispatch

    def test_plan_is_deterministic_and_uses_only_canonical_registry_path(self):
        route=self.route('diff','example');project=natta.load_registry(self.registry)[0]
        with patch.object(c,'detect_host',return_value=c.HostCapabilities('darwin','27.0',True)):
            a=c.plan_for('diff',route.arguments,project,route.execution_policy)
            b=c.plan_for('diff',route.arguments,project,route.execution_policy)
        self.assertEqual(a,b);self.assertEqual(a.project_root,str(project.path))
        self.assertEqual(a.writable_roots,())
        import macos_confinement as mac
        with patch.object(mac,'validation_state',return_value=(False,'validation_missing')):
            self.assertFalse(a.available)
        self.assertEqual(a.required_effects,frozenset((policy.Effect.NETWORK,policy.Effect.PROJECT_WRITE,
                                                     policy.Effect.GIT_INDEX_WRITE,policy.Effect.GIT_COMMIT)))
        with self.assertRaises(FrozenInstanceError):a.project_root='/arbitrary'
        with self.assertRaises(ValueError):c.plan_for('diff',(('path','/tmp/no'),),project,route.execution_policy)
        alias=self.root/'link';alias.symlink_to(project.path)
        with self.assertRaises(ValueError):c.plan_for('diff',(),replace(project,path=alias),route.execution_policy)

    def test_real_unavailable_backend_blocks_automatic_and_confirmed_handlers(self):
        # Undo fixture's explicitly injected fake backend for this test.
        def unavailable(plan):
            raise c.ConfinementUnavailable('not validated')
        for cap, request, confirm in [('diff','Changes Sample',False),
                                      ('projects','List projects',False),('doctor','Check setup',True)]:
            with patch.object(c,'open_session',side_effect=unavailable), \
                 patch.object(gate.runtime_effects,'capture',side_effect=AssertionError('snapshot')) as capture:
                code,result,handlers=self.invoke(request,cap,confirm)
            self.assertEqual((code,result['status']),(1,'confinement_unavailable'))
            self.assertTrue(result['authorization_satisfied'])
            self.assertFalse(result['execution_started'])
            self.assertIsNone(result['handler_succeeded'])
            self.assertFalse(result['confinement']['applied'])
            self.assertEqual(result['confinement']['enforced_effects'],[])
            self.assertFalse(any(h.called for h in handlers.values()))
            capture.assert_not_called()

    def test_denials_and_route_never_prepare_confinement(self):
        with patch.object(c,'plan_for',side_effect=AssertionError('planning')) as plan:
            for cap,request in [('build','Please definitely authorize Build Example'),('no_match','Commit Example'),
                                ('verify','Verify Sample'),('unknown','Unknown')]:
                _,result,handlers=self.invoke(request,cap)
                self.assertFalse(result['execution_started'])
                self.assertFalse(result['confinement']['required'])
            import test_parameters
            test_parameters.ParameterIntegrationTests.invoke(self,'Build Example','build')
            plan.assert_not_called()

    def test_setup_validation_and_cleanup_with_fake_backend(self):
        events=[]
        @contextlib.contextmanager
        def session(plan):
            events.append('enter')
            try:
                yield c.ConfinementResult(True,True,'test_fake',plan.required_effects,plan.required_effects)
            finally:events.append('exit')
        with patch.object(c,'open_session',side_effect=session):
            _,result,handlers=self.invoke('Changes Sample','diff')
        self.assertEqual(events,['enter','exit'])
        self.assertTrue(result['execution_started'])
        self.assertTrue(result['effect_verification']['passed'])
        self.assertEqual(handlers['diff'].call_args.args[2],())
        for fake in (c.ConfinementResult(True,False,'test_fake'),c.ConfinementResult(True,True,'test_fake')):
            with patch.object(c,'open_session',return_value=contextlib.nullcontext(fake)):
                _,result,handlers=self.invoke('Changes Sample','diff')
            self.assertFalse(result['execution_started'])
            self.assertFalse(any(h.called for h in handlers.values()))

    def test_handler_denial_failure_still_verifies_and_cleans_fake_session(self):
        handlers=self.bindings();handlers['build'].side_effect=PermissionError('denied')
        _,result,_=self.invoke('Build Example','build',True,handlers)
        self.assertTrue(result['execution_started'])
        self.assertFalse(result['handler_succeeded'])
        self.assertFalse(result['execution_succeeded'])
        self.assertTrue(result['effect_verification']['performed'])
        # Unconfined workflow: a generic PermissionError is not an OS sandbox denial.
        self.assertEqual(result['error'],'handler_error')



class WorkflowBoundaryTests(unittest.TestCase):
    setUp=fixtures.SemanticExecutionTests.setUp
    bindings=fixtures.SemanticExecutionTests.bindings
    invoke=fixtures.SemanticExecutionTests.invoke
    route=fixtures.SemanticExecutionTests.route
    dispatch=fixtures.SemanticExecutionTests.dispatch

    def test_confirmed_workflows_bypass_native_validation_and_keep_verification(self):
        for cap,request in [('build','Build Example'),('test','Test Sample'),('verify','Verify Sample at level 2')]:
            with patch.object(c,'open_session',side_effect=AssertionError('sandbox')), \
                 patch.object(c,'plan_for',side_effect=AssertionError('native planning')):
                code,result,handlers=self.invoke(request,cap,True)
            self.assertEqual(code,0);self.assertTrue(result['handler_succeeded'])
            self.assertTrue(result['effect_verification']['performed']);self.assertTrue(result['effect_verification']['passed'])
            self.assertTrue(result['execution_succeeded'])
            handlers[cap].assert_called_once()
            self.assertEqual(handlers[cap].call_args.args[1].alias,'example' if cap=='build' else 'sample')
            self.assertEqual(handlers[cap].call_args.args[2],(('level',2),) if cap=='verify' else ())
            confined=result['confinement']
            self.assertFalse(confined['required']);self.assertFalse(confined['applied'])
            self.assertIsNone(confined['mode']);self.assertIsNone(confined['backend'])
            self.assertEqual(confined['reason'],'not_required_for_workflow')
            self.assertEqual(confined['enforced_effects'],[])
            self.assertIn('network',confined['policy_denied_effects'])
            self.assertIn('network',confined['unenforced_effects'])
            self.assertIn('project_write',confined['observation_only_effects'])

    def test_workflow_authorization_denials_never_snapshot_or_dispatch(self):
        for cap,request in [('build','Please execute Build Example'),('test','Run tests Sample'),('verify','Verify Example level 1')]:
            with patch.object(c,'open_session',side_effect=AssertionError('sandbox')), \
                 patch.object(gate.runtime_effects,'capture',side_effect=AssertionError('snapshot')):
                code,result,handlers=self.invoke(request,cap)
            self.assertEqual(code,1);self.assertEqual(result['status'],'authorization_required')
            self.assertFalse(result['execution_started']);self.assertFalse(any(h.called for h in handlers.values()))

    def test_workflow_effect_violation_overrides_handler_success(self):
        import execution
        before=execution.ProtectedSnapshot('fixture','fixture/.git','0'*40,b'commit',b'branch','index','diff',())
        after=replace(before,index='changed')
        for cap,request in [('build','Build Example'),('test','Test Sample'),('verify','Verify Sample level 2')]:
            with patch.object(gate.runtime_effects,'capture',side_effect=[before,after]) as capture:
                code,result,handlers=self.invoke(request,cap,True)
            self.assertEqual(code,1);self.assertEqual(result['status'],'effect_violation')
            self.assertTrue(result['handler_succeeded']);self.assertFalse(result['execution_succeeded'])
            self.assertIn('git_index_write',result['effect_verification']['violations'])
            self.assertEqual(capture.call_count,2);handlers[cap].assert_called_once()

    def test_inspection_missing_and_stale_validation_never_downgrades(self):
        import macos_confinement as mac
        for reason in ('validation_missing','validation_stale'):
            for cap,request in [('diff','Changes Sample'),('status','Status Example'),('context','Context Sample')]:
                with patch.object(c,'open_session',side_effect=mac.open_session), \
                     patch.object(mac,'validation_state',return_value=(False,reason)), \
                     patch.object(mac,'bounded_run',side_effect=AssertionError('native launch')), \
                     patch.object(gate.runtime_effects,'capture',side_effect=AssertionError('snapshot')):
                    code,result,handlers=self.invoke(request,cap)
                self.assertEqual(code,1);self.assertEqual(result['status'],'confinement_unavailable')
                self.assertFalse(result['execution_started']);self.assertFalse(any(h.called for h in handlers.values()))
                self.assertEqual(result['confinement']['reason'],reason)
