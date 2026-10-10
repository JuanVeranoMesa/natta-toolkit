"""Local temporary Git evidence and mocked semantic execution, no app/model calls."""
import contextlib
from dataclasses import replace, FrozenInstanceError
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import execution
import natta
import policy
import runtime_effects as effects
import semantic_execution as gate
import test_semantic_execution as fixtures


class ProtectedSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.repo=Path(self.tmp.name).resolve()
        self.git('init','-b','main')
        (self.repo/'tracked').write_text('initial\n')
        self.git('add','tracked');self.git('commit','-m','initial')

    def git(self,*args):
        return subprocess.check_output(['git','--no-optional-locks','-c','user.name=Fixture',
            '-c','user.email=fixture@example.test','-C',str(self.repo),*args],stderr=subprocess.DEVNULL)

    def capture(self,before=None):
        return execution.snapshot(self.repo,protected=True,baseline=before.baseline if before else None)

    def compare(self,before,allowed=frozenset()):
        return effects.compare(before,self.capture(before),allowed)

    def test_clean_and_preexisting_dirty_unchanged_pass(self):
        self.assertTrue(self.compare(self.capture()).passed)
        (self.repo/'tracked').write_text('dirty\n')
        (self.repo/'untracked').write_text('private text')
        before=self.capture()
        with patch.object(Path,'read_bytes',side_effect=AssertionError('no file content reads')):
            self.assertTrue(self.compare(before).passed)
        self.assertEqual(len(json.dumps(effects.compare(before,before,()).as_dict())) < 500,True)

    def test_new_and_already_dirty_tracked_modifications(self):
        for text in ('new\n','dirtier\n'):
            before=self.capture();(self.repo/'tracked').write_text(text)
            result=self.compare(before)
            self.assertEqual(result.violations,frozenset((policy.Effect.PROJECT_WRITE,)))

    def test_untracked_add_remove_and_content_not_observed(self):
        before=self.capture();p=self.repo/'untracked';p.write_text('one')
        self.assertIn(policy.Effect.PROJECT_WRITE,self.compare(before).violations)
        before=self.capture();p.write_text('two')
        self.assertTrue(self.compare(before).passed) # documented path set, not contents
        p.unlink();self.assertIn(policy.Effect.PROJECT_WRITE,self.compare(before).violations)

    def test_index_change_and_fixed_baseline_avoids_staging_false_positive(self):
        (self.repo/'tracked').write_text('dirty\n')
        before=self.capture();self.git('add','tracked')
        result=self.compare(before)
        self.assertIn(policy.Effect.GIT_INDEX_WRITE,result.violations)
        self.assertNotIn(policy.Effect.PROJECT_WRITE,result.observed_effects)

    def test_commit_branch_and_detached_head_movements(self):
        before=self.capture();self.git('commit','--allow-empty','-m','second')
        self.assertIn(policy.Effect.GIT_COMMIT,self.compare(before).violations)
        before=self.capture();self.git('switch','-c','other')
        self.assertIn(policy.Effect.GIT_COMMIT,self.compare(before).violations)
        before=self.capture();self.git('checkout','--detach')
        self.assertIn(policy.Effect.GIT_COMMIT,self.compare(before).violations)

    def test_allowed_effects_comparison_and_no_rollback(self):
        before=self.capture();(self.repo/'tracked').write_text('changed\n')
        after=self.capture(before)
        self.assertTrue(effects.compare(before,after,{policy.Effect.PROJECT_WRITE}).passed)
        self.assertFalse(effects.compare(before,after,set()).passed)
        self.assertEqual((self.repo/'tracked').read_text(),'changed\n')
        with self.assertRaises(FrozenInstanceError):after.index='other'
        with self.assertRaises(ValueError):effects.compare(before,after,{'project_write'})

    def test_unborn_repository_and_subdirectory_rejection(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        subprocess.check_call(['git','init','-q',temp.name])
        before=execution.snapshot(temp.name,protected=True)
        after=execution.snapshot(temp.name,protected=True,baseline=before.baseline)
        self.assertTrue(effects.compare(before,after,()).passed)
        (self.repo/'sub').mkdir()
        with self.assertRaises(execution.NattaError):execution.snapshot(self.repo/'sub',protected=True)

    def test_external_filter_and_hidden_index_entries_fail_closed(self):
        self.git('config','filter.bad.clean','arbitrary command')
        (self.repo/'.git/info').mkdir(exist_ok=True);(self.repo/'.git/info/attributes').write_text('tracked filter=bad\n')
        with self.assertRaises(execution.ExternalFilterConfigured):self.capture()
        self.git('config','--unset','filter.bad.clean');(self.repo/'.git/info/attributes').unlink()
        for flag,undo in (('--assume-unchanged','--no-assume-unchanged'),('--skip-worktree','--no-skip-worktree')):
            self.git('update-index',flag,'tracked')
            with self.assertRaises(execution.NattaError):self.capture()
            self.git('update-index',undo,'tracked')

    def test_shared_integrity_guard_and_split_index_logical_entries(self):
        self.git('update-index','--split-index')
        before=self.capture()
        self.assertTrue(before.index_entries)
        execution.assert_integrity(self.repo,before,'unchanged')
        (self.repo/'tracked').write_text('changed\n')
        self.git('add','tracked')
        self.assertIn(policy.Effect.GIT_INDEX_WRITE,self.compare(before).violations)
        with self.assertRaises(execution.NattaError):execution.assert_integrity(self.repo,before,'changed')

    def test_ignored_paths_and_external_runtime_artifacts_outside_scope(self):
        (self.repo/'.gitignore').write_text('cache/\n');self.git('add','.gitignore');self.git('commit','-m','ignore')
        before=self.capture();(self.repo/'cache').mkdir();(self.repo/'cache/output').write_text('cache')
        self.assertTrue(self.compare(before).passed)
        legacy=execution.snapshot(self.repo)
        self.assertIn(b'cache/output',legacy['files'])


class RuntimeIntegrationTests(unittest.TestCase):
    setUp=fixtures.SemanticExecutionTests.setUp
    bindings=fixtures.SemanticExecutionTests.bindings
    invoke=fixtures.SemanticExecutionTests.invoke
    route=fixtures.SemanticExecutionTests.route
    dispatch=fixtures.SemanticExecutionTests.dispatch

    def evidence(self):
        return execution.ProtectedSnapshot('fixture','fixture/.git','0'*40,b'commit',b'branch','index','diff',())

    def test_all_targeted_capabilities_verify_once_before_and_after(self):
        for cap,request in [('diff','Changes Sample'),('status','Status Example'),('context','Context Sample'),
                ('build','Build Example'),('test','Test Sample'),('verify','Verify Sample level 2')]:
            with patch.object(effects,'capture',return_value=self.evidence()) as capture:
                code,result,handlers=self.invoke(request,cap,True)
            self.assertEqual(code,0)
            self.assertEqual(capture.call_count,2)
            self.assertTrue(result['handler_succeeded'])
            self.assertTrue(result['effect_verification']['performed'])
            self.assertTrue(result['effect_verification']['passed'])
            self.assertTrue(result['execution_succeeded'])
            self.assertEqual(result['effect_verification']['observed_effects'],[])
            self.assertEqual(sum(h.call_count for h in handlers.values()),1)

    def test_all_observable_violations_override_handler_success(self):
        for field,value,effect in [('project_state','changed','project_write'),('untracked',(b'new',),'project_write'),
                ('index','changed','git_index_write'),('commit',b'changed','git_commit'),('branch',b'changed','git_commit')]:
            before=self.evidence();after=replace(before,**{field:value})
            with patch.object(effects,'capture',side_effect=[before,after]):
                code,result,_=self.invoke('Changes Sample','diff')
            self.assertEqual((code,result['status']),(1,'effect_violation'))
            self.assertTrue(result['execution_started']);self.assertTrue(result['handler_succeeded'])
            self.assertFalse(result['execution_succeeded'])
            self.assertIn(effect,result['effect_verification']['violations'])
            self.assertEqual(result['result']['exit_code'],0) # handler code preserved, CLI fails

    def test_pre_snapshot_failure_denies_handler(self):
        for value in (execution.NattaError('private'),None):
            with patch.object(effects,'capture',side_effect=value if isinstance(value,Exception) else None,return_value=value) as capture:
                code,result,handlers=self.invoke('Build Example','build',True)
            self.assertEqual((code,result['status']),(1,'verification_unavailable'))
            self.assertFalse(result['execution_started']);self.assertIsNone(result['handler_succeeded'])
            self.assertFalse(any(h.called for h in handlers.values()))
            self.assertEqual(capture.call_count,1)
            self.assertEqual(result['effect_verification']['reason'],'pre_execution_verification_failed')

    def test_applied_external_filter_refusal_is_specific(self):
        with patch.object(effects,'capture',side_effect=execution.ExternalFilterConfigured()):
            code,result,handlers=self.invoke('Build Example','build',True)
        self.assertEqual((code,result['status'],result['error']),(1,'verification_unavailable','external_filter_configured'))
        self.assertEqual(result['effect_verification']['reason'],'external_filter_configured')
        self.assertFalse(result['execution_started']);self.assertFalse(any(h.called for h in handlers.values()))
        handlers=self.bindings()
        with patch.object(effects,'capture',side_effect=[self.evidence(),execution.ExternalFilterConfigured()]):
            code,result,_=self.invoke('Build Example','build',True,handlers)
        self.assertEqual((code,result['status'],result['error']),(1,'verification_unavailable','external_filter_configured'))
        self.assertTrue(result['execution_started'])

    def test_post_failure_preserves_handler_facts(self):
        for exit_code in (0,7):
            handlers=self.bindings();handlers['build'].return_value=gate.HandlerOutcome(exit_code)
            with patch.object(effects,'capture',side_effect=[self.evidence(),OSError('private')]):
                code,result,_=self.invoke('Build Example','build',True,handlers)
            self.assertEqual(code,1)
            self.assertEqual(result['status'],'verification_unavailable')
            self.assertTrue(result['execution_started'])
            self.assertEqual(result['handler_succeeded'],exit_code==0)
            self.assertFalse(result['execution_succeeded'])
            self.assertEqual(result['effect_verification']['reason'],'post_execution_verification_failed')

    def test_failed_handlers_still_verified_unchanged_or_violating(self):
        for failure in (gate.HandlerOutcome(7),RuntimeError('private')):
            for violation in (False,True):
                handlers=self.bindings()
                if isinstance(failure,Exception):handlers['build'].side_effect=failure
                else:handlers['build'].return_value=failure
                before=self.evidence();after=replace(before,index='changed') if violation else before
                with patch.object(effects,'capture',side_effect=[before,after]) as capture:
                    code,result,_=self.invoke('Build Example','build',True,handlers)
                self.assertNotEqual(code,0);self.assertEqual(capture.call_count,2)
                self.assertFalse(result['handler_succeeded']);self.assertFalse(result['execution_succeeded'])
                self.assertEqual(result['status'],'effect_violation' if violation else 'execution_failed')

    def test_no_project_no_claim_and_denials_no_verification(self):
        with patch.object(effects,'capture',side_effect=AssertionError('snapshot')):
            for cap in ('projects','doctor'):
                _,result,_=self.invoke('Show projects',cap)
                self.assertTrue(result['execution_succeeded'])
                self.assertEqual(result['effect_verification'],{'performed':False,'passed':None,
                    'observed_effects':[],'violations':[],'scope':[],'reason':'no_project_target'})
            for cap,request in [('build','Build Example'),('verify','Verify Sample'),('no_match','Commit Example'),('unknown','Unknown')]:
                _,result,handlers=self.invoke(request,cap)
                self.assertFalse(result['execution_started'])
                self.assertFalse(any(h.called for h in handlers.values()))
                self.assertFalse(result['effect_verification']['performed'])

    def test_route_and_doctor_never_snapshot_and_comparison_no_provider(self):
        import test_parameters
        with patch.object(effects,'capture',side_effect=AssertionError('snapshot')), \
             patch.object(natta.routing.provider,'run_provider',side_effect=AssertionError('provider')):
            self.assertTrue(effects.doctor_checks()[0][0])
            self.assertTrue(effects.compare(self.evidence(),self.evidence(),()).passed)
        with patch.object(effects,'capture',side_effect=AssertionError('snapshot')):
            test_parameters.ParameterIntegrationTests.invoke(self,'Build Example','build')

    def test_semantic_workflow_bindings_select_protected_mode_only(self):
        for cap in ('build','test','verify'):
            self.assertTrue(natta.handler_bindings()[cap].keywords['protected_state'])
        with patch.object(effects,'compare',return_value=None), \
             patch.object(effects,'capture',return_value=self.evidence()):
            result=self.dispatch(self.route(),True)
        self.assertEqual(result.status,'verification_unavailable')
        self.assertTrue(result.execution_started)
        self.assertTrue(result.handler_succeeded)
        self.assertFalse(result.execution_succeeded)

    def test_doctor_rejects_untyped_mapping_without_snapshot_or_provider(self):
        with patch.object(effects,'VERIFIABLE',frozenset(('project_write','git_index_write','git_commit'))), \
             patch.object(execution,'snapshot',side_effect=AssertionError('snapshot')) as capture, \
             patch.object(natta.routing.provider,'run_provider',side_effect=AssertionError('provider')):
            self.assertFalse(effects.doctor_checks()[0][0])
            capture.assert_not_called()

    def test_scope_bounded_and_metadata_cannot_be_waived(self):
        result=effects.compare(self.evidence(),replace(self.evidence(),index='changed'),policy.CATALOG['diff'].effects)
        self.assertFalse(result.passed)
        self.assertNotIn('network',result.scope)
        self.assertNotIn('external_service_mutation',result.scope)
        self.assertLess(len(json.dumps(result.as_dict())),500)
        with self.assertRaises(FrozenInstanceError):result.passed=True


if __name__=='__main__':unittest.main()
