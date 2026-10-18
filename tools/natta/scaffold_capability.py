"""Contributor scaffold for a new capability (docs/EXTENDING.md). Development only.

Writes exactly two new files and never edits existing source:
  config/routing-<name>-v1.json   reviewed routing description (placeholder text)
  tests/test_capability_<name>.py failing test skeleton
then prints the remaining reviewed edits, with snippets, in checklist order.
Placeholders carry PLACEHOLDER; capability_integration rejects a routing file
that still contains it, and the test skeleton fails until it is written, so an
unfinished scaffold cannot pass parity or the suite.

Run from tools/natta: python3 -B scaffold_capability.py NAME --kind inspection
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

import capabilities

PLACEHOLDER = 'TODO(scaffold)'
SOURCE = Path(__file__).resolve().parent
KINDS = {
    # kind: (policy type, authorization, effects, confinement set, default project)
    'inspection': ('ATOMIC', 'AUTOMATIC', 'PROJECT_INSPECTION', 'INSPECTION_TARGETS', 'required'),
    'workflow': ('WORKFLOW', 'EXPLICIT', 'BUILD', 'WORKFLOW_TARGETS', 'required'),
    'unvalidated': ('ATOMIC', 'AUTOMATIC', 'READ', None, 'none'),
}
NAME = re.compile(r'[a-z][a-z0-9_]{0,31}')

TEST_TEMPLATE = '''"""{name}: handler, policy, authorization and direct-command tests (docs/EXTENDING.md step 12)."""
import unittest


class {cls}CapabilityTests(unittest.TestCase):
    def test_{name}_is_implemented(self):
        # Replace with real tests in disposable repositories with mocked external
        # tools: handler outcome, declared effects, {auth} authorization, direct
        # command, confinement classification{planner}. Never call real providers.
        self.fail('{placeholder}: write the {name} capability tests')


if __name__ == '__main__':
    unittest.main()
'''


def plan(name, kind, project, planner):
    ptype, auth, effects, targets, _ = KINDS[kind]
    routing = f'config/routing-{name}-v1.json'
    steps = [
        ('capabilities.py: COMMANDS (help order) and append to CAPABILITIES',
         f"    '{name}': Command('<summary>', '<description>', 'natta {name}{' example' if project == 'required' else ''}', '{project}'),\n"
         f"    '{name}': Capability('{kind}'{', planner=True' if planner else ''}),"),
        ('natta.py: deterministic handler, direct entry point, DIRECT_HANDLERS and handler_bindings()',
         f"def handle_{name}(projects, project, arguments):\n    ...  # one explainable outcome; argv tuples only\n"
         f"    return semantic_execution.HandlerOutcome(0)\n\n"
         f"def run_{name}(args, parser):\n    projects = load_registry(args.registry)\n"
         f"    return handle_{name}(projects, {'lookup(projects, args.project)' if project == 'required' else 'None'}, ()).exit_code\n\n"
         f"    '{name}': run_{name},          # DIRECT_HANDLERS\n    '{name}': handle_{name},       # handler_bindings()"),
        ('policy.py: CATALOG (conservative upper bound of effects)',
         f"    '{name}': CapabilityPolicy(CapabilityType.{ptype}, Authorization.{auth}, {effects}),"),
        ('router_codex.py: append to EXTENSIONS, after writing the description in ' + routing,
         f"    ('{name}', '{routing}', 1, ()),"),
        ('evaluation/public-controls-integrity.json: pin the reviewed description (recompute after editing it)',
         f'  "{routing}": "<sha256 of the reviewed file>",'),
    ]
    if targets:
        note = (' The inspection fingerprint changes: update inspection_compatibility.json and re-run '
                '`natta confinement validate`.' if kind == 'inspection' else '')
        steps.append((f'confinement.py: add to {targets}.{note}', f"{targets} = frozenset((..., '{name}'))"))
    if auth == 'EXPLICIT':
        steps.append(('authorization.action_notice / compound_execution.authorization_notice: state the consequence '
                      'if approval needs one', None))
    if planner:
        steps.append(('planner_provider.py: new Revision entry (previous allowed IDs + this one), point CURRENT at it, '
                      'add frozen corpus/controls under evaluation/ and pin them', None))
    steps += [('tests/test_capability_' + name + '.py: replace the failing skeleton; README test count', None),
              ('docs: README capability table, the relevant tools/natta/docs contract and RESULT_CONTRACT.md', None),
              ('check: python3 -B capability_integration.py && python3 -B -m unittest discover -s tests', None)]
    return steps


def scaffold(name, kind, *, project=None, planner=False, root=SOURCE, out=sys.stdout):
    if not NAME.fullmatch(name):
        raise SystemExit(f'Invalid capability name {name!r}: lowercase letter then letters, digits or _')
    if name in capabilities.COMMANDS:
        raise SystemExit(f'{name!r} is already a command')
    project = project or KINDS[kind][4]
    if planner and project != 'required':
        raise SystemExit('Only capabilities that require a project may be planner-eligible')
    routing = root / f'config/routing-{name}-v1.json'
    test = root / f'tests/test_capability_{name}.py'
    existing = [str(p) for p in (routing, test) if p.exists()]
    if existing:
        raise SystemExit('Refusing to overwrite: ' + ', '.join(existing))
    description = (f'{PLACEHOLDER}: describe exactly what {name} does, what it never does, and which nearby '
                   'requests belong to other capabilities or no_match.')
    routing.write_text(json.dumps({'schema_version': 1, 'capability': name, 'description': description},
                                  indent=2) + '\n')
    test.write_text(TEST_TEMPLATE.format(name=name, cls=''.join(p.title() for p in name.split('_')),
                                         auth=KINDS[kind][1].lower(), placeholder=PLACEHOLDER,
                                         planner=', planner gold plans' if planner else ''))
    print(f'Wrote {routing.relative_to(root)} and {test.relative_to(root)}. Remaining reviewed edits:', file=out)
    for number, (title, snippet) in enumerate(plan(name, kind, project, planner), 1):
        print(f'\n{number}. {title}', file=out)
        if snippet:
            print('\n'.join('     ' + line if line else '' for line in snippet.splitlines()), file=out)
    print(f'\nCurrent sha256 of the placeholder description: {hashlib.sha256(routing.read_bytes()).hexdigest()}'
          ' (do not pin it; pin the reviewed text)', file=out)
    return routing, test


def main(argv=None):
    parser = argparse.ArgumentParser(prog='scaffold_capability.py', description=__doc__.split('\n\n')[0])
    parser.add_argument('name', metavar='NAME')
    parser.add_argument('--kind', required=True, choices=sorted(KINDS),
                        help='Confinement classification; selects policy and confinement defaults')
    parser.add_argument('--project', choices=('required', 'optional', 'none'),
                        help='PROJECT positional (default: required, or none for unvalidated)')
    parser.add_argument('--planner', action='store_true', help='Planner-eligible (requires a project)')
    args = parser.parse_args(argv)
    scaffold(args.name, args.kind, project=args.project, planner=args.planner)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
