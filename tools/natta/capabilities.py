"""Authoritative CLI command and semantic capability identity. Plain data only.

No handlers, providers, registry or I/O: any module may import this directly.
Each layer keeps its own reviewed declaration (policy effects, handler binding,
routing descriptions, planner revision, confinement boundary);
capability_integration.py checks that those layers agree with this catalog.
"""
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class Command:
    """One CLI page. `project` selects the PROJECT positional: required/optional/none."""
    summary: str
    description: str = ''
    example: str = ''
    project: str = 'none'


@dataclass(frozen=True)
class Capability:
    """Semantic integration classification for one bounded capability.

    planner: eligible as a bounded plan/do step (current planner revision).
    confinement: 'inspection' (validated native worker), 'unvalidated' (no
    validated native mode; semantic execution fails closed), 'workflow'
    (explicit authorization plus observed protected state) or 'commit'.
    direct: also has a direct CLI handler; False would make it semantic-only.
    """
    confinement: str
    planner: bool = False
    direct: bool = True


CONFINEMENT_MODES = frozenset(('inspection', 'unvalidated', 'workflow', 'commit'))

# Every CLI command, in help order. Commands absent from CAPABILITIES are
# direct-only controls/interfaces and are never routable.
COMMANDS = MappingProxyType({
    'route': Command('Propose a capability using the semantic router; never execute',
        'Bounded semantic capability selection using the configured semantic model '
        '(gpt-6-luna). Natta validates the result and never executes it. Projects and '
        'bounded arguments are resolved locally from the registry and CLI schema. Sends '
        'only the request and routing definitions; no repository inspection.',
        'natta route "Build Example for the simulator"'),
    'codex': Command('Launch Codex at the toolkit checkout or a registered project',
        "Launch the interactive Codex CLI from the toolkit checkout root, or from a "
        "registered project's repository when PROJECT is supplied. Uses Codex's normal "
        "instruction discovery; no prompt is injected.", 'natta codex', 'optional'),
    'projects': Command('List registered projects',
        'List registered projects with alias, display name, adapter type, and repository path.',
        'natta projects'),
    'status': Command('Inspect project and Git status',
        'Deterministic, read-only inspection of branch, working-tree status, version/build, '
        'latest commit, and canonical document paths.', 'natta status example', 'required'),
    'context': Command('Show compact project orientation',
        'Compact orientation/context summary for humans and future provider launchers. '
        'Currently shares status fields and rendering; it does not invoke AI or read '
        'document contents.', 'natta context sample', 'required'),
    'diff': Command('Inspect changed paths and Git differences',
        'Read-only Git/project difference inspection: changed paths and staged/unstaged '
        'summaries. Does not stage, reset, clean, or commit.', 'natta diff example', 'required'),
    'doctor': Command('Diagnose environment and project health',
        'Diagnostic-only core, provider and integration checks, including the registry, '
        'adapters, Xcode availability, and container/scheme metadata. Does not build, test, '
        'verify, install, or repair; simulator availability is checked during test discovery.',
        'natta doctor'),
    'build': Command('Run a registered development build',
        'Run the registered deterministic development build. No AI chooses build commands. '
        'Uses external temporary build state and reports failures without fixing them.',
        'natta build example', 'required'),
    'test': Command('Run a registered normal test workflow',
        'Run the registered deterministic normal test workflow. Simulator or external '
        'prerequisites may be unavailable. Uses external temporary test state; no automatic fixes.',
        'natta test sample', 'required'),
    'testflight': Command('Archive and upload a build for TestFlight',
        'Archive a Release build and upload it to App Store Connect for TestFlight. Requires '
        'explicit confirmation; never submits App Review or releases publicly. --check '
        'validates local prerequisites only.', 'natta testflight example', 'required'),
    'commit': Command('Commit all project changes locally',
        'Stage all current changes inside the registered repository and create one local '
        'commit. No push, hooks, signing or rollback.', 'natta commit example', 'required'),
    'verify': Command('Run project-specific validation by level',
        'Run a registered deterministic verification workflow. Exact checks are '
        'project-specific; missing external prerequisites may produce unavailable/skipped '
        'states. No automatic fixes. Level 3 is not physical-device or release certification.',
        'natta verify example --level 2', 'required'),
    'execute': Command('Execute one resolved route after local authorization'),
    'plan': Command('Propose a bounded experimental plan; never execute'),
    'do': Command('Execute one bounded plan after whole-plan authorization'),
    'confinement': Command('Validate native confinement without the semantic model'),
})

# The bounded semantic universe, in routing candidate order. Append new
# capabilities; the evaluated order of existing ones is preserved.
CAPABILITIES = MappingProxyType({
    'projects': Capability('unvalidated'),
    'status': Capability('inspection', planner=True),
    'context': Capability('inspection', planner=True),
    'diff': Capability('inspection', planner=True),
    'doctor': Capability('unvalidated'),
    'build': Capability('workflow', planner=True),
    'test': Capability('workflow', planner=True),
    'verify': Capability('workflow', planner=True),
    'commit': Capability('commit', planner=True),
    'testflight': Capability('workflow', planner=True),
})


def command_ids(commands=None):
    return frozenset(COMMANDS if commands is None else commands)


def capability_ids(capabilities=None):
    """Ordered semantic capability IDs: routing, policy and handler universe."""
    return tuple(CAPABILITIES if capabilities is None else capabilities)


def direct_ids(commands=None, capabilities=None):
    """Commands with a direct CLI handler (everything except semantic-only capabilities)."""
    capabilities = CAPABILITIES if capabilities is None else capabilities
    return command_ids(commands) - {name for name, c in capabilities.items() if not c.direct}


def planner_ids(capabilities=None):
    capabilities = CAPABILITIES if capabilities is None else capabilities
    return frozenset(name for name, c in capabilities.items() if c.planner)


def confinement_ids(mode, capabilities=None):
    capabilities = CAPABILITIES if capabilities is None else capabilities
    if mode not in CONFINEMENT_MODES:
        raise ValueError('Unknown confinement classification')
    return frozenset(name for name, c in capabilities.items() if c.confinement == mode)
