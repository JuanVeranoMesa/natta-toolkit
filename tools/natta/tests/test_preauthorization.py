"""Semantic --confirm covers only a known action (NTT-004). Mocked provider/handlers."""
import io
import json
import unittest
from unittest.mock import patch

import authorization
import natta
import routing
import router_codex as provider
import runtime_effects
import semantic_execution as gate
import test_authorization as terminal
import test_semantic_execution as fixtures


class ExpectedCapabilityTests(unittest.TestCase):
    setUp = fixtures.SemanticExecutionTests.setUp
    bindings = fixtures.SemanticExecutionTests.bindings
    route = fixtures.SemanticExecutionTests.route

    def execute(self, request, choice, *flags, json_mode=True, tty=False, response='y\n'):
        """One `natta execute`; returns code, output, handlers and the provider call count."""
        handlers = self.bindings()
        stdin = terminal.Terminal(response) if tty else io.StringIO(response)
        stdout = terminal.Terminal() if tty else io.StringIO()
        args = ['--registry', str(self.registry), 'execute', request, *flags] + (['--json'] if json_mode else [])
        with patch.object(provider, 'run_provider', return_value=json.dumps({'choice': choice})) as run, \
             patch.object(natta, 'handler_bindings', return_value=handlers), \
             patch.object(runtime_effects, 'capture_commit', return_value=gate.runtime_effects.capture.return_value), \
             patch('sys.stdin', stdin), patch('sys.stdout', stdout):
            code = natta.main(args)
        output = json.loads(stdout.getvalue()) if json_mode else stdout.getvalue()
        return code, output, handlers, run.call_count, stdin

    def test_without_confirm_tty_prompts_after_routing(self):
        code, out, handlers, calls, _ = self.execute('Build Example', 'build', json_mode=False, tty=True)
        self.assertEqual((code, calls), (0, 1))
        self.assertIn('Continue? [y/N]:', out)
        self.assertEqual(handlers['build'].call_count, 1)

    def test_expect_without_confirm_still_prompts_and_guards(self):
        code, out, handlers, calls, _ = self.execute('Build Example', 'build', '--expect', 'build', json_mode=False, tty=True)
        self.assertEqual((code, calls), (0, 1))
        self.assertEqual(out.count('Continue? [y/N]:'), 1)
        code, out, handlers, calls, _ = self.execute('Build Example', 'commit', '--expect', 'build', json_mode=False, tty=True)
        self.assertEqual((code, calls), (1, 1))
        self.assertNotIn('Continue?', out)
        self.assertIn('expected_capability_mismatch', out)
        self.assertFalse(any(h.called for h in handlers.values()))

    def test_automatic_route_needs_no_approval_or_expectation(self):
        with patch.object(authorization, 'request_approval', side_effect=AssertionError('prompt')):
            for flags in ((), ('--expect', 'status')):
                code, data, handlers, calls, _ = self.execute('Status Example', 'status', *flags)
                self.assertEqual((code, data['status'], calls), (0, 'executed', 1))
                self.assertEqual(data['authorization'], 'automatic')
                self.assertEqual(handlers['status'].call_count, 1)

    def test_valid_expectation_preauthorizes_without_prompt(self):
        with patch.object(authorization, 'request_approval', side_effect=AssertionError('prompt')):
            for request, cap in (('Build Example', 'build'), ('Commit Example', 'commit'),
                                 ('Upload Example to TestFlight', 'testflight'), ('Verify Sample at level 2', 'verify')):
                code, data, handlers, calls, stdin = self.execute(request, cap, '--confirm', '--expect', cap)
                self.assertEqual((code, data['status'], calls), (0, 'executed', 1))
                self.assertTrue(data['authorization_satisfied'])
                self.assertEqual(data['expected_capability'], cap)
                self.assertEqual(handlers[cap].call_count, 1)
                self.assertEqual(stdin.tell(), 0)

    def test_mismatch_fails_before_any_handler_snapshot_or_prompt(self):
        # Including: commit or TestFlight cannot inherit approval intended for build/test.
        cases = (('build', 'commit'), ('test', 'commit'), ('build', 'testflight'),
                 ('commit', 'testflight'), ('verify', 'build'), ('status', 'commit'))
        for expected, selected in cases:
            gate.runtime_effects.capture.reset_mock()
            with self.subTest(expected=expected, selected=selected), \
                 patch.object(authorization, 'request_approval', side_effect=AssertionError('prompt')):
                code, data, handlers, calls, _ = self.execute('Do the thing for Example', selected,
                                                                '--confirm', '--expect', expected)
                self.assertEqual((code, calls), (1, 1))
                self.assertEqual((data['status'], data['error']), ('execution_denied', 'expected_capability_mismatch'))
                self.assertEqual((data['capability'], data['expected_capability']), (selected, expected))
                self.assertFalse(data['executed']); self.assertFalse(data['authorization_satisfied'])
                self.assertFalse(any(h.called for h in handlers.values()))
                gate.runtime_effects.capture.assert_not_called()

    def test_confirm_without_expect_refused_before_routing(self):
        for json_mode in (True, False):
            with patch.object(authorization, 'request_approval', side_effect=AssertionError('prompt')):
                code, out, handlers, calls, _ = self.execute('Build Example', 'build', '--confirm', json_mode=json_mode)
            self.assertEqual((code, calls), (1, 0))
            if json_mode:
                self.assertEqual((out['status'], out['error']), ('invalid_request', 'expected_capability_required'))
                self.assertFalse(out['executed'])
            else:
                self.assertIn('expected_capability_required', out)
                self.assertIn('add --expect CAPABILITY', out)
            self.assertFalse(any(h.called for h in handlers.values()))

    def test_json_and_non_tty_never_prompt(self):
        with patch.object(authorization, 'request_approval', wraps=authorization.request_approval) as prompt:
            code, data, handlers, _, stdin = self.execute('Build Example', 'build', '--expect', 'build')
            self.assertEqual((code, data['status']), (1, 'authorization_required'))
            code, out, handlers, _, stdin = self.execute('Build Example', 'build', json_mode=False)
            self.assertIn('authorization_required', out); self.assertNotIn('Continue?', out)
        prompt.assert_called_once()  # non-TTY: called, returned None without reading
        self.assertEqual(stdin.tell(), 0)
        self.assertFalse(any(h.called for h in handlers.values()))

    def test_unknown_expectation_is_an_argument_error(self):
        code, data, handlers, calls, _ = self.execute('Build Example', 'build', '--confirm', '--expect', 'shell')
        self.assertEqual((code, data['error'], calls), (2, 'invalid_arguments', 0))

    def test_no_match_and_unresolved_routes_with_expectation_execute_nothing(self):
        for request, choice in (('Push Example', 'no_match'), ('Build the project', 'build')):
            code, data, handlers, calls, _ = self.execute(request, choice, '--confirm', '--expect', 'build')
            self.assertEqual((code, calls), (1, 1))
            self.assertFalse(data['executed'])
            self.assertFalse(any(h.called for h in handlers.values()))

    def test_gate_requires_expectation_for_explicit_confirm(self):
        handlers = self.bindings()
        def dispatch(route, **kwargs):
            return gate.dispatch(route, natta.make_parser(), lambda: natta.load_registry(self.registry),
                                 provider.authoritative_ids(), handlers, **kwargs)
        self.assertEqual(dispatch(self.route('build'), confirm=True).error, 'expected_capability_required')
        self.assertEqual(dispatch(self.route('commit'), confirm=True, expected_capability='build').error,
                         'expected_capability_mismatch')
        self.assertFalse(any(h.called for h in handlers.values()))
        self.assertEqual(dispatch(self.route('build'), confirm=True, expected_capability='build').status, 'executed')
        self.assertEqual(handlers['build'].call_count, 1)
        unmatched = routing.RouteResult('no_match', 'no_match')
        self.assertEqual(dispatch(unmatched, confirm=True, expected_capability='build').status, 'no_match')


if __name__ == '__main__':
    unittest.main()
