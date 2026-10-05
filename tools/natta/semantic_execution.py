"""Local authorization and bound dispatch. No provider calls or text-to-command path."""
import contextlib
from dataclasses import dataclass, field
import io

import parameters
import policy
import routing
import runtime_effects
import confinement
from commits import CommitResult
from testflight import TestFlightResult
from execution import NattaError, Result, bounded_text


@dataclass(frozen=True)
class HandlerOutcome:
    exit_code: int
    checks: tuple[Result, ...] = ()
    commit_result: CommitResult | None = None
    testflight_result: TestFlightResult | None = None


@dataclass(frozen=True)
class ExecutionResult:
    route: routing.RouteResult
    status: str
    authorization_satisfied: bool = False
    execution_started: bool = False
    execution_succeeded: bool | None = None
    outcome: HandlerOutcome | None = None
    report: str = ''
    report_truncated: bool = False
    error: str | None = None
    handler_succeeded: bool | None = None
    effect_verification: runtime_effects.VerificationResult = field(default_factory=runtime_effects.VerificationResult)

    confinement: confinement.ConfinementResult = field(default_factory=confinement.ConfinementResult)

    @property
    def exit_code(self):
        if self.status in ('effect_violation', 'verification_unavailable', 'confinement_unavailable', 'confinement_setup_failed'):
            return 1
        return self.outcome.exit_code if self.execution_started and self.outcome else 1

    def as_dict(self):
        data = self.route.as_dict()
        data.update(status=self.status, executed=self.execution_started,
                    authorization=self.route.execution_policy.authorization.value,
                    authorization_satisfied=self.authorization_satisfied,
                    execution_started=self.execution_started,
                    execution_succeeded=self.execution_succeeded, handler_succeeded=self.handler_succeeded,
                    effect_verification=self.effect_verification.as_dict(), confinement=self.confinement.as_dict(), error=self.error,
                    result=None)
        if self.outcome:
            data['result'] = {'exit_code': self.outcome.exit_code, 'report': self.report,
                             'report_truncated': self.report_truncated,
                             'commit': self.outcome.commit_result.as_dict() if self.outcome.commit_result else None,
                             'testflight': self.outcome.testflight_result.as_dict() if self.outcome.testflight_result else None,
                             'checks': [{'name': bounded_text(r.check.name), 'status': r.status,
                                         'exit_code': r.exit_code, 'duration': r.duration,
                                         'detail_lines': [bounded_text(s) for s in r.detail_lines[:8]],
                                         'failing_tests': [bounded_text(s) for s in r.failing_tests[:5]]}
                                        for r in self.outcome.checks]}
        return data


class BoundedOutput(io.TextIOBase):
    """Presentation only: cap memory rather than collecting unlimited tool output."""
    def __init__(self, limit=12000):
        self.limit, self.parts, self.size, self.truncated = limit, [], 0, False

    def write(self, text):
        remaining = self.limit - self.size
        self.parts.append(text[:remaining]) if remaining else None
        self.size += min(len(text), remaining)
        self.truncated |= len(text) > remaining
        return len(text)

    def value(self):
        return ''.join(self.parts)


def validate_bindings(capability_ids, bindings):
    if set(bindings) != set(capability_ids) or any(not callable(f) for f in bindings.values()):
        raise ValueError('Handler coverage differs from executable routing universe')


def doctor_checks(capability_ids, bindings, parser=None):
    try:
        policy.validate_catalog(capability_ids)
        validate_bindings(capability_ids, bindings)
    except (TypeError, ValueError):
        return [(False, 'Semantic execution policy/handler coverage invalid')]
    if parser is not None:
        try:
            if parameters.schema_for(parser,'commit') != parameters.ParameterSchema(True):raise ValueError('Invalid commit parameters')
        except (ValueError,StopIteration):return [(False,'Commit project parameter schema invalid')]
    return [(True, 'Semantic execution handlers bound to known policy targets'), *runtime_effects.doctor_checks()]


@dataclass(frozen=True)
class ValidatedTarget:
    projects: tuple
    project: object
    schema: parameters.ParameterSchema
    decision: policy.PolicyResult
    handler: object

    def same_action(self, other):
        return (self.project == other.project and self.schema == other.schema
                and self.decision == other.decision and self.handler is other.handler)


