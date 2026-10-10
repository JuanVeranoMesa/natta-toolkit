"""One deterministic local commit. No hooks, signing, external filters or remotes."""
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import execution
import runtime_effects
from policy import Effect

MAX_MESSAGE = 512
MAX_PATHS = 30
ALLOWED = frozenset((Effect.GIT_INDEX_WRITE, Effect.GIT_COMMIT))


@dataclass(frozen=True)
class CommitResult:
    status: str
    project: str
    committed: bool | None = False
    commit_hash: str | None = None
    message: str | None = None
    changed_paths: tuple[str, ...] = ()
    changed_path_count: int = 0
    pushed: bool = False
    exit_code: int = 1
    execution_started: bool = False
    git_commit_started: bool = False
    error: str | None = None
    detail: str | None = None
    effect_verification: runtime_effects.VerificationResult = runtime_effects.VerificationResult()

    def as_dict(self):
        return {**{k:getattr(self,k) for k in ('status','project','committed','commit_hash','message',
            'changed_path_count','pushed','exit_code','execution_started','git_commit_started','error','detail')},
            'changed_paths':list(self.changed_paths),'effect_verification':self.effect_verification.as_dict()}


def message_for(project, message=None):
    value = 'Update '+project.name if message is None else message
    if not isinstance(value,str) or not value.strip() or len(value.strip())>MAX_MESSAGE or any(ord(c)<32 or ord(c)==127 for c in value):
        raise ValueError('invalid_commit_message')
    return value.strip()


def git(project, *args):
    # Do not inherit GIT_DIR, GIT_INDEX_FILE, GIT_WORK_TREE, injected config or author
    # overrides. Identity is supplied by ordinary Git configuration, never Luna.
    env=execution.git_environment()
    argv=('git','--no-optional-locks','--no-pager','-c','core.hooksPath=/dev/null',
          '-c','commit.gpgSign=false','-c','core.fsmonitor=false',
          '-c','core.untrackedCache=false','-c','core.splitIndex=false',
          '-c','maintenance.auto=false','-c','gc.auto=0','-c','core.quotePath=true','-C',str(project.path),*args)
    return subprocess.run(argv,cwd=project.path,env=env,capture_output=True,timeout=60)


def read(project,*args, optional=False):
    result=git(project,*args)
    if result.returncode and not (optional and result.returncode==1):
        raise execution.NattaError('git_operation_failed')
    if len(result.stdout)>1024*1024:raise execution.NattaError('git_evidence_limit')
    return result.stdout


