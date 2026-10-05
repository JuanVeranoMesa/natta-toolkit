"""Legacy macOS sandbox-exec backend. Enabled only by exact local validation."""
import contextlib
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import threading

import confinement as c
import execution
import policy

SOURCE = Path(__file__).resolve().parent
SCHEMA = 1
FILES = ('macos_confinement.py','macos_worker.py','confinement.py','semantic_execution.py',
         'natta.py','execution.py','adapters.py','runtime_effects.py','policy.py','parameters.py',
         'testflight.py')


def canonical(path):
    path = Path(path)
    if (not path.is_absolute() or path.resolve() != path or
            any(p.is_symlink() for p in (path,*path.parents)) or
            any(ord(ch)<32 for ch in str(path))):
        raise ValueError('Unsafe confinement path')
    return path


def owned_runtime(runtime):
    runtime=canonical(runtime)
    for path in (runtime,runtime.parent):
        info=path.stat()
        if not path.is_dir() or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('Unsafe private runtime ownership')
    return runtime


def profile(mode, runtime):
    if mode != 'inspection':raise ValueError('Only inspection confinement is supported')
    runtime=canonical(runtime)
    return ('(version 1)\n(deny default)\n'
            '(allow file-read*)\n(allow process*)\n(allow sysctl-read)\n'
            '(allow mach-lookup)\n(allow ipc-posix*)\n'
            '(allow file-write* (subpath '+json.dumps(str(runtime),ensure_ascii=False)+'))\n'
            '(allow file-write* (literal "/dev/null"))\n(deny network*)\n')


def small_command(argv):
    p=subprocess.run(argv,capture_output=True,text=True,timeout=15)
    if p.returncode or len(p.stdout)>65536:raise ValueError('Host identity unavailable')
    return p.stdout.strip()



def identity(mode='inspection'):
    if mode != 'inspection':raise ValueError('Only inspection validation is supported')
    host=c.detect_host()
    if host.platform!='darwin' or not host.native_primitive_present:
        raise c.ConfinementUnavailable(host.reason)
    return {'schema':SCHEMA,'macos':host.version,
            'build':small_command(['/usr/bin/sw_vers','-buildVersion']),
            'python':str(Path(sys.executable).resolve()),'python_version':sys.version,
            'implementation':hashlib.sha256(b''.join((SOURCE/f).read_bytes() for f in FILES)).hexdigest(),
            'profile':hashlib.sha256(profile('inspection',Path('/private/tmp/NattaRuntime')).encode()).hexdigest()}


def state_root():
    return Path.home()/'Library/Application Support/NattaToolkit/confinement'


def safe_root(create=False):
    root=canonical(state_root())
    if create:root.mkdir(mode=0o700,parents=True,exist_ok=True)
    if root.exists():
        s=root.stat()
        if s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)!=0o700 or not root.is_dir():
            raise ValueError('Unsafe validation directory')
    return root


def record_path(mode='inspection'):
    if mode != 'inspection':raise ValueError('Only inspection validation is supported')
    return safe_root()/'inspection.json'


def validation_state(mode='inspection'):
    try:
        expected=identity(mode)
        path=record_path(mode)
        if not path.exists():return False,'validation_missing'
        if path.is_symlink():raise ValueError('Unsafe record')
        s=path.stat()
        if s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)!=0o600 or s.st_size>65536:raise ValueError('Unsafe record')
        record=json.loads(path.read_text())
        keys={'identity','fixture_validated','inspection_validated'}
        # Existing inspection records are retained; obsolete Xcode records are
        # never read. Legacy inspection envelope fields grant no extra authority.
        legacy=(set(record)==keys|{'scope','xcode_validated'} and record['scope'] is None
                and record['xcode_validated'] is False)
        if (not (set(record)==keys or legacy) or
                not identity_matches(mode, record['identity'], expected)):
            return False,'validation_stale'
        if record['fixture_validated'] is not True or record['inspection_validated'] is not True:
            return False,'validation_incomplete'
        return True,None
    except c.ConfinementUnavailable as exc:return False,str(exc)
    except Exception:return False,'validation_invalid'


