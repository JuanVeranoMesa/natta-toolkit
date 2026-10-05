"""Inspection backend contracts. Native/provider calls are mocked."""
import contextlib
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import patch, Mock
import unittest

import confinement as c
import macos_confinement as mac
import macos_worker as worker
import natta
import policy
import semantic_execution as gate
import test_semantic_execution as fixtures


class MacOSBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.state=self.root/'validation'
        patcher=patch.object(mac,'state_root',return_value=self.state);patcher.start();self.addCleanup(patcher.stop)
        self.fingerprint={'schema':1,'macos':'27.0','build':'fixture-build','implementation':'hash','profile':'profile'}
        patcher=patch.object(mac,'identity',return_value=self.fingerprint.copy());patcher.start();self.addCleanup(patcher.stop)
        self.project=natta.Project('sample',(),'Sample',self.root/'project','generic-git',{}, {})
        self.project.path.mkdir()
        self.decision=policy.PolicyResult(True,policy.Authorization.AUTOMATIC,policy.PROJECT_INSPECTION,policy.CapabilityType.ATOMIC,None)
        self.plan=c.plan_for('diff',(),self.project,self.decision)

    def test_deterministic_deny_default_profile_and_path_escaping(self):
        runtime=self.root/'runtime "quoted"';runtime.mkdir(mode=0o700)
        text=mac.profile('inspection',runtime)
        self.assertEqual(text,mac.profile('inspection',runtime))
        self.assertIn('(deny default)',text);self.assertIn('(deny network*)',text)
        self.assertIn('(allow file-read*)',text);self.assertIn('(allow process*)',text)
        self.assertIn(json.dumps(str(runtime)),text)
        self.assertNotIn(str(self.project.path),text)
        self.assertNotIn(str(Path.home()),text)
        self.assertNotIn('allow default',text)
        link=self.root/'link';link.symlink_to(runtime)
        with self.assertRaises(ValueError):mac.profile('inspection',link)
        with self.assertRaises(ValueError):mac.profile('xcode',runtime)

    def test_validation_state_modes_permissions_and_staleness(self):
        self.assertEqual(mac.validation_state(),(False,'validation_missing'))
        mac.save_record('inspection',self.fingerprint)
        self.assertTrue(mac.validation_state()[0])
        self.assertEqual((self.state/'inspection.json').stat().st_mode & 0o777,0o600)
        self.assertEqual(self.state.stat().st_mode & 0o777,0o700)
        for key in ('build','macos','implementation','profile','schema'):
            with patch.object(mac,'identity',return_value={**self.fingerprint,key:'changed'}):
                self.assertEqual(mac.validation_state()[1],'validation_stale')
        (self.state/'inspection.json').chmod(0o644)
        self.assertEqual(mac.validation_state()[1],'validation_invalid')

    def test_symlink_record_and_unvalidated_modes_never_enable(self):
        self.state.mkdir(mode=0o700)
        target=self.root/'outside';target.write_text('{}')
        (self.state/'inspection.json').symlink_to(target)
        self.assertEqual(mac.validation_state()[1],'validation_invalid')
        with self.assertRaises(ValueError):mac.save_record('inspection',self.fingerprint)
        for cap in ('projects','doctor','build','test','verify'):
            with self.assertRaises(c.ConfinementUnavailable):mac.mode_for(cap)
        for cap in ('status','context','diff'):self.assertEqual(mac.mode_for(cap),'inspection')

    def test_private_session_cleanup_and_worker_binding(self):
        mac.save_record('inspection',self.fingerprint)
        with mac.Session(self.plan,self.project) as session:
            path=session.profile_path;runtime=session.runtime
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            mac.owned_runtime(runtime)
            self.assertIn(str(runtime),path.read_text());self.assertNotIn(str(self.project.path),path.read_text())
            final={'event':'result','exit_code':0,'checks':[],'report':'Diff passed','report_truncated':False,'error':None}
            with patch.object(mac,'bounded_run',return_value=(0,json.dumps({'event':'started'})+'\n'+json.dumps(final))) as run:
                result=session.invoke((self.project,),self.project,())
            self.assertTrue(result.started);self.assertTrue(session.result.applied)
            self.assertTrue(session.result.validated)
            argv,payload,env=run.call_args.args
            self.assertEqual(argv[0],'/usr/bin/sandbox-exec')
            self.assertEqual(set(json.loads(payload)),{'capability','project','arguments','projects'})
            self.assertEqual(env['TMPDIR'],str(runtime));self.assertNotIn('CFFIXED_USER_HOME',env)
        self.assertFalse(path.exists());self.assertFalse(runtime.exists())

    def test_setup_and_started_worker_failures_truthful(self):
        mac.save_record('inspection',self.fingerprint)
        for code,raw,started in [(71,'sandbox_apply: Operation not permitted',False),
                                 (1,json.dumps({'event':'started'}),True)]:
            with mac.Session(self.plan,self.project) as session,patch.object(mac,'bounded_run',return_value=(code,raw)):
                invocation=session.invoke((self.project,),self.project,())
            self.assertEqual(invocation.started,started);self.assertEqual(invocation.outcome.exit_code,1)

    def test_failed_validation_removes_previous_success_and_never_builds_implicitly(self):
        mac.save_record('inspection',self.fingerprint)
        with patch.object(mac,'fixture_validation',side_effect=ValueError('fixture blocked')), \
             patch.object(mac,'candidate_run',side_effect=AssertionError('handler')), \
             patch.object(gate.routing.provider,'run_provider',side_effect=AssertionError('provider')):
            code,result=mac.validate()
        self.assertEqual(code,1);self.assertFalse(mac.validation_state()[0])
        self.assertFalse(result['inspection_validated'])

    def test_explicit_validation_records_only_complete_success(self):
        with patch.object(mac,'fixture_validation',return_value=['fixtures']) as fixture, \
             patch.object(gate.routing.provider,'run_provider',side_effect=AssertionError('provider')):
            code,result=mac.validate()
        fixture.assert_called_once_with('inspection')
        self.assertEqual(code,0);self.assertTrue(mac.validation_state()[0])
        self.assertEqual(result['mode'],'inspection')

    def test_worker_rejects_unbounded_input_and_uses_only_known_handler(self):
        payload={'capability':'diff','project':'sample','arguments':{},'projects':mac.serialize_projects((self.project,))}
        handler=Mock(return_value=gate.HandlerOutcome(0))
        with patch.object(worker.sys,'stdin',io.StringIO(json.dumps(payload))), \
             patch.object(natta,'handler_bindings',return_value={'diff':handler}),contextlib.redirect_stdout(io.StringIO()):
            worker.main()
        self.assertEqual(handler.call_args.args[2],())
        for changes in ({'capability':'build'},{'capability':'delete_everything'},{'verbose':True},
                        {'arguments':{'path':'/tmp/arbitrary'}},{'request':'Build Example'}):
            with patch.object(worker.sys,'stdin',io.StringIO(json.dumps({**payload,**changes}))),self.assertRaises(ValueError):worker.main()

    def test_cli_and_doctor_zero_provider_no_native_validation(self):
        natta.make_parser().parse_args(['confinement','validate'])
        for extra in (['--xcode','example'],['--verbose']):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                natta.make_parser().parse_args(['confinement','validate',*extra])
        with patch.object(mac,'validate',return_value=(0,{'status':'validated'})) as validate, \
             patch.object(gate.routing.provider,'run_provider',side_effect=AssertionError('provider')),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(natta.main(['confinement','validate']),0)
            validate.assert_called_once_with()
        with patch.object(mac,'validation_state',return_value=(True,None)) as state, \
             patch.object(mac,'fixture_validation',side_effect=AssertionError('fixture')), \
             patch.object(mac,'candidate_run',side_effect=AssertionError('handler')):
            checks=c.doctor_checks((self.project,))
        state.assert_called_once_with('inspection')
        self.assertTrue(any(valid and 'deferred' in text for valid,text in checks))

    def test_shipping_compatibility_manifest_pins_exact_current_sources(self):
        manifest=json.loads((mac.SOURCE/'inspection_compatibility.json').read_text())
        actual=hashlib.sha256(b''.join((mac.SOURCE/f).read_bytes() for f in mac.FILES)).hexdigest()
        self.assertEqual(manifest['current_implementation'],actual)
        self.assertNotIn(actual,manifest['previous_implementations'])

    def test_legacy_inspection_records_preserve_only_audited_profile(self):
        mac.save_record('inspection',self.fingerprint)
        path=mac.record_path();record=json.loads(path.read_text())
        record.update(scope=None,xcode_validated=False);path.write_text(json.dumps(record))
        self.assertTrue(mac.validation_state()[0])
        record['xcode_validated']=True;path.write_text(json.dumps(record))
        self.assertEqual(mac.validation_state()[1],'validation_stale')
        old={**self.fingerprint,'implementation':'old'}
        new={**self.fingerprint,'implementation':'new'}
        with patch.object(Path,'read_text',return_value=json.dumps({'previous_implementations':['old'],'current_implementation':'new'})):
            self.assertTrue(mac.identity_matches('inspection',old,new))
            self.assertFalse(mac.identity_matches('inspection',old,{**new,'profile':'changed'}))
            self.assertFalse(mac.identity_matches('inspection',old,{**new,'implementation':'future'}))



