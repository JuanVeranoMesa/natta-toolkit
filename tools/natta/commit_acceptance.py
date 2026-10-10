"""Explicit host acceptance: disposable first, then review/confirm ONE chosen project."""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import commits
import execution
import natta
import runtime_effects

ROOT=Path(__file__).resolve().parent


def cli(registry,*args, expected=0):
    child=subprocess.run([sys.executable,'-B',str(ROOT/'natta.py'),'--registry',str(registry),*args],capture_output=True,text=True)
    if child.returncode!=expected:raise ValueError('Acceptance CLI failed: '+child.stdout[:12000]+child.stderr[:1000])
    return json.loads(child.stdout)


def fingerprint(snapshot):
    def encode(value):
        if isinstance(value,bytes):return {'bytes':value.hex()}
        if isinstance(value,dict):return {k:encode(v) for k,v in value.items()}
        if isinstance(value,(list,tuple)):return [encode(v) for v in value]
        return value
    return hashlib.sha256(json.dumps(encode(asdict(snapshot)),sort_keys=True).encode()).hexdigest()


def remote_refs(project):
    return hashlib.sha256(commits.read(project,'for-each-ref','refs/remotes/')).hexdigest()


def fixture():
    with tempfile.TemporaryDirectory(prefix='natta-commit-acceptance-') as temp:
        root=Path(temp).resolve();repo=root/'repo';repo.mkdir(mode=0o700)
        for argv in (['git','init','-q','-b','main',str(repo)],
                     ['git','-C',str(repo),'config','user.name','Fixture'],
                     ['git','-C',str(repo),'config','user.email','fixture@example.test']):
            subprocess.run(argv,check=True,env=execution.git_environment())
        registry=root/'projects.toml'
        registry.write_text('schema_version=1\n[[projects]]\nalias="fixture"\nname="Disposable Fixture"\ntype="generic-git"\npath='+json.dumps(str(repo))+'\n')
        (repo/'tracked').write_text('first\n')
        first=cli(registry,'commit','fixture','--json')
        if first['status']!='committed' or first['message']!='Update Disposable Fixture':raise ValueError('Default commit failed')
        (repo/'tracked').write_text('second\n');(repo/'untracked').write_text('new\n')
        second=cli(registry,'commit','fixture','--message','Fixture custom message','--json')
        project=natta.lookup(natta.load_registry(registry),'fixture')
        if (second['status']!='committed' or second['message']!='Fixture custom message'
                or not second['effect_verification']['passed'] or second['pushed']
                or commits.read(project,'rev-parse','HEAD^').strip().decode()!=first['commit_hash']
                or commits.read(project,'status','--porcelain')):raise ValueError('Mixed changes commit failed')
        clean=cli(registry,'commit','fixture','--json')
        if clean['status']!='no_changes' or clean['committed']:raise ValueError('Clean handling failed')
        print(json.dumps({'disposable_acceptance':'PASS','default_commit':first['commit_hash'],
            'custom_commit':second['commit_hash'],'effect_verification':second['effect_verification'],
            'pushed':False,'luna_calls':0},indent=2))
    print('Disposable fixture cleaned.')


def review(project_id,state_path):
    project=natta.lookup(natta.load_registry(natta.DEFAULT_REGISTRY),project_id)
    state_path=state_path.absolute()
    if state_path.resolve()!=state_path or state_path.is_relative_to(project.path):raise ValueError('State must be an external canonical path')
    before=runtime_effects.capture_commit(project)
    status=commits.read(project,'status','--short').decode(errors='replace')
    if not status:raise ValueError('Choose a project with changes you actually want committed')
    print('ALL staged, unstaged, deleted and untracked changes will be included:',flush=True)
    print(status[:10000],flush=True)
    print('Default message: '+commits.message_for(project),flush=True)
    # Reserve without overwriting any earlier acceptance record.
    fd=os.open(state_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        denied=cli(natta.DEFAULT_REGISTRY,'execute','--json','Commit '+project.name,expected=1)
        if denied['status']!='authorization_required' or denied['execution_started']:raise ValueError('Unconfirmed commit was not denied')
        after=runtime_effects.capture_commit(project,before)
        if fingerprint(before)!=fingerprint(after):raise ValueError('Protected state changed during review')
        state={'project':project.alias,'snapshot_sha256':fingerprint(before),'head':before.commit.decode(),
               'remote_refs_sha256':remote_refs(project),'default_message':commits.message_for(project),'review_passed':True}
        with os.fdopen(fd,'w') as stream:json.dump(state,stream,indent=2);stream.write('\n')
        print(json.dumps({'unconfirmed':'authorization_required','execution_started':False,
                          'protected_state':'UNCHANGED','next':'Review the changes; run confirm only if you want this ONE real commit.'},indent=2))
    except BaseException:
        try:os.close(fd)
        except OSError:pass
        state_path.unlink(missing_ok=True)
        raise


def confirm(state_path,confirmed):
    if not confirmed:raise ValueError('Explicit --confirm-real-project required')
    state=json.loads(state_path.read_text())
    project=natta.lookup(natta.load_registry(natta.DEFAULT_REGISTRY),state['project'])
    before=runtime_effects.capture_commit(project)
    if not state['review_passed'] or fingerprint(before)!=state['snapshot_sha256']:raise ValueError('State changed since review; stop and review again')
    print('Creating ONE local commit for '+project.name+'; no push or rollback.',flush=True)
    result=cli(natta.DEFAULT_REGISTRY,'execute','--json','--confirm','Commit '+project.name)
    after=runtime_effects.capture_commit(project,before)
    verification=runtime_effects.compare(before,after,commits.ALLOWED)
    committed=result['result']['commit']
    if (not result['execution_succeeded'] or not result['handler_succeeded'] or not verification.passed
            or committed['status']!='committed' or committed['pushed']
            or before.commit==after.commit or committed['commit_hash']!=after.commit.decode()
            or committed['message']!=state['default_message'] or remote_refs(project)!=state['remote_refs_sha256']):
        raise ValueError('Acceptance failed AFTER possible mutation; inspect Git state. No rollback performed.')
    print(json.dumps({'acceptance':'PASS','project':project.alias,'commit_hash':committed['commit_hash'],
        'message':committed['message'],'handler_succeeded':result['handler_succeeded'],
        'execution_succeeded':result['execution_succeeded'],'effect_verification':result['effect_verification'],
        'pushed':False,'remote_tracking_refs':'UNCHANGED','rollback':False},indent=2))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='mode',required=True);sub.add_parser('fixture')
    rev=sub.add_parser('review');rev.add_argument('project',choices=('example','sample'));rev.add_argument('--state',required=True,type=Path)
    conf=sub.add_parser('confirm');conf.add_argument('--state',required=True,type=Path);conf.add_argument('--confirm-real-project',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.mode=='fixture':fixture()
        elif args.mode=='review':review(args.project,args.state)
        else:confirm(args.state,args.confirm_real_project)
    except (ValueError,OSError,subprocess.SubprocessError) as exc:parser.exit(1,str(exc)+'\n')
    return 0


if __name__=='__main__':raise SystemExit(main())
