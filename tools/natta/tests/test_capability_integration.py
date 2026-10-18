"""Capability identity and mechanical integration parity (NTT-005). No providers."""
import contextlib
from dataclasses import replace
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import capabilities
import capability_integration as integration
import confinement
import natta
import planner_provider
import policy
import router_codex
import semantic_execution as gate


def handler(*args, **kwargs):
    return gate.HandlerOutcome(0)


class CapabilityIntegrationTests(unittest.TestCase):
    def validate(self, *, parser=None, bindings=None, direct=None, **overrides):
        return integration.validate(parser or natta.make_parser(), natta.handler_bindings() if bindings is None else bindings,
                                    natta.DIRECT_HANDLERS if direct is None else direct, **overrides)

    def assertDrift(self, message, **kwargs):
        with self.assertRaisesRegex(ValueError, message):
            self.validate(**kwargs)

    @contextlib.contextmanager
    def extended(self, name, command, capability=None):
        """Temporarily add one command (and optionally a capability) to the catalog."""
        commands = {**capabilities.COMMANDS, name: command}
        catalog = dict(capabilities.CAPABILITIES)
        if capability is not None:
            catalog[name] = capability
        with patch.object(capabilities, 'COMMANDS', commands), patch.object(capabilities, 'CAPABILITIES', catalog):
            yield natta.make_parser()

    def test_current_toolkit_integrates_completely(self):
        ids = self.validate()
        self.assertEqual(ids, frozenset(capabilities.capability_ids()))
        self.assertEqual(set(policy.CATALOG), set(ids))
        self.assertEqual(set(natta.handler_bindings()), set(ids))
        self.assertEqual(set(planner_provider.CURRENT.ALLOWED), capabilities.planner_ids())
        self.assertEqual(confinement.WORKFLOW_TARGETS, capabilities.confinement_ids('workflow'))
        self.assertEqual(confinement.INSPECTION_TARGETS, capabilities.confinement_ids('inspection'))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(integration.main(), 0)
        self.assertIn('parity passed', out.getvalue())

    def test_catalog_order_appends_to_last_evaluated_routing_universe(self):
        evaluated = router_codex.TOOLKIT_IDS[:-1]
        self.assertEqual(router_codex.current_ids()[:len(evaluated)], evaluated)
        self.assertEqual(router_codex.current_ids()[-1], 'no_match')

    def test_missing_handler_is_detected(self):
        bindings = dict(natta.handler_bindings()); bindings.pop('build')
        self.assertDrift('Handler binding', bindings=bindings)
        self.assertDrift('Handler binding', bindings={**natta.handler_bindings(), 'diff': None})

    def test_missing_policy_or_effects_is_detected(self):
        policies = dict(policy.CATALOG); policies.pop('diff')
        self.assertDrift('Policy', policies=policies)
        empty = {**policy.CATALOG, 'status': replace(policy.CATALOG['status'], effects=frozenset())}
        self.assertDrift('Effect declaration missing: status', policies=empty)

    def test_semantic_routing_drift_is_detected(self):
        # A new catalog capability with no reviewed routing description.
        with self.extended('lint', capabilities.Command('Lint', 'Lint', 'natta lint example', 'required'),
                           capabilities.Capability('workflow')) as parser:
            direct = {**natta.DIRECT_HANDLERS, 'lint': handler}
            self.assertDrift('Routing descriptions differ', parser=parser, direct=direct)
            # Production routing fails closed on the same drift.
            with self.assertRaisesRegex(ValueError, 'Routing universe differs'):
                router_codex.definitions()
        definitions = router_codex.descriptions(router_codex.current_ids(), routing_revision=True)
        broken = {**definitions, 'concise': {k: v for k, v in definitions['concise'].items() if k != 'commit'}}
        self.assertDrift('Routing descriptions differ', definitions=broken)

    def test_routing_description_file_drift_is_detected(self):
        pins = json.loads(integration.INTEGRITY.read_text())
        for name in integration.routing_files():
            with self.subTest(name=name):
                self.assertDrift('not pinned or changed: ' + name, pins={**pins, name: '0' * 64})
                self.assertDrift('not pinned or changed: ' + name, pins={k: v for k, v in pins.items() if k != name})

    def test_direct_dispatch_drift_is_detected(self):
        direct = dict(natta.DIRECT_HANDLERS); direct.pop('diff')
        self.assertDrift('Direct dispatch', direct=direct)
        self.assertDrift('Direct dispatch', direct={**natta.DIRECT_HANDLERS, 'phantom': handler})
        self.assertDrift('not callable', direct={**natta.DIRECT_HANDLERS, 'diff': None})
        parser = natta.make_parser()
        commands = next(a for a in parser._actions if a.dest == 'command')
        commands.add_parser('phantom')
        self.assertDrift('Parser/catalog command identity', parser=parser)

    def test_confinement_classification_drift_is_detected(self):
        self.assertDrift('Inspection confinement', inspection_ids={'status', 'context'})
        self.assertDrift('Workflow/commit confinement', workflow_ids=confinement.WORKFLOW_TARGETS - {'testflight'})
        self.assertDrift('Workflow/commit confinement', workflow_ids=confinement.WORKFLOW_TARGETS | {'commit'})
        self.assertDrift('Workflow/commit confinement', unconfined_ids=confinement.WORKFLOW_TARGETS)
        def widened(capability):
            return 'inspection' if capability in ('status', 'context', 'diff', 'build') else natta_mode(capability)
        import macos_confinement
        natta_mode = macos_confinement.mode_for
        self.assertDrift('Native inspection classification mismatch: build', native_mode=widened)
        catalog = {**capabilities.CAPABILITIES, 'commit': capabilities.Capability('workflow', planner=True)}
        with patch.object(capabilities, 'CAPABILITIES', catalog):
            self.assertDrift('Workflow/commit confinement')

    def test_planner_eligibility_drift_is_detected(self):
        self.assertDrift('Planner eligibility', planner_ids=set(planner_provider.CURRENT.ALLOWED) - {'testflight'})
        self.assertDrift('Planner eligibility', planner_ids=planner_provider.COMMIT_V2.ALLOWED)
        catalog = {**capabilities.CAPABILITIES, 'projects': capabilities.Capability('unvalidated', planner=True)}
        with patch.object(capabilities, 'CAPABILITIES', catalog):
            self.assertDrift('Planner-eligible capability must require a project: projects')

    def test_parameter_contract_drift_is_detected(self):
        commands = {**capabilities.COMMANDS, 'status': replace(capabilities.COMMANDS['status'], project='none')}
        with patch.object(capabilities, 'COMMANDS', commands):
            parser = natta.make_parser()
        self.assertDrift('Parameter contract differs', parser=parser)

    def test_intentional_direct_only_command_needs_no_semantic_layers(self):
        with self.extended('lint', capabilities.Command('Lint', 'Lint', 'natta lint example', 'required')) as parser:
            ids = self.validate(parser=parser, direct={**natta.DIRECT_HANDLERS, 'lint': handler})
        self.assertNotIn('lint', ids)
        # Existing direct-only commands are never routable or bound.
        for command in ('codex', 'route', 'execute', 'plan', 'do', 'confinement'):
            self.assertIn(command, natta.DIRECT_HANDLERS)
            self.assertNotIn(command, router_codex.authoritative_ids())
            self.assertNotIn(command, policy.CATALOG)

    def test_intentional_route_only_and_semantic_only_capabilities(self):
        # projects/doctor: routable and bound, but not planner-eligible and no native mode.
        for capability in ('projects', 'doctor'):
            self.assertIn(capability, router_codex.authoritative_ids())
            self.assertNotIn(capability, planner_provider.CURRENT.ALLOWED)
            self.assertEqual(capabilities.CAPABILITIES[capability].confinement, 'unvalidated')
        # A semantic-only capability has policy/handler/routing but no direct handler.
        definitions = router_codex.descriptions(router_codex.current_ids(), routing_revision=True)
        definitions = {k: {**v, 'lint': 'Lint a registered project.'} for k, v in definitions.items() if k != 'schema_version'}
        with self.extended('lint', capabilities.Command('Lint', 'Lint', 'natta lint example', 'required'),
                           capabilities.Capability('workflow', direct=False)) as parser:
            layers = dict(parser=parser, bindings={**natta.handler_bindings(), 'lint': handler},
                          policies={**policy.CATALOG, 'lint': policy.CATALOG['build']}, definitions=definitions,
                          workflow_ids=confinement.WORKFLOW_TARGETS | {'lint'},
                          unconfined_ids=confinement.UNCONFINED_TARGETS | {'lint'})
            self.assertIn('lint', self.validate(**layers))
            self.assertDrift('Direct dispatch', direct={**natta.DIRECT_HANDLERS, 'lint': handler}, **layers)
            # The direct CLI path fails closed for a semantic-only capability.
            err = io.StringIO()
            with contextlib.redirect_stderr(err), patch.object(natta, 'make_parser', return_value=parser):
                self.assertEqual(natta.main(['lint', 'example']), 1)
            self.assertIn("no direct handler for command 'lint'", err.getvalue())

    def test_capability_identity_needs_no_source_parsing(self):
        self.assertNotIn('ast', vars(router_codex))
        real = Path.read_text
        def guarded(path, *args, **kwargs):
            if Path(path).name == 'natta.py':
                raise AssertionError('natta.py source read for capability identity')
            return real(path, *args, **kwargs)
        with patch.object(Path, 'read_text', guarded):
            self.assertEqual(router_codex.authoritative_ids(), frozenset(capabilities.capability_ids()))
            router_codex.definitions()
        probe = subprocess.run([sys.executable, '-B', '-c', 'import capabilities, router_codex, parameters, sys; '
                                'print(sorted(router_codex.authoritative_ids())); print("natta" in sys.modules)'],
                               cwd=natta.ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertEqual(probe.stdout.splitlines()[1], 'False')

    def test_doctor_reports_integration_drift(self):
        bindings = dict(natta.handler_bindings()); bindings.pop('commit')
        checks = integration.doctor_checks(natta.make_parser(), bindings, natta.DIRECT_HANDLERS)
        self.assertEqual(checks, [(False, 'Capability integration parity: Handler binding missing or invalid')])
        self.assertTrue(integration.doctor_checks(natta.make_parser(), natta.handler_bindings(), natta.DIRECT_HANDLERS)[0][0])

    def test_planner_revisions_are_entries_not_modules(self):
        self.assertFalse((natta.ROOT / 'planner_provider_v2.py').exists())
        self.assertFalse((natta.ROOT / 'planner_provider_v3.py').exists())
        self.assertIs(planner_provider.CURRENT, planner_provider.TESTFLIGHT_V1)
        self.assertEqual([planner_provider.VERSION, planner_provider.COMMIT_V2.VERSION, planner_provider.CURRENT.VERSION],
                         ['phase8a-v1', 'phase8a-commit-v2', 'testflight-planner-v1'])


if __name__ == '__main__':
    unittest.main()