class NativeGateTests(unittest.TestCase):
    setUp=fixtures.SemanticExecutionTests.setUp
    bindings=fixtures.SemanticExecutionTests.bindings
    invoke=fixtures.SemanticExecutionTests.invoke
    route=fixtures.SemanticExecutionTests.route
    dispatch=fixtures.SemanticExecutionTests.dispatch

    def test_native_session_dispatches_outside_parent_and_preserves_verification(self):
        class FakeWorker(c.BackendSession):
            result=c.ConfinementResult(True,False,'inspection',backend='macos-sandbox-exec',validated=True)
            def invoke(self,projects,project,arguments):
                self.result=replace(self.result,applied=True,enforced_effects=c.DENIABLE_EFFECTS)
                return c.Invocation(True,gate.HandlerOutcome(0),'inspection report')
        for cap,request in [('diff','Changes Sample'),('status','Status Sample'),('context','Context Sample')]:
            session=FakeWorker()
            with patch.object(c,'open_session',return_value=contextlib.nullcontext(session)):
                code,result,handlers=self.invoke(request,cap,True)
            self.assertEqual(code,0)
            self.assertTrue(result['execution_started']);self.assertTrue(result['confinement']['applied'])
            self.assertTrue(result['effect_verification']['passed'])
            self.assertFalse(any(h.called for h in handlers.values()))

    def test_kernel_setup_failure_is_not_handler_start(self):
        class FakeWorker(c.BackendSession):
            result=c.ConfinementResult(True,False,'inspection',validated=True)
            def invoke(self,*args):return c.Invocation(False,gate.HandlerOutcome(1),error='confinement_setup_failed')
        with patch.object(c,'open_session',return_value=contextlib.nullcontext(FakeWorker())):
            code,result,handlers=self.invoke('Changes Sample','diff')
        self.assertEqual((code,result['status']),(1,'confinement_setup_failed'))
        self.assertFalse(result['execution_started'])
        self.assertFalse(any(h.called for h in handlers.values()))

    def test_native_failed_handler_still_gets_post_snapshot_and_violation_precedence(self):
        import execution
        class FakeWorker(c.BackendSession):
            result=c.ConfinementResult(True,True,'inspection',enforced_effects=c.DENIABLE_EFFECTS,validated=True)
            def invoke(self,*args):return c.Invocation(True,gate.HandlerOutcome(1),error='handler_error')
        before=execution.ProtectedSnapshot('fixture','fixture/.git','0'*40,b'commit',b'branch','index','diff',())
        after=replace(before,index='changed')
        with patch.object(c,'open_session',return_value=contextlib.nullcontext(FakeWorker())), \
             patch.object(gate.runtime_effects,'capture',side_effect=[before,after]) as capture:
            code,result,_=self.invoke('Changes Sample','diff')
        self.assertEqual(code,1);self.assertEqual(result['status'],'effect_violation')
        self.assertTrue(result['execution_started']);self.assertFalse(result['handler_succeeded'])
        self.assertEqual(capture.call_count,2)


if __name__=='__main__':unittest.main()
