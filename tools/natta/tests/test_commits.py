"""Local commits only in disposable repositories; semantic providers are mocked."""
import contextlib
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch,Mock

import commits
import execution
import natta
import parameters
import policy
import routing
import router_codex as provider
import runtime_effects
import semantic_execution
import confinement
import planning
import planner_provider_v2 as planner


class CommitTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name).resolve();self.repo=self.root/'repo';self.repo.mkdir()
        self.git('init','-b','main');self.git('config','user.name','Fixture');self.git('config','user.email','fixture@example.test')
        (self.repo/'tracked').write_text('original\n');self.git('add','tracked');self.git('commit','-m','Initial')
        self.registry=self.root/'projects.toml'
        self.registry.write_text('schema_version=1\n[[projects]]\nalias="example"\naliases=["fixture"]\nname="Example App"\ntype="generic-git"\npath='+json.dumps(str(self.repo))+'\n')
        self.project=natta.load_registry(self.registry)[0]
        self.initial=self.git('rev-parse','HEAD').strip().decode()
        self.addCleanup(patch.stopall)
        patch.object(provider,'workspace_path',return_value=self.root/'router').start()
        patch.object(provider,'probe_cli',return_value='mock Codex').start()

    def git(self,*args):
        return subprocess.check_output(['git','-c','core.hooksPath=/dev/null','-c','commit.gpgSign=false','-C',str(self.repo),*args],stderr=subprocess.DEVNULL)

    def dirty(self):
        (self.repo/'tracked').write_text('updated\n')

    def invoke(self,argv,choice='commit'):
        events=json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps({'choice':choice})}})+'\n'+json.dumps({'type':'turn.completed'})
        with patch.object(provider,'run_provider',return_value=json.dumps({'choice':choice})) as model,contextlib.redirect_stdout(io.StringIO()) as out:
            code=natta.main(['--registry',str(self.registry),*argv])
        return code,out.getvalue(),model

    def test_parser_registry_default_custom_and_json(self):
        parser=natta.make_parser()
        args=parser.parse_args(['commit','example','--message','Improve history layout'])
        self.assertEqual((args.project,args.message),('example','Improve history layout'))
        self.dirty()
        with patch.object(provider,'run_provider',side_effect=AssertionError('Luna')),contextlib.redirect_stdout(io.StringIO()) as out:
            code=natta.main(['--registry',str(self.registry),'commit','fixture','--json'])
        data=json.loads(out.getvalue());self.assertEqual(code,0)
        self.assertEqual(data['message'],'Update Example App');self.assertEqual(data['project'],'example')
        self.assertEqual(data['commit_hash'],self.git('rev-parse','HEAD').strip().decode())
        self.assertFalse(data['pushed']);self.assertTrue(data['effect_verification']['passed'])

    def test_arbitrary_path_and_push_options_refused(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(natta.main(['--registry',str(self.registry),'commit',str(self.repo)]),1)
        for argv in (['commit','example','--push'],['commit','example','--amend']):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):natta.make_parser().parse_args(argv)
        self.assertEqual(self.git('rev-parse','HEAD').strip().decode(),self.initial)

    def test_custom_message_no_shell_interpretation(self):
        self.dirty();message='  Improve history; $(touch nope) --amend  '
        result=commits.create(self.project,message)
        self.assertEqual(result.status,'committed');self.assertEqual(result.message,message.strip())
        self.assertFalse((self.repo/'nope').exists())
        self.assertEqual(self.git('show','-s','--format=%B','HEAD').decode().rstrip('\n'),message.strip())

    def test_invalid_messages_do_not_stage(self):
        self.dirty();before=self.git('diff','--cached')
        for message in ('','  ','x'*513,'line\nbreak','null\0byte'):
            result=commits.create(self.project,message)
            self.assertEqual(result.error,'invalid_commit_message');self.assertFalse(result.execution_started)
            self.assertEqual(self.git('diff','--cached'),before)

    def test_clean_no_changes_zero_exit_no_empty_commit(self):
        result=commits.create(self.project)
        self.assertEqual((result.status,result.committed,result.exit_code),('no_changes',False,0))
        self.assertIsNone(result.commit_hash);self.assertIsNone(result.message)
        self.assertEqual(self.git('rev-parse','HEAD').strip().decode(),self.initial)

    def test_mixed_staged_unstaged_untracked_deleted_changes(self):
        (self.repo/'staged').write_text('staged');self.git('add','staged')
        self.dirty();(self.repo/'new').write_text('new');(self.repo/'tracked').unlink()
        result=commits.create(self.project)
        self.assertEqual(result.status,'committed');self.assertEqual(set(result.changed_paths),{'tracked','staged','new'})
        self.assertEqual(self.git('status','--porcelain'),b'')
        self.assertEqual(self.git('rev-parse','HEAD^').strip().decode(),self.initial)
        self.assertEqual(result.changed_path_count,3)
        self.assertEqual(result.effect_verification.observed_effects,commits.ALLOWED)

    def test_tracked_modification_and_dirty_content_preserved(self):
        self.dirty();before=runtime_effects.capture_commit(self.project)
        result=commits.create(self.project)
        after=runtime_effects.capture_commit(self.project,before)
        self.assertEqual(result.status,'committed');self.assertEqual(before.worktree_contents,after.worktree_contents)
        self.assertEqual((before.repository,before.git_directory),(after.repository,after.git_directory))
        self.assertEqual(self.git('show','HEAD:tracked'),b'updated\n')

    def test_unborn_repository_creates_one_root_commit(self):
        repo=self.root/'unborn';repo.mkdir()
        subprocess.check_call(['git','init','-q','-b','main',str(repo)])
        subprocess.check_call(['git','-C',str(repo),'config','user.name','Fixture'])
        subprocess.check_call(['git','-C',str(repo),'config','user.email','fixture@example.test'])
        (repo/'new').write_text('first')
        result=commits.create(replace(self.project,path=repo))
        self.assertEqual(result.status,'committed');self.assertTrue(result.effect_verification.passed)

    def test_symlink_contents_not_followed_and_external_target_not_staged(self):
        outside=self.root/'outside';outside.write_text('secret')
        (self.repo/'link').symlink_to(outside)
        result=commits.create(self.project)
        self.assertEqual(result.status,'committed');self.assertEqual(outside.read_text(),'secret')
        self.assertEqual(self.git('show','HEAD:link').decode(),str(outside))

    def test_hooks_signing_and_auto_maintenance_disabled(self):
        hook=self.repo/'.git/hooks/pre-commit';hook.write_text('#!/bin/sh\ntouch tracked new-from-hook\nexit 1\n');hook.chmod(0o700)
        self.git('config','commit.gpgSign','true');self.git('config','gpg.program','nonexistent-signing-tool')
        self.dirty();result=commits.create(self.project)
        self.assertEqual(result.status,'committed');self.assertFalse((self.repo/'new-from-hook').exists())

    def test_filters_and_merge_state_fail_before_staging(self):
        self.dirty();self.git('config','filter.evil.clean','touch arbitrary')
        result=commits.create(self.project);self.assertFalse(result.execution_started)
        self.git('config','--unset','filter.evil.clean')
        (self.repo/'.git/MERGE_HEAD').write_text(self.initial+'\n')
        result=commits.create(self.project);self.assertEqual(result.error,'git_operation_in_progress')
        self.assertFalse(result.execution_started)

    def test_git_environment_override_cannot_redirect_index(self):
        self.dirty();outside=self.root/'index'
        with patch.dict(os.environ,{'GIT_INDEX_FILE':str(outside)}):result=commits.create(self.project)
        self.assertEqual(result.status,'committed');self.assertFalse(outside.exists())

    def test_argv_commands_no_push_fetch_pull_shell_or_network(self):
        self.dirty();real=commits.subprocess.run
        with patch.object(commits.subprocess,'run',wraps=real) as runner:result=commits.create(self.project)
        self.assertEqual(result.status,'committed')
        for call in runner.call_args_list:
            argv=call.args[0];self.assertIsInstance(argv,tuple);self.assertFalse(call.kwargs.get('shell',False))
            self.assertEqual(call.kwargs['cwd'],self.repo)
            self.assertFalse(set(('push','fetch','pull','amend','reset','revert','checkout')) & set(argv))
            self.assertEqual(argv[0],'git')

    def test_failed_git_commit_reports_staged_mutation_without_rollback(self):
        self.dirty();real=commits.git
        def fail(project,*args):
            if args[0]=='commit':return subprocess.CompletedProcess(args,1,b'',b'failed')
            return real(project,*args)
        with patch.object(commits,'git',side_effect=fail):result=commits.create(self.project)
        self.assertEqual(result.status,'failed');self.assertFalse(result.committed)
        self.assertTrue(result.execution_started);self.assertTrue(result.git_commit_started)
        self.assertTrue(self.git('diff','--cached'));self.assertEqual(self.git('rev-parse','HEAD').strip().decode(),self.initial)
        self.assertIn(policy.Effect.GIT_INDEX_WRITE,result.effect_verification.observed_effects)

    def test_commit_created_but_bad_message_reports_mutation_truthfully(self):
        self.dirty();real=commits.read
        def wrong(project,*args,**kwargs):
            if args[:2]==('show','-s'):return b'unexpected message\n'
            return real(project,*args,**kwargs)
        with patch.object(commits,'read',side_effect=wrong):result=commits.create(self.project)
        self.assertEqual(result.status,'failed');self.assertTrue(result.committed)
        self.assertNotEqual(self.git('rev-parse','HEAD').strip().decode(),self.initial)
        self.assertEqual(result.error,'commit_postcondition_failed')

    def test_authorized_git_changes_but_project_mutation_still_violates(self):
        self.dirty();before=runtime_effects.capture_commit(self.project)
        self.git('add','-A');self.git('commit','-m','fixture')
        after=runtime_effects.capture_commit(self.project,before)
        self.assertTrue(runtime_effects.compare(before,after,commits.ALLOWED).passed)
        (self.repo/'tracked').write_text('forbidden mutation')
        after=runtime_effects.capture_commit(self.project,before)
        result=runtime_effects.compare(before,after,commits.ALLOWED)
        self.assertFalse(result.passed);self.assertIn(policy.Effect.PROJECT_WRITE,result.violations)

    def test_branch_and_repository_identity_are_not_authorized_by_commit(self):
        before=runtime_effects.capture_commit(self.project)
        for field,value in (('branch',b'refs/heads/other'),('repository','other'),('git_directory','other')):
            result=runtime_effects.compare(before,replace(before,**{field:value}),commits.ALLOWED)
            self.assertFalse(result.passed)

    def test_worktree_change_during_commit_overrides_success(self):
        self.dirty();real=commits.git
        def mutate(project,*args):
            if args[0]=='commit':(project.path/'tracked').write_text('unexpected')
            return real(project,*args)
        with patch.object(commits,'git',side_effect=mutate):result=commits.create(self.project)
        self.assertTrue(result.committed);self.assertEqual(result.status,'effect_violation')
        self.assertEqual((self.repo/'tracked').read_text(),'unexpected')

    def test_semantic_without_confirmation_never_starts(self):
        self.dirty()
        with patch.object(natta,'handle_commit',side_effect=AssertionError('handler')):
            code,out,model=self.invoke(['execute','--json','Commit Example'])
        data=json.loads(out);self.assertEqual(code,1);self.assertEqual(data['status'],'authorization_required')
        self.assertFalse(data['execution_started']);model.assert_called_once()
        self.assertEqual(self.git('rev-parse','HEAD').strip().decode(),self.initial)

    def test_semantic_confirmed_one_handler_one_luna_default_message(self):
        self.dirty();real=natta.handle_commit
        with patch.object(natta,'handle_commit',wraps=real) as handler, \
             patch.object(confinement,'open_session',side_effect=AssertionError('inspection sandbox')):
            code,out,model=self.invoke(['execute','--json','--confirm','Commit Example'])
        data=json.loads(out);self.assertEqual(code,0);model.assert_called_once();handler.assert_called_once()
        self.assertEqual(handler.call_args.args[1].alias,'example');self.assertEqual(handler.call_args.args[2],())
        self.assertEqual(data['result']['commit']['message'],'Update Example App')
        self.assertTrue(data['execution_succeeded']);self.assertTrue(data['effect_verification']['passed'])
        self.assertEqual(data['confinement']['reason'],'not_required_for_commit');self.assertFalse(data['confinement']['required'])
        self.assertEqual(data['execution_policy']['authorization'],'explicit')
        self.assertFalse(data['result']['commit']['pushed'])

    def test_route_commit_never_executes_or_snapshots(self):
        self.dirty()
        with patch.object(natta,'handle_commit',side_effect=AssertionError('handler')), \
             patch.object(runtime_effects,'capture_commit',side_effect=AssertionError('snapshot')):
            code,out,model=self.invoke(['route','--json','Commit Example'])
        data=json.loads(out);self.assertEqual(code,0);self.assertEqual(data['capability'],'commit')
        self.assertFalse(data['executed']);self.assertTrue(data['arguments_resolved']);model.assert_called_once()

    def test_policy_atomic_explicit_git_only_and_doctor_integrity(self):
        ids=provider.authoritative_ids()-{'codex','route'}
        route=routing.RouteResult('matched','commit',project='example',arguments_resolved=True)
        decision=policy.evaluate(route,ids,parameters.schema_for(natta.make_parser(),'commit'),[self.project])
        self.assertTrue(decision.eligible);self.assertEqual(decision.authorization,policy.Authorization.EXPLICIT)
        self.assertEqual(decision.capability_type,policy.CapabilityType.ATOMIC)
        self.assertTrue(commits.ALLOWED<=decision.effects)
        self.assertFalse({policy.Effect.PROJECT_WRITE,policy.Effect.GIT_PUSH,policy.Effect.NETWORK}&decision.effects)
        with patch.object(commits,'create',side_effect=AssertionError('commit')),patch.object(provider,'run_provider',side_effect=AssertionError('Luna')):
            self.assertTrue(all(ok for ok,_ in semantic_execution.doctor_checks(ids,natta.handler_bindings())))
        catalog=dict(policy.CATALOG);catalog['build']=replace(catalog['build'],effects=catalog['build'].effects|commits.ALLOWED)
        with self.assertRaises(ValueError):policy.validate_catalog(ids,catalog)

    def test_planner_commit_is_only_a_validated_intent(self):
        candidate={'status':'planned','steps':[{'capability':c,'project':'Example','arguments':{}} for c in ('build','test','commit')]}
        with patch.object(planner,'propose',return_value=planner.Reply(json.dumps(candidate),external_requests=1)) as model, \
             patch.object(commits,'create',side_effect=AssertionError('handler')):
            result=planning.propose('Build Example, test it, then commit it',natta.make_parser(),[self.project],natta.handler_bindings(),provider_mode=planner)
        self.assertTrue(result.valid);self.assertFalse(result.executed);model.assert_called_once()
        self.assertEqual(result.authorization,policy.Authorization.EXPLICIT);self.assertTrue(commits.ALLOWED<=result.effects)
        self.assertFalse(result.steps[-1].confinement_required)
        for capability in ('push','shell','edit'):
            bad={'status':'planned','steps':[{'capability':capability,'project':'example','arguments':{}}]}
            self.assertFalse(planning.validate(bad,natta.make_parser(),[self.project],natta.handler_bindings()).valid)
        bad={'status':'planned','steps':[{'capability':'commit','project':'example','arguments':{'message':'AI message'}}]}
        self.assertEqual(planning.validate(bad,natta.make_parser(),[self.project],natta.handler_bindings()).error,'invalid_argument')
        self.assertIn('Reject the WHOLE goal',planner.POLICY)

    def evaluation_registry(self):
        path=self.root/'evaluation-projects.toml'
        path.write_text('schema_version=1\n'+''.join('[[projects]]\nalias='+json.dumps(alias)+'\naliases='+json.dumps(aliases)+'\nname='+json.dumps(name)+'\ntype="generic-git"\npath='+json.dumps(str(self.repo))+'\n' for alias,aliases,name in (
            ('example',['example-app'],'Example App'),('sample',['sampleapp','sample-app'],'Sample App'))))
        return path

    def test_focused_evaluations_counted_no_execution(self):
        import commit_evaluation as e
        registry=self.evaluation_registry();projects=natta.load_registry(registry)
        for mode,count,suite in (('routing',16,'regression-r2'),('routing',8,'r1-regression-r2'),('routing',8,'holdout-r2'),('planner',20,'baseline')):
            corpus,control,_=e.controls(mode,projects,suite)
            self.assertEqual(len(corpus['cases']),count);self.assertEqual(control['predeclared_acceptance']['minimum_exact_accuracy'],1.0 if mode=='routing' else .95)
            for auth in (None,count-1,count+1,True):
                with patch.object(provider,'run_provider',side_effect=AssertionError('Luna')), \
                     patch.object(planner,'propose',side_effect=AssertionError('Luna')):
                    with self.assertRaises(ValueError):e.run(mode,auth,self.root/'refused.json',registry,suite=suite)
            replies=([json.dumps({'choice':c['expected']['capability']}) for c in corpus['cases']] if mode=='routing' else
                [planner.Reply(json.dumps(c['expected']),external_requests=1) for c in corpus['cases']])
            with patch.object(provider,'run_provider',side_effect=replies) as runner, \
                 patch.object(planner,'propose',side_effect=replies) as proposed, \
                 patch.object(commits,'create',side_effect=AssertionError('commit')), \
                 patch.object(runtime_effects,'capture',side_effect=AssertionError('snapshot')), \
                 patch.object(runtime_effects,'capture_commit',side_effect=AssertionError('snapshot')):
                artifact=e.run(mode,count,self.root/(mode+'-'+suite+'.json'),registry,suite=suite)
            self.assertTrue(artifact['assessment']['passed']);self.assertFalse(artifact['executed'])
            self.assertEqual(artifact['actual_external_requests'],count)
            self.assertEqual(runner.call_count if mode=='routing' else proposed.call_count,count)
            with patch.object(provider,'run_provider',side_effect=AssertionError('Luna')):
                with self.assertRaises(FileExistsError):e.run(mode,count,self.root/(mode+'-'+suite+'.json'),registry,suite=suite)

    def test_extension_control_change_and_provider_failure_cannot_pass(self):
        import commit_evaluation as e
        registry=self.evaluation_registry()
        with patch.object(planner,'POLICY','changed'),patch.object(planner,'propose',side_effect=AssertionError('Luna')):
            with self.assertRaises(ValueError):e.run('planner',20,self.root/'changed.json',registry)
        with patch.object(planner,'propose',return_value=planner.Reply(error='provider_failure',external_requests=1)):
            artifact=e.run('planner',20,self.root/'failed.json',registry)
        self.assertFalse(artifact['assessment']['passed']);self.assertFalse(artifact['complete'])
        self.assertEqual(artifact['metrics']['provider_failures'],20)


    def test_routing_revision_is_commit_only_and_planner_is_unchanged(self):
        data=provider.definitions()['concise']
        revised=provider.production_definitions(routing_revision=True)['concise']
        self.assertEqual({k:v for k,v in revised.items() if k!='commit'},data)
        original=provider.production_definitions()['concise']['commit']
        self.assertIn('named registered project',original)
        self.assertIn('save changes to Git',revised['commit'])
        self.assertIn('omitted, ambiguous, unknown',revised['commit'])
        self.assertEqual(original.split('No push,')[1],revised['commit'].split('No push,')[1])
        current=provider.workspace_contents('concise',provider.PRODUCTION_IDS)
        self.assertEqual(current['AGENTS.md'],provider.POLICY)
        schema=json.loads(current['output-schema.json'])
        self.assertEqual(schema['required'],['choice'])
        self.assertFalse(schema['additionalProperties'])

    def test_four_regression_cases_select_commit_then_resolve_locally(self):
        import commit_evaluation as e
        registry=self.evaluation_registry()
        corpus,_,_=e.controls('routing',natta.load_registry(registry),'regression-r2')
        for case in corpus['cases']:
            if case['id'] not in ('commit-routing-v2-03','commit-routing-v2-04','commit-routing-v2-07','commit-routing-v2-08'):
                continue
            with patch.object(provider,'run_provider',return_value='{"choice":"commit"}') as runner, \
                 patch.object(commits,'create',side_effect=AssertionError('commit')), \
                 patch.object(runtime_effects,'capture_commit',side_effect=AssertionError('snapshot')):
                result=natta.prepare_route(case['request'],natta.make_parser(),registry)
            self.assertEqual(e.compact_route(result),case['expected'])
            self.assertFalse(result.executed)
            self.assertEqual(runner.call_count,1)

    def test_original_controls_and_failed_result_are_preserved(self):
        import commit_evaluation as e,hashlib
        projects=natta.load_registry(self.evaluation_registry())
        with self.assertRaises(ValueError):e.controls('routing',projects)
        self.assertEqual(hashlib.sha256((natta.ROOT/'evaluation/commit-routing-v2-corpus.json').read_bytes()).hexdigest(), '9dd43fbfff2ec8f5b35786a79e838cc3ebe15c26ce62d8561139ee9825936c12')
        # Preserve a newly generated failed evaluation artifact, not private host output.
        with patch.object(provider, 'run_provider', return_value='{"choice":"no_match"}'):
            path=self.root/'synthetic-failed-evaluation.json'
            result=e.run('routing',16,path,self.evaluation_registry(),suite='regression-r2')
            self.assertFalse(result['assessment']['passed'])
            before=path.read_bytes()
            with self.assertRaises(FileExistsError):
                e.run('routing',16,path,self.evaluation_registry(),suite='regression-r2')
            self.assertEqual(before,path.read_bytes())

    def test_holdout_frozen_gate_and_unresolved_unknown_project(self):
        import commit_evaluation as e
        registry=self.evaluation_registry();projects=natta.load_registry(registry)
        corpus,control,_=e.controls('routing',projects,'holdout-r2')
        self.assertEqual(len(corpus['cases']),8)
        self.assertEqual(control['predeclared_acceptance'],{
            'complete_cases':8,'provider_failures':0,'minimum_exact_accuracy':1.0,
            'unsupported_rejection':1.0,'adversarial_rejection':1.0,
            'accepted_unknown_capabilities':0,'accepted_unknown_projects':0})
        with patch.object(provider,'run_provider',return_value='{"choice":"commit"}'):
            result=natta.prepare_route(corpus['cases'][-1]['request'],natta.make_parser(),registry)
        self.assertEqual(e.compact_route(result),corpus['cases'][-1]['expected'])
        self.assertFalse(result.execution_policy.eligible)
        original=json.loads((natta.ROOT/'evaluation/commit-routing-v2-corpus.json').read_text())
        self.assertFalse({c['request'] for c in corpus['cases']} & {c['request'] for c in original['cases']})
        with self.assertRaises(ValueError):e.controls('planner',projects,'holdout-r2')

    def test_holdout_unsafe_false_positive_fails_gate(self):
        import commit_evaluation as e
        registry=self.evaluation_registry()
        with patch.object(provider,'run_provider',return_value='{"choice":"commit"}'):
            result=e.run('routing',8,self.root/'unsafe-holdout.json',registry,suite='holdout-r2')
        self.assertFalse(result['assessment']['passed'])
        self.assertEqual(result['metrics']['unsupported_rejection'],0)
        self.assertEqual(result['metrics']['adversarial_rejection'],0)

    def test_undecided_commit_target_is_locally_ambiguous_not_no_match(self):
        import commit_evaluation as e
        registry=self.evaluation_registry();projects=natta.load_registry(registry)
        corpus,_,_=e.controls('routing',projects,'r1-regression-r2')
        case=corpus['cases'][3]
        self.assertEqual(case['request'],'Make a commit for Example or Sample; I have not picked one.')
        with patch.object(provider,'run_provider',return_value='{"choice":"commit"}') as runner, \
             patch.object(commits,'create',side_effect=AssertionError('commit')), \
             patch.object(runtime_effects,'capture_commit',side_effect=AssertionError('snapshot')):
            result=natta.prepare_route(case['request'],natta.make_parser(),registry)
        self.assertEqual(e.compact_route(result),case['expected'])
        self.assertEqual(result.resolution_error,'ambiguous_project')
        self.assertFalse(result.executed)
        self.assertFalse(result.execution_policy.eligible)
        self.assertEqual(runner.call_count,1)

    def test_previous_r1_controls_fail_closed_and_r2_words_are_new(self):
        import commit_evaluation as e
        projects=natta.load_registry(self.evaluation_registry())
        for suite in ('regression','holdout'):
            with self.assertRaises(ValueError):e.controls('routing',projects,suite)
        fresh,control,_=e.controls('routing',projects,'holdout-r2')
        old=[json.loads((natta.ROOT/'evaluation'/name).read_text()) for name in
             ('commit-routing-v2-corpus.json','commit-routing-holdout-r1-corpus.json')]
        self.assertFalse({c['request'] for c in fresh['cases']} &
                         {c['request'] for corpus in old for c in corpus['cases']})
        self.assertEqual(control['predeclared_acceptance']['minimum_exact_accuracy'],1.0)
        self.assertIn('undecided target',provider.production_definitions(routing_revision=True)['concise']['commit'])

    def test_historical_and_current_provider_domains_remain_separate(self):
        import planner_provider as historical
        self.assertNotIn('commit',provider.ROUTING_IDS);self.assertIn('commit',provider.PRODUCTION_IDS)
        self.assertNotIn('commit',provider.definitions()['concise'])
        self.assertIn('commit',provider.production_definitions()['concise'])
        self.assertNotIn('commit',historical.ALLOWED);self.assertIn('commit',planner.ALLOWED)
        candidate={'status':'planned','steps':[{'capability':'commit','project':'example','arguments':{}}]}
        old=planning.validate(candidate,natta.make_parser(),[self.project],natta.handler_bindings(),provider_mode=historical)
        self.assertEqual(old.error,'unsupported_capability')
        self.assertEqual(historical.VERSION,'phase8a-v1');self.assertEqual(planner.VERSION,'phase8a-commit-v2')

    def test_historical_frozen_artifacts_remain_byte_identical(self):
        import hashlib
        manifest=json.loads((natta.ROOT/'evaluation/public-controls-integrity.json').read_text())
        self.assertIn('evaluation/planner-corpus-v1.json', manifest)
        self.assertIn('config/routing.json', manifest)
        for path,digest in manifest.items():
            self.assertEqual(hashlib.sha256((natta.ROOT/path).read_bytes()).hexdigest(),digest,path)

    def test_post_snapshot_failure_does_not_hide_created_commit(self):
        self.dirty();real=runtime_effects.capture_commit
        def fail(project,before=None):
            if before is not None:raise execution.NattaError('unavailable')
            return real(project)
        with patch.object(runtime_effects,'capture_commit',side_effect=fail):result=commits.create(self.project)
        self.assertTrue(result.committed);self.assertTrue(result.git_commit_started)
        self.assertEqual(result.status,'failed');self.assertFalse(result.effect_verification.passed)
        self.assertEqual(result.commit_hash,self.git('rev-parse','HEAD').strip().decode())

    def test_unknown_outcome_after_commit_start_is_null_not_false(self):
        self.dirty();real_capture=runtime_effects.capture_commit;real_read=commits.read
        def no_after(project,before=None):
            if before is not None:raise execution.NattaError('unavailable')
            return real_capture(project)
        def no_head(project,*args,**kwargs):
            if args[:2]==('rev-parse','--verify'):raise execution.NattaError('unavailable')
            return real_read(project,*args,**kwargs)
        with patch.object(runtime_effects,'capture_commit',side_effect=no_after),patch.object(commits,'read',side_effect=no_head):result=commits.create(self.project)
        self.assertIsNone(result.committed);self.assertTrue(result.git_commit_started)
        self.assertFalse(result.effect_verification.passed);self.assertIsNone(result.commit_hash)
        self.assertNotEqual(self.git('rev-parse','HEAD').strip().decode(),self.initial)

    def test_private_snapshot_evidence_rejects_parent_symlink_escape(self):
        self.dirty();before=runtime_effects.capture_commit(self.project)
        outside=self.root/'outside';outside.mkdir()
        (outside/'file').write_text('outside')
        (self.repo/'directory').symlink_to(outside,target_is_directory=True)
        with self.assertRaises(execution.NattaError):
            execution.protected_snapshot(self.repo,baseline=before.baseline,content_paths=(b'directory/file',))
        self.assertEqual((outside/'file').read_text(),'outside')

    def test_message_option_shaped_value_is_not_a_git_option(self):
        self.dirty();result=commits.create(self.project,'--amend')
        self.assertEqual(result.status,'committed');self.assertEqual(result.message,'--amend')
        self.assertEqual(self.git('rev-parse','HEAD^').strip().decode(),self.initial)

    def test_host_disposable_acceptance_runs_only_direct_commit_argv(self):
        import commit_acceptance as host
        real=host.cli
        with patch.object(host,'cli',wraps=real) as invoked,contextlib.redirect_stdout(io.StringIO()) as out:
            host.fixture()
        self.assertEqual(invoked.call_count,3)
        self.assertTrue(all(call.args[1]=='commit' for call in invoked.call_args_list))
        self.assertIn('"disposable_acceptance": "PASS"',out.getvalue())
        self.assertIn('"luna_calls": 0',out.getvalue())
        self.assertIn('Disposable fixture cleaned.',out.getvalue())
        with self.assertRaises(ValueError):host.confirm(self.root/'missing.json',False)

    def test_doctor_commit_parameter_requirements_fail_closed_no_mutation(self):
        import argparse
        parser=natta.make_parser();ids=provider.authoritative_ids()-{'codex','route'}
        with patch.object(commits,'create',side_effect=AssertionError('commit')),patch.object(provider,'run_provider',side_effect=AssertionError('Luna')):
            self.assertTrue(all(ok for ok,_ in semantic_execution.doctor_checks(ids,natta.handler_bindings(),parser)))
            commands=next(a for a in parser._actions if isinstance(a,argparse._SubParsersAction))
            next(a for a in commands.choices['commit']._actions if a.dest=='project').required=False
            self.assertFalse(semantic_execution.doctor_checks(ids,natta.handler_bindings(),parser)[0][0])
