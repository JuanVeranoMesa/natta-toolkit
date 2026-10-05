"""Declared capability effects and future authorization. No provider/handler access."""
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType


class CapabilityType(str, Enum):
    ATOMIC = 'atomic'
    WORKFLOW = 'workflow'
    INTERFACE = 'interface'


class Authorization(str, Enum):
    AUTOMATIC = 'automatic'
    EXPLICIT = 'explicit'
    FORBIDDEN = 'forbidden'


class Effect(str, Enum):
    LOCAL_READ = 'local_read'
    PROJECT_READ = 'project_read'
    GIT_READ = 'git_read'
    LOCAL_PROCESS = 'local_process'
    TEMPORARY_WRITE = 'temporary_write'
    PERSISTENT_RUNTIME_WRITE = 'persistent_runtime_write'
    SIMULATOR = 'simulator'
    NETWORK = 'network'
    PROJECT_WRITE = 'project_write'
    GIT_INDEX_WRITE = 'git_index_write'
    GIT_COMMIT = 'git_commit'
    GIT_PUSH = 'git_push'
    EXTERNAL_SERVICE_MUTATION = 'external_service_mutation'


# These effects remain forbidden. Only the reviewed explicit commit entry may
# declare git_index_write/git_commit; other targets cannot acquire that authority.
# The separately reviewed explicit TestFlight entry may use Apple network/service effects.
FORBIDDEN_EFFECTS = frozenset((Effect.PROJECT_WRITE, Effect.GIT_PUSH, Effect.NETWORK, Effect.EXTERNAL_SERVICE_MUTATION))
READ = frozenset((Effect.LOCAL_READ,))
PROJECT_INSPECTION = READ | {Effect.PROJECT_READ, Effect.GIT_READ, Effect.LOCAL_PROCESS}
BUILD = PROJECT_INSPECTION | {Effect.TEMPORARY_WRITE, Effect.PERSISTENT_RUNTIME_WRITE}
TEST = BUILD | {Effect.SIMULATOR}
TESTFLIGHT = BUILD | {Effect.NETWORK, Effect.EXTERNAL_SERVICE_MUTATION}


def forbidden_effects(capability):
    # Only the separately reviewed TestFlight workflow can use Apple services.
    return FORBIDDEN_EFFECTS - ({Effect.NETWORK, Effect.EXTERNAL_SERVICE_MUTATION}
                              if capability == 'testflight' else set())


@dataclass(frozen=True)
class CapabilityPolicy:
    capability_type: CapabilityType
    authorization: Authorization
    effects: frozenset[Effect]
    level_effects: tuple[tuple[int, frozenset[Effect]], ...] = ()


# One policy catalog, bound by validation to real CLI registrations. These are
# conservative upper bounds across current adapters, not execution plans or
# availability claims. GenericGit may perform only a subset (or be unavailable).
CATALOG = MappingProxyType({
    'projects': CapabilityPolicy(CapabilityType.ATOMIC, Authorization.AUTOMATIC, READ),
    'status': CapabilityPolicy(CapabilityType.ATOMIC, Authorization.AUTOMATIC, PROJECT_INSPECTION),
    'context': CapabilityPolicy(CapabilityType.ATOMIC, Authorization.AUTOMATIC, PROJECT_INSPECTION),
    'diff': CapabilityPolicy(CapabilityType.ATOMIC, Authorization.AUTOMATIC, PROJECT_INSPECTION),
    # Local Codex/Xcode metadata probes may initialize caches/runtime files.
    'doctor': CapabilityPolicy(CapabilityType.WORKFLOW, Authorization.AUTOMATIC,
                              PROJECT_INSPECTION | {Effect.PERSISTENT_RUNTIME_WRITE}),
    'build': CapabilityPolicy(CapabilityType.WORKFLOW, Authorization.EXPLICIT, BUILD),
    'test': CapabilityPolicy(CapabilityType.WORKFLOW, Authorization.EXPLICIT, TEST),
    'testflight': CapabilityPolicy(CapabilityType.WORKFLOW, Authorization.EXPLICIT, TESTFLIGHT),
    'commit': CapabilityPolicy(CapabilityType.ATOMIC, Authorization.EXPLICIT,
                              PROJECT_INSPECTION | {Effect.GIT_INDEX_WRITE, Effect.GIT_COMMIT}),
    'verify': CapabilityPolicy(CapabilityType.WORKFLOW, Authorization.EXPLICIT, BUILD,
                              ((1, BUILD), (2, BUILD | TEST), (3, BUILD | TEST))),
})


