"""Read-only capability integration parity. Construction and policy stay explicit.

capabilities.py declares identity and classification. Each layer keeps its own
reviewed declaration; validate() checks every layer agrees with the catalog and
names the first drift it finds. Not every command exists in every layer:
direct-only commands have no routing/policy/handler entries, unvalidated
capabilities have no native mode, and only planner-eligible ones join plans.

Run from tools/natta: python3 -B capability_integration.py
"""
import argparse
import hashlib
import json

import capabilities
import confinement
import parameters
import planner_provider
import policy
import router_codex
import semantic_execution

INTEGRITY = router_codex.SOURCE / 'evaluation/public-controls-integrity.json'
# Marker written by scaffold_capability.py; reviewed descriptions never contain it.
SCAFFOLD_PLACEHOLDER = b'TODO(scaffold)'


def parser_command_ids(parser):
    commands = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return frozenset(commands.choices)


def routing_files():
    return ('config/routing.json', *(name for _, name, *_ in router_codex.EXTENSIONS))


def validate(parser, bindings, direct_handlers, *, commands=None, catalog=None, policies=None,
             definitions=None, pins=None, planner_ids=None, inspection_ids=None,
             workflow_ids=None, unconfined_ids=None, native_mode=None):
    """Raise ValueError naming the first integration drift; return the capability IDs.

    Keyword overrides substitute one layer's declaration, for tests and review.
    """
    commands = capabilities.COMMANDS if commands is None else commands
    catalog = capabilities.CAPABILITIES if catalog is None else catalog
    ids = frozenset(capabilities.capability_ids(catalog))
    policies = policy.CATALOG if policies is None else policies
    if definitions is None:
        try:
            definitions = router_codex.descriptions((*capabilities.capability_ids(catalog), 'no_match'),
                                                    routing_revision=True)
        except ValueError as exc:
            raise ValueError('Routing descriptions differ from the semantic capability universe: ' + str(exc)) from None
    if pins is None:
        pins = json.loads(INTEGRITY.read_text())
    planner_ids = frozenset(planner_provider.CURRENT.ALLOWED if planner_ids is None else planner_ids)
    inspection_ids = confinement.INSPECTION_TARGETS if inspection_ids is None else frozenset(inspection_ids)
    workflow_ids = confinement.WORKFLOW_TARGETS if workflow_ids is None else frozenset(workflow_ids)
    unconfined_ids = confinement.UNCONFINED_TARGETS if unconfined_ids is None else frozenset(unconfined_ids)
    if native_mode is None:
        import macos_confinement
        native_mode = macos_confinement.mode_for

    # CLI identity and direct dispatch.
    if parser_command_ids(parser) != capabilities.command_ids(commands):
        raise ValueError('Parser/catalog command identity mismatch')
    if not ids <= capabilities.command_ids(commands):
        raise ValueError('Capability without a CLI command registration')
    if frozenset(direct_handlers) != capabilities.direct_ids(commands, catalog):
        raise ValueError('Direct dispatch differs from catalog direct commands')
    if any(not callable(handler) for handler in direct_handlers.values()):
        raise ValueError('Direct handler is not callable')
    # Semantic routing and descriptions.
    for name in ('concise', 'precise'):
        values = definitions.get(name) if isinstance(definitions, dict) else None
        if (not isinstance(values, dict) or set(values) != ids | {'no_match'}
                or any(not isinstance(v, str) or not v for v in values.values())):
            raise ValueError('Routing descriptions differ from the semantic capability universe')
    for name in routing_files():
        path = router_codex.SOURCE / name
        if SCAFFOLD_PLACEHOLDER in path.read_bytes():
            raise ValueError('Routing description is an unreviewed scaffold placeholder: ' + name)
        if pins.get(name) != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError('Routing description file is not pinned or changed: ' + name)
    # Handler bindings, policy and effects.
    try:
        semantic_execution.validate_bindings(ids, bindings)
    except ValueError:
        raise ValueError('Handler binding missing or invalid') from None
    try:
        policy.validate_catalog(ids, policies)
    except (TypeError, ValueError):
        raise ValueError('Policy/effect declaration missing or invalid') from None
    for capability in sorted(ids):
        entry = catalog[capability]
        if not policies[capability].effects:
            raise ValueError('Effect declaration missing: ' + capability)
        # Parameter contracts are derived from the real CLI registration.
        try:
            schema = parameters.schema_for(parser, capability)
        except (ValueError, StopIteration):
            raise ValueError('Parameter contract missing or unsupported: ' + capability) from None
        if schema.project_required != (commands[capability].project == 'required'):
            raise ValueError('Parameter contract differs from catalog project requirement: ' + capability)
        if entry.planner and not schema.project_required:
            raise ValueError('Planner-eligible capability must require a project: ' + capability)
        if entry.confinement not in capabilities.CONFINEMENT_MODES:
            raise ValueError('Invalid confinement classification: ' + capability)
        try:
            mode = native_mode(capability)
        except confinement.ConfinementUnavailable:
            mode = None
        if (mode == 'inspection') != (entry.confinement == 'inspection'):
            raise ValueError('Native inspection classification mismatch: ' + capability)
    # Confinement boundary: explicit security declarations, never inferred.
    if inspection_ids != capabilities.confinement_ids('inspection', catalog):
        raise ValueError('Inspection confinement classification mismatch')
    workflows = capabilities.confinement_ids('workflow', catalog)
    commits = capabilities.confinement_ids('commit', catalog)
    if workflow_ids != workflows or unconfined_ids != workflows | commits:
        raise ValueError('Workflow/commit confinement classification mismatch')
    # Planner eligibility: the current frozen revision's domain.
    if planner_ids != capabilities.planner_ids(catalog):
        raise ValueError('Planner eligibility differs from the current planner revision')
    return ids


def doctor_checks(parser, bindings, direct_handlers):
    try:
        validate(parser, bindings, direct_handlers)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return [(False, 'Capability integration parity: ' + str(exc))]
    return [(True, 'Capability integration parity: CLI, dispatch, routing, policy, handlers, '
                   'parameters, confinement and planner agree')]


def main():
    import natta
    ids = validate(natta.make_parser(), natta.handler_bindings(), natta.DIRECT_HANDLERS)
    print('Capability integration parity passed: ' + ', '.join(sorted(ids)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