def validate_target(route, parser, load_projects, capability_ids, bindings):
    """Shared fail-closed local preflight; no approval, snapshots or handlers."""
    if not isinstance(route, routing.RouteResult):
        raise TypeError('A typed internal route is required; JSON is not executable input')
    def denied(status, reason):
        return ExecutionResult(route, status, error=reason)
    if route.status == 'no_match':
        return denied('no_match', 'no_match')
    if route.status != 'matched' or route.error is not None:
        return denied(route.status, route.error or 'routing_error')
    if route.capability not in capability_ids:
        return denied('execution_denied', 'unknown_capability')
    if not route.arguments_resolved or route.missing_arguments or route.resolution_error:
        return denied('unresolved', 'unresolved_arguments')
    try:
        policy.validate_catalog(capability_ids)
        validate_bindings(capability_ids, bindings)
        schema = parameters.schema_for(parser, route.capability)
        projects = tuple(load_projects())
        decision = policy.evaluate(route, capability_ids, schema, projects)
    except Exception:
        return denied('execution_denied', 'execution_configuration_invalid')
    # Reject stale/tampered metadata, not just a subset of user-reported effects.
    if route.execution_policy != decision:
        return denied('execution_denied', 'policy_mismatch')
    if not decision.eligible or decision.effects & policy.forbidden_effects(route.capability):
        return denied('execution_denied', decision.reason or 'policy_ineligible')
    if decision.authorization == policy.Authorization.FORBIDDEN:
        return denied('execution_denied', 'forbidden_policy')
    if decision.authorization not in (policy.Authorization.AUTOMATIC, policy.Authorization.EXPLICIT):
        return denied('execution_denied', 'invalid_authorization')
    project = next((p for p in projects if p.alias == route.project), None)
    handler = bindings[route.capability]
    return ValidatedTarget(projects, project, schema, decision, handler)


