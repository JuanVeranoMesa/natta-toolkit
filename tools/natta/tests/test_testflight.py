"""No Apple uploads/builds/providers: fake Apple commands and disposable Git roots."""
import contextlib
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import unittest
from unittest.mock import Mock, patch

import authorization
import compound_execution
import natta
import planning
import planner_provider_v3 as planner
import policy
import router_codex as provider
import runtime_effects
import semantic_execution as gate
import testflight as tf
import test_commits as fixtures
import test_authorization as terminal_fixture

REAL_APPLE_RUN=tf.apple_run
REAL_AUTHENTICATION=tf.authentication
TEAM='ABCDEFGHIJ'
# Deliberately fake sentinel; never an operational credential.
SECRET='SECRET_TOKEN_NEVER_DISPLAY_123'


class TestFlightTests(unittest.TestCase):
    git=fixtures.CommitTests.git
    dirty=fixtures.CommitTests.dirty

    def setUp(self):
        fixtures.CommitTests.setUp(self)
        (self.repo/'Example.xcodeproj').mkdir()
        self.registry.write_text(self.registry.read_text().replace('generic-git','ios-xcode') + '''
[projects.execution]
project="Example.xcodeproj"
scheme="Example"
configuration="Debug"
unit_targets=["ExampleTests"]
test_targets=["ExampleTests"]
''')
        self.project=natta.load_registry(self.registry)[0]
        self.commands=[];self.failure=None;self.mutation=None;self.mutate_upload=False
        self.settings={'PRODUCT_TYPE':'com.apple.product-type.application','SKIP_INSTALL':'NO',
            'CONFIGURATION':'Release','PRODUCT_BUNDLE_IDENTIFIER':'com.example.App',
            'MARKETING_VERSION':'1.2.3','CURRENT_PROJECT_VERSION':'12',
            'DEVELOPMENT_TEAM':TEAM,'CODE_SIGN_STYLE':'Automatic','CODE_SIGNING_ALLOWED':'YES'}
        self.metadata={'project':{'schemes':['Example'],'configurations':['Debug','Release'],'targets':['Example','ExampleTests']}}
        self.identities='  1) ABC "Apple Development: Fixture ('+TEAM+')"\n 1 valid identities found'
        self.patches=[patch.object(tf.shutil,'which',return_value='/mock/tool'),
                      patch.object(tf,'authentication',return_value=tf.Authentication('xcode_account')),
                      patch.object(tf,'apple_run',side_effect=self.apple)]
        for p in self.patches:p.start();self.addCleanup(p.stop)

    def apple(self,argv,cwd,**kwargs):
        argv=tuple(argv);self.commands.append((argv,kwargs))
        if '-list' in argv:return subprocess.CompletedProcess(argv,0,json.dumps(self.metadata),'')
        if '-showBuildSettings' in argv:return subprocess.CompletedProcess(argv,0,json.dumps([{'target':'Example','buildSettings':self.settings}]),'')
        if argv[0]=='security':return subprocess.CompletedProcess(argv,0,self.identities,'')
        if '-help' in argv:return subprocess.CompletedProcess(argv,0,'app-store-connect manageAppVersionAndBuildNumber destination -allowProvisioningUpdates -authenticationKeyPath -authenticationKeyID -authenticationKeyIssuerID','')
        stage='archive' if 'archive' in argv else plistlib.loads(Path(argv[argv.index('-exportOptionsPlist')+1]).read_bytes())['destination']
        if self.failure==stage:return subprocess.CompletedProcess(argv,42,'',SECRET+' '+getattr(self,'failure_message','failure'))
        if stage=='archive':
            archive=Path(argv[argv.index('-archivePath')+1]);app=archive/'Products/Applications/Example.app';app.mkdir(parents=True)
            (archive/'Info.plist').write_bytes(plistlib.dumps({'ApplicationProperties':{'ApplicationPath':'Applications/Example.app','Team':TEAM}}))
            (app/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'com.example.App','CFBundleShortVersionString':'1.2.3','CFBundleVersion':'12'}))
        elif stage=='export':
            path=Path(argv[argv.index('-exportPath')+1]);path.mkdir();(path/'Example.ipa').write_bytes(b'fake-package')
        if self.mutation==stage:(self.repo/'tracked').write_text('unexpected change')
        return subprocess.CompletedProcess(argv,0,'Apple accepted '+SECRET,'')

    def execute(self,check=False):return tf.execute([self.project],self.project,check=check)

    def invoke(self,args,*,response='yes\n',tty=True):
        source=terminal_fixture.Terminal(response) if tty else io.StringIO(response)
        out=terminal_fixture.Terminal()
        with patch('sys.stdin',source),patch('sys.stdout',out), \
             patch.object(provider,'run_provider',side_effect=AssertionError('Luna')):
            code=natta.main(['--registry',str(self.registry),'testflight',*args])
        return code,out.getvalue(),source

    def stages(self):
        return [ ('archive' if 'archive' in a else plistlib.loads(Path(a[a.index('-exportOptionsPlist')+1]).read_bytes())['destination'])
                for a,k in self.commands if 'archive' in a or '-exportArchive' in a]

    def test_parser_and_canonical_registry_alias(self):
        args=natta.make_parser().parse_args(['testflight','example','--confirm','--json'])
        self.assertTrue(args.confirm);self.assertTrue(args.json)
        code,out,_=self.invoke(['fixture','--confirm','--json'])
        self.assertEqual(code,0);self.assertEqual(json.loads(out)['project'],'example')

    def test_arbitrary_path_and_distribute_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code,_,_=self.invoke([str(self.repo),'--confirm'])
        self.assertEqual(code,1);self.assertFalse(self.commands)
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):natta.make_parser().parse_args(['distribute','example'])

    def test_unsupported_adapter_rejected(self):
        result=tf.execute([self.project],replace(self.project,type='generic-git'),check=True)
        self.assertEqual(result.error,'unsupported_adapter');self.assertFalse(self.commands)

    def test_missing_scheme_release_or_archiveable_target(self):
        self.metadata['project']['schemes']=[]
        self.assertEqual(self.execute(True).error,'container_scheme_or_configuration_missing')
        self.metadata['project']['schemes']=['Example'];self.metadata['project']['configurations']=['Debug']
        self.assertEqual(self.execute(True).error,'release_configuration_missing')
        self.metadata['project']['configurations'].append('Release');self.settings['SKIP_INSTALL']='YES'
        self.assertEqual(self.execute(True).error,'one_archiveable_app_required')
        self.assertFalse(any('archive' in a for a,k in self.commands))

    def test_missing_team_fails_before_archive(self):
        self.settings['DEVELOPMENT_TEAM']=''
        result=self.execute()
        self.assertEqual(result.error,'development_team_required')
        self.assertFalse(result.readiness['team_configured'])
        self.assertFalse(any('archive' in a for a,k in self.commands))

    def test_automatic_signing_without_local_identity_attempts_managed_workflow(self):
        self.identities='0 valid identities found'
        before=natta.snapshot(self.repo)
        result=self.execute()
        self.assertEqual(result.status,'uploaded')
        self.assertIsNone(result.error)
        self.assertEqual(result.readiness['local_distribution_identity'],'unavailable')
        self.assertTrue(result.readiness['xcode_managed_signing_eligible'])
        self.assertTrue(result.effect_verification.passed)
        self.assertEqual(natta.snapshot(self.repo),before)
        stages=[a for a,k in self.commands if 'archive' in a or '-exportArchive' in a]
        self.assertEqual(len(stages),3)
        self.assertTrue(all('-allowProvisioningUpdates' in a for a in stages))
        self.assertFalse(any(any(value.startswith('CODE_SIGN') for value in a) for a in stages))

    def test_manual_signing_missing_local_distribution_identity_fails(self):
        self.settings['CODE_SIGN_STYLE']='Manual'
        for identities in ('0 valid identities found', self.identities):
            with self.subTest(identities=identities):
                self.identities=identities
                result=self.execute()
                self.assertEqual(result.error,'usable_team_signing_identity_required')
                self.assertFalse(result.readiness['xcode_managed_signing_eligible'])
        self.assertFalse(any('archive' in a for a,k in self.commands))

    def test_manual_signing_is_not_silently_converted_to_automatic_export(self):
        self.settings['CODE_SIGN_STYLE']='Manual'
        self.identities='1) ABC "Apple Distribution: Fixture ('+TEAM+')"'
        result=self.execute(True)
        self.assertEqual(result.error,'manual_signing_export_mapping_unsupported')
        self.assertFalse(result.readiness['local_archive_upload_ready'])
        self.assertFalse(any('archive' in a for a,k in self.commands))

    def test_check_separates_local_identity_managed_and_authentication_readiness(self):
        self.identities='0 valid identities found'
        code,out,_=self.invoke(['example','--check','--json'])
        self.assertEqual(code,0)
        ready=json.loads(out)['readiness']
        self.assertTrue(ready['team_configured']);self.assertEqual(ready['signing_style'],'Automatic')
        self.assertEqual(ready['local_signing_identity'],'unavailable')
        self.assertEqual(ready['local_distribution_identity'],'unavailable')
        self.assertTrue(ready['xcode_managed_signing_eligible'])
        self.assertTrue(ready['authentication_ready']);self.assertTrue(ready['local_archive_upload_ready'])
        code,out,_=self.invoke(['example','--check'])
        self.assertEqual(code,0)
        self.assertIn('Local distribution identity: unavailable',out)
        self.assertIn('Xcode-managed signing eligible: yes',out)
        self.assertIn('Remote signing/authentication/upload readiness: not verified',out)
        self.assertFalse(any('-allowProvisioningUpdates' in a for a,k in self.commands))

    def test_keychain_probe_unavailable_is_informational_for_automatic_signing(self):
        original=self.apple
        def runner(argv,cwd,**kwargs):
            if argv[0]=='security':raise FileNotFoundError('security unavailable')
            return original(argv,cwd,**kwargs)
        result=tf.execute([self.project],self.project,check=True,runner=runner)
        self.assertEqual(result.status,'ready')
        self.assertEqual(result.readiness['local_signing_identity'],'unknown')
        self.assertTrue(result.readiness['xcode_managed_signing_eligible'])

    def test_managed_archive_signing_failure_is_truthful_and_stops_export(self):
        self.identities='0 valid identities found';self.failure='archive'
        self.failure_message='No signing certificate found: '+SECRET
        result=self.execute()
        self.assertEqual((result.stage,result.error),('archive','apple_signing_failed'))
        self.assertTrue(result.execution_started);self.assertFalse(result.upload_started)
        self.assertTrue(result.effect_verification.passed)
        self.assertFalse(any('-exportArchive' in a for a,k in self.commands))
        self.assertNotIn(SECRET,json.dumps(result.as_dict()))

    def test_invalid_bundle_version_build_and_configuration(self):
        for key,value in (('PRODUCT_BUNDLE_IDENTIFIER',''),('CURRENT_PROJECT_VERSION','$(SECRET)'),('MARKETING_VERSION',SECRET),('CONFIGURATION','Debug')):
            with self.subTest(key=key):
                original=self.settings[key];self.settings[key]=value
                result=self.execute(True);self.settings[key]=original
                self.assertNotEqual(result.status,'ready');self.assertNotIn(SECRET,json.dumps(result.as_dict()))

    def test_archive_export_upload_success_release_external_paths_and_cleanup(self):
        result=self.execute()
        self.assertEqual(result.status,'uploaded');self.assertTrue(result.upload_succeeded)
        self.assertTrue(result.archive_succeeded);self.assertTrue(result.export_succeeded)
        archive=[a for a,k in self.commands if 'archive' in a][0]
        self.assertEqual(archive[archive.index('-configuration')+1],'Release')
        self.assertEqual(archive[archive.index('-scheme')+1],'Example')
        self.assertEqual(archive[archive.index('-destination')+1],'generic/platform=iOS')
        state=Path(archive[archive.index('-archivePath')+1]).parent
        self.assertFalse(state.is_relative_to(self.repo));self.assertFalse(state.exists())
        exports=[a for a,k in self.commands if '-exportArchive' in a]
        self.assertEqual(len(exports),2)
        self.assertTrue(result.effect_verification.passed)
        self.assertEqual(result.as_dict()['processing'],'pending')
        self.assertFalse(result.as_dict()['submitted_for_review']);self.assertFalse(result.as_dict()['released_publicly'])
        self.assertFalse(result.as_dict()['pushed']);self.assertFalse(result.as_dict()['committed'])

    def test_export_options_disable_build_number_management_and_no_credentials(self):
        distribution=tf.Distribution('Example','com.example.App','1.2.3','12',TEAM,(),tf.Authentication('xcode_account'))
        options=tf.export_options(distribution,'upload')
        self.assertEqual(options['method'],'app-store-connect');self.assertEqual(options['destination'],'upload')
        self.assertFalse(options['manageAppVersionAndBuildNumber'])
        self.assertEqual(set(options),{'method','destination','signingStyle','teamID','manageAppVersionAndBuildNumber','uploadSymbols'})

    def test_archive_failure_prevents_export_and_upload(self):
        self.failure='archive';result=self.execute()
        self.assertEqual(result.stage,'archive');self.assertFalse(result.upload_succeeded)
        self.assertEqual(sum('-exportArchive' in a for a,k in self.commands),0)

    def test_export_failure_prevents_upload(self):
        self.failure='export';result=self.execute()
        self.assertEqual(result.stage,'export');self.assertTrue(result.archive_succeeded)
        self.assertEqual(sum('-exportArchive' in a for a,k in self.commands),1)
        self.assertFalse(result.upload_succeeded)

    def test_upload_failure_and_build_number_conflict_bounded(self):
        self.failure='upload';self.failure_message='ITMS-90189 redundant binary already uploaded'
        result=self.execute();self.assertEqual(result.stage,'upload')
        self.assertEqual(result.error,'build_number_conflict_update_explicitly')
        self.assertNotIn(SECRET,json.dumps(result.as_dict()));self.assertNotIn(SECRET,tf.render(result,self.project))

    def test_failure_sensitive_diagnostics_never_logged_or_rendered(self):
        self.failure='archive';code,out,_=self.invoke(['example','--confirm','--json'])
        self.assertEqual(code,1);self.assertNotIn(SECRET,out)
        self.commands=[];code,out,_=self.invoke(['example','--confirm'])
        self.assertEqual(code,1);self.assertNotIn(SECRET,out)
        self.assertFalse(any('shell' in kwargs for argv,kwargs in self.commands))

    def test_mutation_stops_before_upload_and_fails_verification(self):
        self.mutation='archive';result=self.execute()
        self.assertEqual(result.stage,'verification');self.assertFalse(result.effect_verification.passed)
        self.assertFalse(result.upload_succeeded)
        self.assertEqual(sum('-exportArchive' in a for a,k in self.commands),0)
        self.assertEqual((self.repo/'tracked').read_text(),'unexpected change')

    def test_accepted_upload_reported_truthfully_if_later_verification_fails(self):
        self.mutation='upload';result=self.execute()
        self.assertEqual(result.status,'verification_failed');self.assertTrue(result.upload_succeeded)
        self.assertTrue(result.as_dict()['uploaded']);self.assertFalse(result.effect_verification.passed)

    def test_direct_decline_prevents_any_apple_command(self):
        code,out,_=self.invoke(['example'],response='no\n')
        self.assertEqual(code,1);self.assertIn('authorization_declined',out)
        self.assertIn('external build record',out);self.assertIn('NOT submit',out)
        self.assertEqual(out.count('Continue? [y/N]:'),1);self.assertFalse(self.commands)

    def test_direct_approval_once_and_confirm_skips_prompt(self):
        code,out,_=self.invoke(['example']);self.assertEqual(code,0)
        self.assertEqual(out.count('Continue? [y/N]:'),1)
        with patch.object(authorization,'request_approval',side_effect=AssertionError('prompt')):
            code,out,source=self.invoke(['example','--confirm'])
        self.assertEqual(code,0);self.assertEqual(source.tell(),0)

    def test_json_and_non_tty_never_prompt(self):
        with patch.object(authorization,'request_approval',side_effect=AssertionError('prompt')):
            code,out,source=self.invoke(['example','--json'])
        self.assertEqual(code,1);self.assertEqual(json.loads(out)['status'],'authorization_required')
        self.assertEqual(source.tell(),0);self.assertFalse(self.commands)
        code,out,source=self.invoke(['example'],tty=False)
        self.assertEqual(code,1);self.assertNotIn('Continue?',out);self.assertFalse(self.commands)

    def test_check_never_authorizes_archives_or_uploads(self):
        with patch.object(authorization,'request_approval',side_effect=AssertionError('prompt')):
            code,out,_=self.invoke(['example','--check','--json'])
        self.assertEqual(code,0);data=json.loads(out)
        self.assertEqual(data['status'],'ready');self.assertFalse(data['remote_readiness_verified'])
        self.assertFalse(data['execution_started']);self.assertFalse(data['uploaded'])
        self.assertFalse(any('archive' in a or '-exportArchive' in a or '-allowProvisioningUpdates' in a for a,k in self.commands))

    def test_applied_external_filter_refused_before_apple_commands(self):
        marker=self.root/'filter-executed'
        self.git('config','filter.custom.clean','touch '+json.dumps(str(marker))+' && cat')
        (self.repo/'.git/info').mkdir(exist_ok=True);(self.repo/'.git/info/attributes').write_text('* filter=custom\n')
        result=self.execute()
        self.assertEqual((result.status,result.error,result.execution_started),('verification_failed','external_filter_configured',False))
        self.assertEqual(result.effect_verification.reason,'external_filter_configured')
        self.assertIn('clean/process filter applies',tf.render(result,self.project))
        self.assertEqual(self.commands,[]);self.assertFalse(marker.exists())

    def test_submodule_local_filter_refused_before_apple_commands(self):
        import test_git_filters as filters
        home=self.root/'home';home.mkdir();marker=self.root/'filter-executed'
        with patch.dict(os.environ,{'HOME':str(home),'XDG_CONFIG_HOME':str(home/'.config')}):
            data=filters.add_filtered_submodule(self.repo,self.root/'source','data.txt filter=custom\n',
                                                'touch '+json.dumps(str(marker))+' && cat')
            filters.require_refresh(data)
            result=self.execute()
        self.assertEqual((result.status,result.error,result.execution_started),('verification_failed','submodule_unsupported',False))
        self.assertEqual(result.effect_verification.reason,'submodule_unsupported')
        self.assertIn('does not support repositories containing Git submodules',tf.render(result,self.project))
        self.assertEqual(self.commands,[]);self.assertFalse(marker.exists())

    def test_verification_required_and_no_git_mutation(self):
        initial=self.git('rev-parse','HEAD');index=self.git('diff','--cached')
        with patch.object(tf.runtime_effects,'capture',wraps=runtime_effects.capture) as capture, \
             patch.object(natta.commits,'create',side_effect=AssertionError('commit')):
            result=self.execute()
        self.assertEqual(result.status,'uploaded');self.assertEqual(capture.call_count,2)
        self.assertEqual(self.git('rev-parse','HEAD'),initial);self.assertEqual(self.git('diff','--cached'),index)
        self.assertFalse(any(any(x in a for x in ('commit','push','add')) for a,k in self.commands))

    def test_doctor_only_local_checks_no_network(self):
        checks=tf.doctor_checks([self.project]);self.assertTrue(all(ok for ok,label in checks))
        self.assertIn('NOT verified',checks[-1][1]);self.assertFalse(any('-allowProvisioningUpdates' in a for a,k in self.commands))

    def test_policy_truthful_and_authority_not_available_to_other_capabilities(self):
        decision=policy.CATALOG['testflight']
        self.assertEqual(decision.authorization,policy.Authorization.EXPLICIT)
        self.assertTrue({policy.Effect.NETWORK,policy.Effect.EXTERNAL_SERVICE_MUTATION}<=decision.effects)
        self.assertFalse(decision.effects & {policy.Effect.PROJECT_WRITE,policy.Effect.GIT_INDEX_WRITE,policy.Effect.GIT_COMMIT,policy.Effect.GIT_PUSH})
        catalog=dict(policy.CATALOG);catalog['build']=replace(catalog['build'],effects=decision.effects)
        with self.assertRaises(ValueError):policy.validate_catalog(provider.authoritative_ids()-{'codex','route'},catalog)

    def test_missing_authentication_fails_before_archive(self):
        with patch.object(tf,'authentication',side_effect=tf.PrerequisiteError('xcode_account_or_api_key_required')):
            result=self.execute()
        self.assertEqual(result.stage,'prerequisites')
        self.assertFalse(any('archive' in a or '-exportArchive' in a for a,k in self.commands))

    def test_api_references_and_secrets_absent_from_all_presentation(self):
        secret_path='/private/secure/'+SECRET+'.p8'
        auth=tf.Authentication('app_store_connect_api_key',('-authenticationKeyPath',secret_path,
            '-authenticationKeyID','ABCDEFGHIJ','-authenticationKeyIssuerID','00000000-1111-2222-3333-444444444444'))
        with patch.object(tf,'authentication',return_value=auth):
            code,out,_=self.invoke(['example','--confirm','--json'])
        self.assertEqual(code,0);self.assertNotIn(SECRET,out)
        self.assertNotIn('authenticationKeyPath',out);self.assertNotIn(secret_path,out)
        data=json.loads(out);self.assertEqual(data['authentication'],'app_store_connect_api_key')
        archive=[a for a,k in self.commands if 'archive' in a][0]
        self.assertIn(secret_path,archive)
        self.assertFalse(Path(archive[archive.index('-archivePath')+1]).parent.exists())

    def test_failure_cleans_all_owned_runtime_material(self):
        for stage in ('archive','export','upload'):
            self.commands=[];self.failure=stage;result=self.execute()
            self.assertEqual(result.stage,stage)
            archive=[a for a,k in self.commands if 'archive' in a][0]
            self.assertFalse(Path(archive[archive.index('-archivePath')+1]).parent.exists())

    def test_help_is_first_class_and_stops_before_execution(self):
        with patch.object(tf,'execute',side_effect=AssertionError('execution')), \
             contextlib.redirect_stdout(io.StringIO()) as out,self.assertRaises(SystemExit) as status:
            natta.main(['testflight','--help'])
        self.assertEqual(status.exception.code,0)
        self.assertIn('natta testflight example',out.getvalue())
        self.assertIn('--check',out.getvalue());self.assertIn('never submits',out.getvalue())

    def test_archive_identity_mismatch_prevents_export(self):
        self.settings['CURRENT_PROJECT_VERSION']='13'
        result=self.execute();self.assertEqual(result.error,'archive_identity_or_version_changed')
        self.assertFalse(any('-exportArchive' in a for a,k in self.commands))

    def test_real_subprocess_boundary_argv_and_no_shell(self):
        with patch.object(subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')) as run:
            REAL_APPLE_RUN(('xcodebuild','archive'),self.repo,env={'SAFE':'1'})
        self.assertEqual(run.call_args.args[0],['xcodebuild','archive'])
        self.assertNotIn('shell',run.call_args.kwargs);self.assertTrue(run.call_args.kwargs['capture_output'])


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.key=self.root/'AuthKey.p8';self.key.write_text(SECRET);self.key.chmod(0o600)
        self.env={'NATTA_ASC_KEY_PATH':str(self.key),'NATTA_ASC_KEY_ID':'ABCDEFGHIJ',
                  'NATTA_ASC_ISSUER_ID':'00000000-1111-2222-3333-444444444444'}

    def test_secure_external_api_key_reference_never_reads_or_copies_key(self):
        with patch.object(Path,'read_bytes',side_effect=AssertionError('key contents read')):
            result=REAL_AUTHENTICATION([],environ=self.env)
        self.assertEqual(result.mode,'app_store_connect_api_key')
        self.assertIn(str(self.key),result.flags);self.assertNotIn(str(self.key),repr(result))
        self.assertEqual(self.key.read_text(),SECRET)
        self.assertEqual(list(self.root.iterdir()),[self.key])

    def test_partial_insecure_and_in_repository_key_fail_closed_without_secret(self):
        for changes in ({'NATTA_ASC_ISSUER_ID':''},{'NATTA_ASC_KEY_PATH':'relative.p8'}, {'NATTA_ASC_KEY_ID':SECRET}):
            with self.assertRaises(tf.PrerequisiteError) as exc:REAL_AUTHENTICATION([],environ={**self.env,**changes})
            self.assertNotIn(SECRET,str(exc.exception))
        self.key.chmod(0o644)
        with self.assertRaises(tf.PrerequisiteError):REAL_AUTHENTICATION([],environ=self.env)
        self.key.chmod(0o600)
        from types import SimpleNamespace
        with self.assertRaises(tf.PrerequisiteError):REAL_AUTHENTICATION([SimpleNamespace(path=self.root)],environ=self.env)

    def test_existing_xcode_account_and_missing_credentials(self):
        preferences=self.root/'Xcode.plist'
        preferences.write_bytes(plistlib.dumps({'DVTDeveloperAccountManagerAppleIDLists':{'account':['private@example.test']}}))
        auth=REAL_AUTHENTICATION([],environ={},preferences=preferences)
        self.assertEqual(auth.mode,'xcode_account');self.assertEqual(auth.flags,())
        self.assertNotIn('private@example.test',repr(auth))
        preferences.write_bytes(plistlib.dumps({}))
        with self.assertRaises(tf.PrerequisiteError):REAL_AUTHENTICATION([],environ={},preferences=preferences)


class TestFlightSemanticTests(unittest.TestCase):
    def setUp(self):
        import test_semantic_execution as fixture
        fixture.SemanticExecutionTests.setUp(self)

    def handlers(self):
        return {c:Mock(return_value=gate.HandlerOutcome(0)) for c in policy.CATALOG}

    def invoke(self,command,request,*,json_mode=True,confirm=False,response='yes\n',candidate=None,handlers=None):
        handlers=self.handlers() if handlers is None else handlers
        args=['--registry',str(self.registry),command,request]
        if json_mode:args.append('--json')
        if confirm:args.append('--confirm')
        source=terminal_fixture.Terminal(response);out=terminal_fixture.Terminal()
        choice='testflight'
        reply=planner.Reply(json.dumps(candidate or {'status':'planned','steps':[{'capability':'testflight','project':'example','arguments':{}}]}),external_requests=1)
        with patch.object(provider,'run_provider',return_value=json.dumps({'choice':choice})) as route_call, \
             patch.object(planner,'propose',return_value=reply) as planner_call, \
             patch.object(natta,'handler_bindings',return_value=handlers), \
             patch('sys.stdin',source),patch('sys.stdout',out), \
             patch.object(runtime_effects,'capture_commit',return_value=runtime_effects.capture.return_value), \
             patch.object(tf,'execute',side_effect=AssertionError('real Apple workflow')):
            code=natta.main(args)
        if command in ('plan','do'):planner_call.assert_called_once();route_call.assert_not_called()
        else:route_call.assert_called_once();planner_call.assert_not_called()
        return code,json.loads(out.getvalue()) if json_mode else out.getvalue(),handlers

    def test_route_selection_nonexecuting_and_explicit_policy(self):
        code,data,handlers=self.invoke('route','Upload Example App to TestFlight')
        self.assertEqual(code,0);self.assertEqual(data['capability'],'testflight')
        self.assertEqual(data['project'],'example');self.assertFalse(data['executed'])
        self.assertEqual(data['execution_policy']['authorization'],'explicit')
        self.assertFalse(any(h.called for h in handlers.values()))

    def test_execute_requires_authorization_then_approved_once(self):
        code,data,handlers=self.invoke('execute','Upload Example to TestFlight')
        self.assertEqual(code,1);self.assertEqual(data['status'],'authorization_required')
        self.assertFalse(any(h.called for h in handlers.values()))
        code,out,handlers=self.invoke('execute','Upload Sample to TestFlight',json_mode=False)
        self.assertEqual(code,0);self.assertEqual(handlers['testflight'].call_count,1)
        self.assertEqual(handlers['testflight'].call_args.args[1].alias,'sample')
        self.assertIn('external build record',out);self.assertIn('NOT submit',out)
        self.assertEqual(out.count('Continue? [y/N]:'),1)

    def candidate(self):
        return {'status':'planned','steps':[{'capability':c,'project':'example','arguments':{}}
            for c in ('build','test','commit','testflight')]}

    def test_planner_four_steps_nonexecuting(self):
        with patch.object(runtime_effects,'capture',side_effect=AssertionError('snapshot')):
            code,data,handlers=self.invoke('plan','Build Example, test, commit, then upload to TestFlight',candidate=self.candidate())
        self.assertEqual(code,0);self.assertEqual([s['capability'] for s in data['steps']],['build','test','commit','testflight'])
        self.assertFalse(data['executed']);self.assertFalse(any(h.called for h in handlers.values()))

    def test_compound_final_upload_one_whole_plan_prompt(self):
        code,out,handlers=self.invoke('do','Build Example, test, commit, then upload to TestFlight',candidate=self.candidate(),json_mode=False)
        self.assertEqual(code,0);self.assertEqual(out.count('Continue? [y/N]:'),1)
        self.assertIn('Step 4:',out);self.assertIn('external build record',out)
        self.assertIn('NOT submit',out)
        for cap in ('build','test','commit','testflight'):self.assertEqual(handlers[cap].call_count,1)

    def test_earlier_failure_never_starts_testflight(self):
        handlers=self.handlers();handlers['test'].return_value=gate.HandlerOutcome(1)
        code,data,handlers=self.invoke('do','Build/test/commit/TestFlight Example',candidate=self.candidate(),confirm=True,handlers=handlers)
        self.assertEqual(code,1);self.assertEqual(data['failed_step'],2)
        handlers['testflight'].assert_not_called();handlers['commit'].assert_not_called()
        self.assertEqual(data['steps'][3]['status'],'not_started')

    def test_public_release_is_vetoed_even_if_provider_returns_safe_prefix(self):
        for text in ('Publish Example to the App Store','Release Example publicly','Upload Example to TestFlight and publish it','Distribute Sample','Submit Example for App Review'):
            code,data,handlers=self.invoke('route',text)
            self.assertEqual(data['status'],'no_match');self.assertFalse(data['executed'])
            code,data,handlers=self.invoke('plan',text,candidate=self.candidate())
            self.assertEqual(data['status'],'no_match');self.assertEqual(data['steps'],[])
            self.assertFalse(any(h.called for h in handlers.values()))

    def test_invalid_semantic_arguments_and_shell_never_execute(self):
        for cap,args in (('testflight',{'check':True}),('shell',{}),('distribute',{})):
            candidate={'status':'planned','steps':[{'capability':cap,'project':'example','arguments':args}]}
            code,data,handlers=self.invoke('do','unsafe',candidate=candidate,confirm=True)
            self.assertEqual(code,1);self.assertEqual(data['status'],'invalid_plan')
            self.assertFalse(any(h.called for h in handlers.values()))

    def test_historical_provider_inputs_preserved(self):
        import planner_provider_v2 as previous
        import planner_evaluation
        import commit_evaluation
        projects=natta.load_registry(self.registry)
        planner_evaluation.load_controls(projects)
        commit_evaluation.controls('planner',projects)
        self.assertNotIn('testflight',previous.ALLOWED);self.assertNotIn('testflight',provider.PRODUCTION_IDS)
        self.assertNotIn('testflight',provider.definitions()['concise'])
        self.assertIn('testflight',provider.toolkit_definitions()['concise'])


class TestFlightEvaluationTests(unittest.TestCase):
    def setUp(self):
        import test_parameters
        test_parameters.ParameterIntegrationTests.setUp(self)

    def test_frozen_suites_counted_mocked_and_nonexecuting(self):
        import testflight_evaluation as evaluation
        projects=natta.load_registry(self.registry)
        for mode,count in (('routing',12),('planner',16)):
            corpus,control,_=evaluation.controls(mode,projects)
            self.assertEqual(len(corpus['cases']),count)
            self.assertEqual(control['predeclared_acceptance']['minimum_exact_accuracy'],1.0)
            with patch.object(provider,'run_provider',side_effect=AssertionError('Luna')):
                with self.assertRaises(ValueError):evaluation.run(mode,count-1,self.root/'refused.json',self.registry)
            replies=([json.dumps({'choice':case['expected']['capability']}) for case in corpus['cases']]
                if mode=='routing' else [planner.Reply(json.dumps(case['expected']),external_requests=1) for case in corpus['cases']])
            with patch.object(provider,'run_provider',side_effect=replies) as runner, \
                 patch.object(planner,'propose',side_effect=replies) as proposed, \
                 patch.object(tf,'execute',side_effect=AssertionError('Apple workflow')), \
                 patch.object(runtime_effects,'capture',side_effect=AssertionError('snapshot')):
                artifact=evaluation.run(mode,count,self.root/(mode+'.json'),self.registry)
            self.assertTrue(artifact['assessment']['passed']);self.assertFalse(artifact['executed'])
            self.assertEqual(artifact['actual_external_requests'],count)
            self.assertEqual(runner.call_count if mode=='routing' else proposed.call_count,count)
            with patch.object(provider,'run_provider',side_effect=AssertionError('Luna')):
                with self.assertRaises(FileExistsError):evaluation.run(mode,count,self.root/(mode+'.json'),self.registry)

    def test_frozen_control_tampering_and_provider_failure_refuse_or_fail(self):
        import testflight_evaluation as evaluation
        with patch.object(planner,'POLICY','changed'),patch.object(planner,'propose',side_effect=AssertionError('provider')):
            with self.assertRaises(ValueError):evaluation.run('planner',16,self.root/'tampered.json',self.registry)
        with patch.object(planner,'propose',return_value=planner.Reply(error='provider_failure',external_requests=1)):
            artifact=evaluation.run('planner',16,self.root/'failure.json',self.registry)
        self.assertFalse(artifact['assessment']['passed']);self.assertEqual(artifact['metrics']['provider_failures'],16)
