"""Native inspection confinement; workflows use explicit auth and observation.

Primitive presence alone never proves enforcement. Workflow policy declarations
are not OS restrictions; runtime snapshots cover only observable project/Git state.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import platform
import sys

import policy


class Support(str, Enum):
    ENFORCED = 'enforced'
    PARTIAL = 'partial'
    UNAVAILABLE = 'unavailable'
    NOT_APPLICABLE = 'not_applicable'


class ConfinementUnavailable(Exception):
    pass


class ConfinementSetupFailed(Exception):
    pass


@dataclass(frozen=True)
class Invocation:
    started: bool
    outcome: object
    report: str = ''
    report_truncated: bool = False
    error: str | None = None


class BackendSession:
    """Backend must execute the handler remotely; never an in-process sandbox."""
    result: object

    def invoke(self, projects, project, arguments):
        raise NotImplementedError


@dataclass(frozen=True)
class HostCapabilities:
    platform: str
    version: str
    native_primitive_present: bool
    backend: str | None = None
    filesystem: Support = Support.UNAVAILABLE
    network: Support = Support.UNAVAILABLE
    process_tree: Support = Support.UNAVAILABLE
    reason: str = 'native_backend_unvalidated'

    def as_dict(self):
        return {'platform': self.platform, 'version': self.version,
                'native_primitive_present': self.native_primitive_present,
                'backend': self.backend, 'filesystem': self.filesystem.value,
                'network': self.network.value, 'process_tree': self.process_tree.value,
                'reason': self.reason}


def detect_host():
    """Structural only: no sandbox self-test, Xcode, network or model usage."""
    mac = sys.platform == 'darwin'
    present = mac and Path('/usr/bin/sandbox-exec').is_file()
    reason = ('unsupported_platform' if not mac else
              'native_backend_unvalidated' if present else 'native_primitive_missing')
    return HostCapabilities(sys.platform, platform.mac_ver()[0] if mac else '', present, reason=reason)


@dataclass(frozen=True)
class Plan:
    capability: str
    host: HostCapabilities
    required_effects: frozenset[policy.Effect]
    project_root: str | None
    writable_roots: tuple[str, ...] = ()
    project: object = None

    @property
    def available(self):
        from macos_confinement import available
        return available(self,self.project)[0]


@dataclass(frozen=True)
class ConfinementResult:
    required: bool = False
    applied: bool = False
    mode: str | None = 'not_started'
    required_effects: frozenset[policy.Effect] = frozenset()
    enforced_effects: frozenset[policy.Effect] = frozenset()
    observation_only_effects: frozenset[policy.Effect] = frozenset()
    unenforced_effects: frozenset[policy.Effect] = frozenset()
    process_control: Support = Support.NOT_APPLICABLE
    reason: str | None = None
    backend: str | None = None
    validated: bool = False
    policy_denied_effects: frozenset[policy.Effect] = frozenset()

    def as_dict(self):
        return {'required': self.required, 'applied': self.applied, 'mode': self.mode,
                'backend': self.backend, 'validated': self.validated,
                'policy_denied_effects': sorted(e.value for e in self.policy_denied_effects),
                'required_effects': sorted(e.value for e in self.required_effects),
                'enforced_effects': sorted(e.value for e in self.enforced_effects),
                'observation_only_effects': sorted(e.value for e in self.observation_only_effects),
                'unenforced_effects': sorted(e.value for e in self.unenforced_effects),
                'process_control': self.process_control.value, 'reason': self.reason}


# Explicit reviewed execution boundary, independent of capability type/effects.
# Future workflows are not exempted automatically.
WORKFLOW_TARGETS = frozenset(('build','test','verify','testflight'))
UNCONFINED_TARGETS = WORKFLOW_TARGETS | {'commit'}
DENIABLE_EFFECTS = policy.FORBIDDEN_EFFECTS | {policy.Effect.GIT_INDEX_WRITE,policy.Effect.GIT_COMMIT}
OBSERVABLE_DENIED = frozenset((policy.Effect.PROJECT_WRITE,policy.Effect.GIT_INDEX_WRITE,policy.Effect.GIT_COMMIT))


def workflow_result(decision, *, committing=False):
    denied=DENIABLE_EFFECTS - decision.effects
    return ConfinementResult(required=False,mode=None,reason='not_required_for_commit' if committing else 'not_required_for_workflow',
        policy_denied_effects=denied,observation_only_effects=denied & OBSERVABLE_DENIED,
        unenforced_effects=denied - OBSERVABLE_DENIED)


def plan_for(capability, arguments, project, decision):
    """Native enforcement requirements from trusted capability/registry metadata."""
    if capability in UNCONFINED_TARGETS:
        raise ValueError('Native confinement is not required for local workflows')
    if capability not in policy.CATALOG or not isinstance(decision, policy.PolicyResult) or not decision.eligible:
        raise ValueError('Invalid confinement target')
    if not isinstance(arguments, tuple) or any(k != 'level' or type(v) is not int or v not in (1,2,3) for k,v in arguments):
        raise ValueError('Invalid confinement parameters')
    required = {policy.Effect.NETWORK} - decision.effects
    root = None
    if project is not None:
        path = project.path
        if not isinstance(path, Path) or not path.is_absolute() or path.resolve() != path or any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError('Unsafe confinement project path')
        root = str(path)
        required.update({policy.Effect.PROJECT_WRITE, policy.Effect.GIT_INDEX_WRITE,
                         policy.Effect.GIT_COMMIT} - decision.effects)
    return Plan(capability, detect_host(), frozenset(required), root, project=project)


def unavailable_result(plan, reason=None):
    observable = {policy.Effect.PROJECT_WRITE, policy.Effect.GIT_INDEX_WRITE, policy.Effect.GIT_COMMIT}
    return ConfinementResult(True, False, 'unavailable', plan.required_effects,
                             observation_only_effects=frozenset(observable) if plan.project_root else frozenset(),
                             unenforced_effects=DENIABLE_EFFECTS,
                             process_control=Support.PARTIAL,
                             reason=reason or plan.host.reason,policy_denied_effects=DENIABLE_EFFECTS)


@contextmanager
def open_session(plan):
    from macos_confinement import open_session as native_session
    with native_session(plan) as session:
        yield session


def doctor_checks(projects=()):
    from macos_confinement import validation_state
    host=detect_host()
    inspection,reason=validation_state('inspection')
    checks = [(host.native_primitive_present, 'Native sandbox-exec primitive present (deprecated)'),
            (True, 'macOS sandbox worker backend implemented; no doctor self-tests'),
            (inspection, 'Inspection confinement validation: '+('current' if inspection else str(reason))),
            ]
    checks.append((False, 'Semantic projects/doctor: no validated confinement mode; direct commands available'))
    checks.append((True, 'Xcode native confinement: deferred research / not required for build/test/verify'))
    return checks