def create(project,message=None):
    """Dirty files are input. On failure leave index/commit state; never roll back."""
    before=None;started=commit_started=False;head=None;created=False;actual_message=None
    paths=();count=0;detail=None;verification=runtime_effects.VerificationResult()
    try:
        requested=message_for(project,message)
        path=project.path
        if not isinstance(path,Path) or path.resolve()!=path or not path.is_dir():
            raise ValueError('invalid_project_repository')
        before=runtime_effects.capture_commit(project)
        # Merge/rebase/cherry-pick state and unresolved entries require direct Git UX.
        for marker in ('MERGE_HEAD','CHERRY_PICK_HEAD','REVERT_HEAD','rebase-merge','rebase-apply','sequencer'):
            if (Path(before.git_directory)/marker).exists():raise ValueError('git_operation_in_progress')
        if any(entry.startswith(b'160000 ') for entry in read(project,'ls-files','--stage','-z').split(b'\0')):
            raise ValueError('submodule_commit_unsupported')
        if read(project,'ls-files','--unmerged','-z'):raise ValueError('unmerged_index')
        state=read(project,'status','--porcelain=v1','-z','--untracked-files=all')
        if not state:return CommitResult('no_changes',project.alias,exit_code=0)
        started=True
        read(project,'add','-A','--','.')
        # No empty commit (e.g. dirty submodule with no changed gitlink).
        diff=git(project,'diff','--cached','--quiet','--exit-code')
        if diff.returncode==0:
            verification=runtime_effects.compare(before,runtime_effects.capture_commit(project,before),ALLOWED)
            return CommitResult('no_changes' if verification.passed else 'effect_violation',project.alias,
                execution_started=True,exit_code=0 if verification.passed else 1,effect_verification=verification)
        if diff.returncode!=1:raise ValueError('git_operation_failed')
        raw=read(project,'diff','--cached','--name-only','--no-renames','-z').split(b'\0')
        changed=sorted({os.fsdecode(p) for p in raw if p});count=len(changed)
        paths=tuple(execution.bounded_text(p) for p in changed[:MAX_PATHS])
        commit_started=True
        result=git(project,'commit','--no-gpg-sign','--cleanup=verbatim','-m',requested)
        if result.returncode:
            detail=execution.bounded_text(os.fsdecode(result.stderr),512)
            raise ValueError('git_commit_failed')
        head=read(project,'rev-parse','--verify','HEAD').strip().decode('ascii')
        created=head.encode()!=before.commit
        actual_message=read(project,'show','-s','--format=%B','HEAD').decode('utf-8').rstrip('\n')
        after=runtime_effects.capture_commit(project,before)
        verification=runtime_effects.compare(before,after,ALLOWED)
        # Exactly one new child commit, or one root commit in an unborn repo.
        ancestry=read(project,'rev-list','--parents','-n','1','HEAD').strip().decode('ascii').split()
        expected=[head]+([before.commit.decode('ascii')] if before.commit else [])
        if (not created or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',head)
                or ancestry!=expected or after.commit!=head.encode() or actual_message!=requested
                or before.repository!=after.repository or before.git_directory!=after.git_directory
                or before.branch!=after.branch or read(project,'status','--porcelain=v1','-z','--untracked-files=all')):
            raise ValueError('commit_postcondition_failed')
        if not verification.passed:raise ValueError('unauthorized_effect')
        return CommitResult('committed',project.alias,True,head,actual_message,paths,count,
            exit_code=0,execution_started=True,git_commit_started=True,effect_verification=verification)
    except (ValueError,OSError,execution.NattaError,subprocess.SubprocessError,UnicodeError) as exc:
        # Git may have mutated before failing; collect evidence even on failure.
        if before is not None and started:
            try:
                after=runtime_effects.capture_commit(project,before)
                verification=runtime_effects.compare(before,after,ALLOWED)
                if after.commit!=before.commit:
                    created=True;head=after.commit.decode('ascii')
                    actual_message=read(project,'show','-s','--format=%B','HEAD').decode('utf-8').rstrip('\n')
            except Exception:verification=runtime_effects.unavailable('post_execution_verification_failed',committing=True)
        if isinstance(exc,execution.SubmoduleUnsupported):error,detail='submodule_commit_unsupported',str(exc)
        elif isinstance(exc,execution.ExternalFilterConfigured):
            error,detail=exc.code,str(exc)
        else:error=str(exc) if isinstance(exc,ValueError) else 'git_operation_failed'
        return CommitResult('effect_violation' if verification.violations else 'failed',project.alias,
            created if created or not (commit_started and head is None and verification.passed is False) else None,
            head if created else None,actual_message if created else None,paths,count,execution_started=started,
            git_commit_started=commit_started,error=error,detail=detail,effect_verification=verification)


def render(result,project):
    lines=['Commit: '+execution.bounded_text(project.name)]
    if result.status=='committed':lines.append('PASS  '+result.commit_hash[:7]+'  '+execution.bounded_text(result.message,MAX_MESSAGE))
    elif result.status=='no_changes':lines.append('No changes to commit.')
    else:
        lines.append('FAIL  '+(result.error or result.status))
        if result.detail:lines.append('      '+result.detail)
        if result.committed is None:lines.append('Commit outcome unknown; inspect Git state (no rollback).')
        if result.committed:lines.append('Commit created: '+result.commit_hash+' (not rolled back)')
    return '\n'.join(lines)