def identity_matches(mode, recorded, expected):
    if recorded == expected:return True
    # Audited inspection-only compatibility: exact old/new implementation hashes,
    # identical profile, OS, interpreter and every other identity field. Never
    # accepts arbitrary future code edits or Xcode-mode records.
    if mode != 'inspection' or not isinstance(recorded, dict):return False
    try:
        compatibility=json.loads((SOURCE/'inspection_compatibility.json').read_text())
        return (expected['implementation'] == compatibility['current_implementation']
                and recorded['implementation'] in compatibility['previous_implementations']
                and {**recorded, 'implementation':expected['implementation']} == expected)
    except (OSError,ValueError,KeyError,TypeError):return False


def worker_environment(mode, runtime):
    runtime=canonical(runtime)
    env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin','HOME':str(Path.home()),
         'TMPDIR':str(runtime),'GIT_OPTIONAL_LOCKS':'0','GIT_PAGER':'cat',
         'PYTHONDONTWRITEBYTECODE':'1','LANG':'en_US.UTF-8'}
    if mode != 'inspection':raise ValueError('Only inspection environment is supported')
    return env


def save_record(mode, fingerprint):
    safe_root(create=True)
    target=record_path(mode)
    if target.is_symlink():raise ValueError('Unsafe validation target')
    record={'identity':fingerprint,'fixture_validated':True,'inspection_validated':True}
    fd,name=tempfile.mkstemp(prefix='.validation-',dir=target.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            os.fchmod(stream.fileno(),0o600);json.dump(record,stream,sort_keys=True)
        os.replace(name,target)
    finally:
        if os.path.exists(name):os.unlink(name)


def mode_for(capability):
    if capability in ('status','context','diff'):return 'inspection'
    raise c.ConfinementUnavailable('capability_mode_unvalidated')


def available(plan,project=None):
    try:return validation_state(mode_for(plan.capability))
    except c.ConfinementUnavailable as exc:return False,str(exc)


def serialize_projects(projects):
    return [{'alias':p.alias,'aliases':list(p.aliases),'name':p.name,'path':str(canonical(p.path)),
             'type':p.type,'files':{k:str(v) for k,v in p.files.items()},'execution':p.execution} for p in projects]


def bounded_run(argv, payload='', env=None, timeout=1800):
    """Bounded merged protocol/diagnostics. No raw output persisted."""
    with subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                          env=env) as process:
        timer=threading.Timer(timeout,process.kill);timer.daemon=True;timer.start()
        parts=[];size=0
        try:
            process.stdin.write(payload.encode());process.stdin.close()
            while chunk:=process.stdout.read(65536):
                size+=len(chunk)
                if size>1024*1024:
                    process.kill()
                    break
                parts.append(chunk)
            return process.wait(),b''.join(parts).decode(errors='replace')
        finally:
            timer.cancel()
            if process.poll() is None:process.kill()
            process.wait()