def validate_catalog(capability_ids, catalog=None):
    catalog = CATALOG if catalog is None else catalog
    if set(catalog) != set(capability_ids):
        raise ValueError('Policy coverage differs from executable routing universe')
    for capability, entry in catalog.items():
        if (not isinstance(entry, CapabilityPolicy) or
                not isinstance(entry.capability_type, CapabilityType) or
                not isinstance(entry.authorization, Authorization) or
                not isinstance(entry.effects, frozenset) or
                any(not isinstance(effect, Effect) for effect in entry.effects) or
                not isinstance(entry.level_effects, tuple)):
            raise ValueError('Invalid typed policy metadata')
        seen = set()
        for level, effects in entry.level_effects:
            if (type(level) is not int or level in seen or not isinstance(effects, frozenset) or
                    any(not isinstance(effect, Effect) for effect in effects)):
                raise ValueError('Invalid level policy metadata')
            seen.add(level)
        declared = entry.effects | frozenset(effect for _, effects in entry.level_effects for effect in effects)
        git_mutations = declared & {Effect.GIT_INDEX_WRITE, Effect.GIT_COMMIT}
        if git_mutations and (capability != 'commit' or entry.authorization != Authorization.EXPLICIT
                or git_mutations != {Effect.GIT_INDEX_WRITE, Effect.GIT_COMMIT}):
            raise ValueError('Only explicit commit may authorize reviewed Git mutation')
        external = declared & {Effect.NETWORK, Effect.EXTERNAL_SERVICE_MUTATION}
        if external and entry.authorization != Authorization.FORBIDDEN and (
                capability != 'testflight' or entry.authorization != Authorization.EXPLICIT
                or declared != TESTFLIGHT):
            raise ValueError('Only explicit TestFlight may authorize reviewed Apple upload effects')
        if declared & forbidden_effects(capability) and entry.authorization != Authorization.FORBIDDEN:
            raise ValueError('Forbidden effects cannot be authorized by current policy')


@dataclass(frozen=True)
class PolicyResult:
    eligible: bool = False
    authorization: Authorization = Authorization.FORBIDDEN
    effects: frozenset[Effect] = frozenset()
    capability_type: CapabilityType | None = None
    reason: str | None = 'not_evaluated'

    def as_dict(self):
        return {'eligible': self.eligible, 'authorization': self.authorization.value,
                'effects': sorted(effect.value for effect in self.effects),
                'capability_type': self.capability_type.value if self.capability_type else None,
                'reason': self.reason}


def evaluate(route, capability_ids, schema=None, projects=(), catalog=None):
    """Classify a locally validated route; eligibility never consumes authorization.

    No request text/provider metadata is accepted. No commands are planned/run.
    Project and parameter completeness is checked defensively from local inputs.
    """
    if route.status == 'no_match' or route.capability == 'no_match':
        return PolicyResult(reason='no_match')
    if route.status != 'matched' or route.error is not None:
        return PolicyResult(reason='routing_error')
    if route.capability not in capability_ids:
        return PolicyResult(reason='unknown_capability')
    catalog = CATALOG if catalog is None else catalog
    try:
        validate_catalog(capability_ids, catalog)
    except (ValueError, TypeError):
        return PolicyResult(reason='policy_configuration_invalid')
    entry = catalog[route.capability]
    if (route.arguments_resolved is not True or route.missing_arguments or
            route.resolution_error is not None):
        return PolicyResult(False, entry.authorization, entry.effects,
                            entry.capability_type, 'unresolved_arguments')
    if schema is None:
        return PolicyResult(reason='invalid_parameters')
    if schema.project_required:
        if route.project not in {project.alias for project in projects}:
            return PolicyResult(reason='invalid_project')
    elif route.project is not None:
        return PolicyResult(reason='invalid_project')
    arguments = dict(route.arguments)
    if len(arguments) != len(route.arguments):
        return PolicyResult(reason='invalid_parameters')
    if schema.level_choices:
        level = arguments.get('level')
        if set(arguments) != {'level'} or type(level) is not int or level not in schema.level_choices:
            return PolicyResult(reason='invalid_parameters')
    elif arguments:
        return PolicyResult(reason='invalid_parameters')
    effects = entry.effects
    if entry.level_effects:
        levels = dict(entry.level_effects)
        if set(levels) != set(schema.level_choices):
            return PolicyResult(reason='policy_configuration_invalid')
        effects = effects | levels[arguments['level']]
    if entry.authorization == Authorization.FORBIDDEN or effects & forbidden_effects(route.capability):
        return PolicyResult(False, Authorization.FORBIDDEN, effects, entry.capability_type, 'forbidden_policy')
    return PolicyResult(True, entry.authorization, effects, entry.capability_type, None)


def doctor_checks(capability_ids):
    try:
        validate_catalog(capability_ids)
    except (ValueError, TypeError):
        return [(False, 'Execution policy metadata invalid or incomplete')]
    return [(True, 'Execution policy coverage, effect/type/authorization enums valid; reporting only')]