def dispatch(route, parser, load_projects, capability_ids, bindings, *, confirm=False,
             authorize=None, expected_target=None):
    """Execute one frozen route through the shared local gate.

    Compound callers pin the preauthorized target; any drift fails closed.
    """
    target = validate_target(route, parser, load_projects, capability_ids, bindings)
    if isinstance(target, ExecutionResult):
        return target
    def denied(status, reason):
        return ExecutionResult(route, status, error=reason)
    if expected_target is not None and not expected_target.same_action(target):
        return denied('execution_denied', 'authorization_target_changed')
    projects, project, schema, decision, handler = (target.projects, target.project,
        target.schema, target.decision, target.handler)
    if decision.authorization == policy.Authorization.EXPLICIT and confirm is not True:
        approval = authorize(route, project) if authorize is not None else None
        if approval is None:
            return denied('authorization_required', 'explicit_authorization_required')
        if approval is not True:
            return denied('authorization_declined', 'authorization_declined')
        # Human input may take time. Revalidate locally without resolving/routing
        # again, and refuse changed targets rather than authorizing replacement data.
        try:
            policy.validate_catalog(capability_ids)
            validate_bindings(capability_ids, bindings)
            fresh_schema = parameters.schema_for(parser, route.capability)
            fresh_projects = tuple(load_projects())
            fresh_project = next((p for p in fresh_projects if p.alias == route.project), None)
            fresh_decision = policy.evaluate(route, capability_ids, fresh_schema, fresh_projects)
            if (fresh_schema != schema or fresh_project != project or fresh_decision != decision
                    or bindings[route.capability] is not handler or not fresh_decision.eligible):
                return denied('execution_denied', 'authorization_target_changed')
            projects = fresh_projects
        except Exception:
            return denied('execution_denied', 'execution_configuration_invalid')
    with contextlib.ExitStack() as stack:
        if route.capability in confinement.UNCONFINED_TARGETS:
            native = False
            confined = confinement.workflow_result(decision, committing=route.capability == 'commit')
        else:
            try:
                plan = confinement.plan_for(route.capability, route.arguments, project, decision)
            except Exception:
                return ExecutionResult(route, 'confinement_unavailable', authorization_satisfied=True,
                                       error='confinement_configuration_invalid',
                                       confinement=confinement.ConfinementResult(required=True, mode='unavailable', reason='confinement_configuration_invalid'))
            try:
                session = stack.enter_context(confinement.open_session(plan))
                native = isinstance(session, confinement.BackendSession)
                confined = session.result if native else session
                if native and (not isinstance(confined, confinement.ConfinementResult) or confined.validated is not True):
                    raise confinement.ConfinementUnavailable('validation_required')
                if not native and (not isinstance(confined, confinement.ConfinementResult) or confined.applied is not True or
                        not plan.required_effects <= confined.enforced_effects):
                    raise confinement.ConfinementUnavailable('invalid_enforcement_session')
            except Exception as exc:
                status='confinement_unavailable' if isinstance(exc, confinement.ConfinementUnavailable) else 'confinement_setup_failed'
                return ExecutionResult(route, status, authorization_satisfied=True,
                                       error=status, confinement=confinement.unavailable_result(plan, str(exc) if isinstance(exc,confinement.ConfinementUnavailable) else 'setup_failed'))
        output = BoundedOutput()
        before = None
        capture = runtime_effects.capture_commit if policy.Effect.GIT_COMMIT in decision.effects else runtime_effects.capture
        verification = runtime_effects.VerificationResult(reason='no_project_target')
        if project is not None:
            try:
                before = capture(project)
                if not isinstance(before, runtime_effects.execution.ProtectedSnapshot):
                    raise ValueError('Invalid protected snapshot')
            except Exception:
                return ExecutionResult(route, 'verification_unavailable', authorization_satisfied=True,
                                       error='pre_execution_verification_failed',
                                       effect_verification=runtime_effects.unavailable('pre_execution_verification_failed', committing=route.capability=='commit'),
                                       confinement=confined)
        error = None
        native_started = False
        try:
            if native:
                invocation = session.invoke(projects, project, route.arguments)
                if not isinstance(invocation, confinement.Invocation):
                    raise ValueError('Invalid native invocation')
                native_started = invocation.started is True
                confined = session.result
                if not invocation.started:
                    return ExecutionResult(route, 'confinement_setup_failed', authorization_satisfied=True,
                                           error=invocation.error, confinement=confined)
                if (not isinstance(confined, confinement.ConfinementResult) or confined.applied is not True or
                        not plan.required_effects <= confined.enforced_effects):
                    raise ValueError('Invalid active enforcement')
                outcome, error = invocation.outcome, invocation.error
                output.write(invocation.report)
                output.truncated |= invocation.report_truncated
            else:
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    outcome = handler(projects, project, route.arguments)
            if (not isinstance(outcome, HandlerOutcome) or type(outcome.exit_code) is not int
                    or outcome.exit_code < 0 or not isinstance(outcome.checks, tuple)
                    or any(not isinstance(check, Result) for check in outcome.checks)
                    or outcome.commit_result is not None and not isinstance(outcome.commit_result,CommitResult)
                    or outcome.testflight_result is not None and not isinstance(outcome.testflight_result,TestFlightResult)):
                raise ValueError('Invalid handler outcome')
        except Exception as exc:
            if native and not native_started:
                return ExecutionResult(route, 'confinement_setup_failed', authorization_satisfied=True,
                                       error='confinement_setup_failed', confinement=confined)
            if isinstance(exc, (NattaError, OSError)):
                output.write('natta: ' + bounded_text(str(exc), limit=1000) + '\n')
            outcome, error = HandlerOutcome(1), 'handler_error'
        handler_succeeded = outcome.exit_code == 0
        if before is not None:
            try:
                after = capture(project, before)
                verification = runtime_effects.compare(before, after, decision.effects)
                if (not isinstance(verification, runtime_effects.VerificationResult) or
                        verification.performed is not True or type(verification.passed) is not bool):
                    raise ValueError('Invalid effect verification result')
            except Exception:
                verification = runtime_effects.unavailable('post_execution_verification_failed', committing=route.capability=='commit')
        status = 'executed' if handler_succeeded else 'execution_failed'
        if verification.passed is False:
            status = 'effect_violation' if verification.violations else 'verification_unavailable'
            error = verification.reason
        return ExecutionResult(route, status, authorization_satisfied=True, execution_started=True,
                               execution_succeeded=handler_succeeded and verification.passed is not False,
                               outcome=outcome, report=output.value(), report_truncated=output.truncated,
                               error=error, handler_succeeded=handler_succeeded, effect_verification=verification, confinement=confined)


def render_result(result):
    data = result.as_dict()
    lines = ['Execution', f'  status: {result.status}',
             f'  capability: {result.route.capability or "none"}',
             f'  project: {result.route.project or "none"}',
             f'  authorization: {data["authorization"]}',
             f'  execution started: {"yes" if result.execution_started else "no"}']
    if result.status == 'authorization_declined':
        lines.append('Authorization declined.')
    if result.execution_succeeded is not None:
        lines.append(f'  execution succeeded: {"yes" if result.execution_succeeded else "no"}')
    if result.confinement.required:
        lines.append('  confinement: ' + result.confinement.mode)
    if result.effect_verification.performed:
        lines.append('  protected state: ' + ('passed' if result.effect_verification.passed else 'failed'))
        if result.effect_verification.violations:
            lines.append('  effect violations: ' + ', '.join(sorted(e.value for e in result.effect_verification.violations)))
    if result.error:
        lines.append(f'  error: {result.error}')
    if result.report:
        lines.append(result.report.rstrip())
    if result.report_truncated:
        lines.append('  report truncated')
    return '\n'.join(lines)