class Session(c.BackendSession):
    def __init__(self,plan,project,*,candidate=False):
        self.plan,self.project=plan,project
        self.mode=mode_for(plan.capability)
        if not candidate:
            valid,reason=validation_state(self.mode)
            if not valid:raise c.ConfinementUnavailable(reason)
        self.temp=None
        self.result=c.ConfinementResult(required=True,mode=self.mode,required_effects=plan.required_effects,
            process_control=c.Support.PARTIAL,backend='macos-sandbox-exec',validated=not candidate,
            policy_denied_effects=policy.FORBIDDEN_EFFECTS)

    def __enter__(self):
        self.temp=tempfile.TemporaryDirectory(prefix='natta-sandbox-')
        try:
            root=canonical(Path(self.temp.name).resolve())
            self.runtime=root/'runtime';self.runtime.mkdir(mode=0o700)
            owned_runtime(self.runtime)
            self.profile_path=root/'profile.sb'
            self.profile_path.write_text(profile(self.mode,self.runtime));self.profile_path.chmod(0o600)
            self.argv=['/usr/bin/sandbox-exec','-f',str(self.profile_path),str(Path(sys.executable).resolve()),
                       '-B',str(SOURCE/'macos_worker.py')]
            return self

        except Exception:
            self.temp.cleanup()
            raise c.ConfinementSetupFailed('profile_setup_failed')

    def __exit__(self,*args):
        if self.temp:self.temp.cleanup()

    def invoke(self,projects,project,arguments):
        from semantic_execution import HandlerOutcome
        for p in projects:
            root=canonical(p.path)
            if root.is_relative_to(self.runtime) or self.runtime.is_relative_to(root):
                raise ValueError('Runtime overlaps a project')
        payload=json.dumps({'capability':self.plan.capability,'project':project.alias,
                            'arguments':dict(arguments),'projects':serialize_projects(projects)})
        if len(payload.encode())>65536:raise ValueError('Worker payload too large')
        env=worker_environment(self.mode,self.runtime)
        try:
            code,raw=bounded_run(self.argv,payload,env)
        except (OSError,subprocess.SubprocessError):
            return c.Invocation(False,HandlerOutcome(1),error='confinement_setup_failed')
        records=[]
        for line in raw.splitlines():
            try:records.append(json.loads(line))
            except ValueError:pass
        started=any(r=={'event':'started'} for r in records)
        if not started:
            return c.Invocation(False,HandlerOutcome(1),report=execution.bounded_text(raw,1000),error='confinement_setup_failed')
        self.result=replace(self.result,applied=True,enforced_effects=self.plan.required_effects,
                            unenforced_effects=frozenset((policy.Effect.EXTERNAL_SERVICE_MUTATION,policy.Effect.GIT_PUSH)))
        finals=[r for r in records if isinstance(r,dict) and r.get('event')=='result']
        try:
            if code or len(finals)!=1:raise ValueError('Worker failed')
            r=finals[0]
            if type(r['exit_code']) is not int or not 0<=r['exit_code']<=255 or len(r['checks'])>100:raise ValueError('Invalid outcome')
            checks=tuple(execution.Result(execution.Check(execution.bounded_text(x['name'])),x['status'],
                x['exit_code'],x['duration'],detail_lines=tuple(execution.bounded_text(s) for s in x['detail_lines'][:8]),
                failing_tests=tuple(execution.bounded_text(s) for s in x['failing_tests'][:5])) for x in r['checks'])
            return c.Invocation(True,HandlerOutcome(r['exit_code'],checks),r['report'][:12000],
                                bool(r['report_truncated']),r['error'])
        except Exception:return c.Invocation(True,HandlerOutcome(1),error='worker_failed')


@contextlib.contextmanager
def open_session(plan):
    if plan.project_root is None:raise c.ConfinementUnavailable('capability_mode_unvalidated')
    # Reconstructed scope is passed by plan, not arbitrary caller JSON.
    project=plan.project
    with Session(plan,project) as session:yield session


def candidate_run(projects,project,capability):
    import parameters,natta,routing,runtime_effects
    from dataclasses import replace as changed
    route=routing.RouteResult('matched',capability,project=project.alias,arguments_resolved=True)
    ids=set(policy.CATALOG)
    decision=policy.evaluate(route,ids,parameters.schema_for(natta.make_parser(),capability),projects)
    route=changed(route,execution_policy=decision)
    plan=c.plan_for(capability,(),project,decision)
    before=runtime_effects.capture(project)
    with Session(plan,project,candidate=True) as session:
        result=session.invoke(projects,project,())
        after=runtime_effects.capture(project,before)
        verification=runtime_effects.compare(before,after,decision.effects)
        return result,verification,session.result


