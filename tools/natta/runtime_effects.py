"""Protected observable state only. Not a filesystem/network sandbox or rollback."""
from dataclasses import dataclass
import execution
from policy import Effect

VERIFIABLE = frozenset((Effect.PROJECT_WRITE, Effect.GIT_INDEX_WRITE, Effect.GIT_COMMIT))
COMMIT_SCOPE = ('bounded_worktree_contents', 'git_index', 'git_head', 'repository_identity')
SCOPE = ('tracked_worktree_diff', 'untracked_path_set', 'git_index', 'git_head', 'repository_identity')


@dataclass(frozen=True)
class VerificationResult:
    performed: bool = False
    passed: bool | None = None
    observed_effects: frozenset[Effect] = frozenset()
    violations: frozenset[Effect] = frozenset()
    scope: tuple[str, ...] = ()
    reason: str | None = 'not_started'

    def as_dict(self):
        return {'performed': self.performed, 'passed': self.passed,
                'observed_effects': sorted(e.value for e in self.observed_effects),
                'violations': sorted(e.value for e in self.violations),
                'scope': list(self.scope), 'reason': self.reason}


def capture(project, before=None):
    return execution.snapshot(project.path, protected=True,
                              baseline=before.baseline if before else None)


def capture_commit(project, before=None):
    return execution.protected_snapshot(project.path,
        baseline=before.baseline if before else None,
        content_paths=tuple(p for p,_ in before.worktree_contents) if before else ())


def compare(before, after, allowed):
    if (not isinstance(before, execution.ProtectedSnapshot) or
            not isinstance(after, execution.ProtectedSnapshot) or before.baseline != after.baseline or
            any(not isinstance(effect, Effect) for effect in allowed)):
        raise ValueError('Invalid protected evidence/policy')
    observed = set()
    committing = Effect.GIT_COMMIT in allowed and Effect.GIT_INDEX_WRITE in allowed
    if committing and before.worktree_contents is not None and after.worktree_contents is not None:
        if before.worktree_contents != after.worktree_contents:observed.add(Effect.PROJECT_WRITE)
    elif before.project_state != after.project_state or before.untracked != after.untracked:
        observed.add(Effect.PROJECT_WRITE)
    if before.index != after.index or before.index_entries != after.index_entries:
        observed.add(Effect.GIT_INDEX_WRITE)
    if before.commit != after.commit or before.branch != after.branch:
        observed.add(Effect.GIT_COMMIT)
    # Repository redirection conservatively violates all protected mutation axes.
    if before.repository != after.repository or before.git_directory != after.git_directory:
        observed.update(VERIFIABLE)
    observed = frozenset(observed)
    violations = observed - frozenset(allowed)
    if before.branch != after.branch or before.repository != after.repository or before.git_directory != after.git_directory:
        violations = violations | {Effect.PROJECT_WRITE}
    scope = COMMIT_SCOPE if committing and before.worktree_contents is not None and after.worktree_contents is not None else SCOPE
    return VerificationResult(True, not violations, observed, violations, scope,
                              'unauthorized_effect' if violations else None)


def unavailable(reason, *, committing=False):
    return VerificationResult(True, False, scope=COMMIT_SCOPE if committing else SCOPE, reason=reason)


def doctor_checks():
    valid = (callable(execution.snapshot) and callable(execution.protected_snapshot) and
             all(isinstance(effect, Effect) for effect in VERIFIABLE) and
             VERIFIABLE == frozenset((Effect.PROJECT_WRITE, Effect.GIT_INDEX_WRITE, Effect.GIT_COMMIT)))
    valid = valid and callable(capture_commit)
    return [(valid, 'Runtime protected-state verifier uses valid policy effects; local Git scope only')]