def fixture_validation(mode):
    """Exact parameterized production profile, disposable Git and denial fixtures."""
    import natta
    with tempfile.TemporaryDirectory(prefix='natta-validate-') as temp:
        root=Path(temp).resolve();project_root=root/'project';project_root.mkdir()
        subprocess.run(['/usr/bin/git','init','-q',str(project_root)],check=True)
        marker=project_root/'marker';marker.write_text('unchanged')
        subprocess.run(['/usr/bin/git','-C',str(project_root),'add','marker'],check=True)
        subprocess.run(['/usr/bin/git','-C',str(project_root),'-c','user.name=Fixture','-c','user.email=fixture@example.test',
                        'commit','-qm','fixture'],check=True)
        project=natta.Project('fixture',(),'Fixture',project_root,'generic-git',{})
        runtime=root/'runtime';runtime.mkdir(mode=0o700)
        sb=root/'profile.sb';sb.write_text(profile(mode,runtime));sb.chmod(0o600)
        prefix=['/usr/bin/sandbox-exec','-f',str(sb),str(Path(sys.executable).resolve()),'-B','-c']
        denied="""import errno,sys
from pathlib import Path
try:Path(sys.argv[1]).write_text('forbidden')
except OSError as e:sys.exit(0 if e.errno in (errno.EPERM,errno.EACCES) else 2)
sys.exit(1)
"""
        diagnostic=[]
        def probe(code,*args):
            code,raw=bounded_run([*prefix,code,*map(str,args)],timeout=30)
            diagnostic[:] = [execution.bounded_text(raw,500)]
            return code==0
        def failure(reason):
            return ValueError(reason+(': '+diagnostic[0] if diagnostic and diagnostic[0] else ''))
        if not probe("from pathlib import Path;import sys;assert Path(sys.argv[1]).read_text()=='unchanged'",marker):raise failure('fixture_read_failed')
        if not probe(denied,marker):raise failure('fixture_write_not_denied')
        descendant='import subprocess,sys;sys.exit(subprocess.run([sys.executable,"-B","-c",sys.argv[1],sys.argv[2]]).returncode)'
        if not probe(descendant,denied,marker):raise failure('descendant_write_not_denied')
        (runtime/'escape').symlink_to(project_root,target_is_directory=True)
        if not probe(denied,runtime/'escape/marker'):raise failure('symlink_escape_not_denied')
        if not probe("from pathlib import Path;import sys;Path(sys.argv[1]).write_text('allowed')",runtime/'output'):raise failure('runtime_write_failed')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen(4)
            port=str(listener.getsockname()[1])
            # Unsandboxed positive control distinguishes denial from no listener.
            subprocess.run([sys.executable,'-B','-c','import socket,sys;socket.create_connection(("127.0.0.1",int(sys.argv[1])),2).close()',port],check=True)
            network="""import socket,errno,sys
try:socket.create_connection(('127.0.0.1',int(sys.argv[1])),2)
except OSError as e:sys.exit(0 if e.errno in (errno.EPERM,errno.EACCES) else 2)
sys.exit(1)
"""
            if not probe(network,port):raise failure('network_not_denied')
        if marker.read_text()!='unchanged':raise ValueError('fixture_mutated')
        for cap in ('status','context','diff'):
            invocation,verification,_=candidate_run((project,),project,cap)
            if not invocation.started or invocation.outcome.exit_code or not verification.passed:
                raise ValueError('inspection_worker_failed')
    return ['fixture_read','project_write_denied','descendant_write_denied','symlink_escape_denied',
            'runtime_write_allowed','network_denied','status','context','diff','ephemeral_cleanup']


def validate():
    """Explicit zero-Luna disposable inspection validation. Never app workflows."""
    try:
        fingerprint=identity('inspection')
        target=record_path()
        if target.is_symlink():raise ValueError('Unsafe record')
        if target.exists():target.unlink()
        checks=fixture_validation('inspection')
        if identity('inspection')!=fingerprint:raise ValueError('host_changed_during_validation')
        save_record('inspection',fingerprint)
        return 0,{'status':'validated','mode':'inspection','checks':checks}
    except Exception as exc:
        return 1,{'status':'validation_failed','mode':'inspection',
                  'reason':execution.bounded_text(str(exc)),'inspection_validated':False}
