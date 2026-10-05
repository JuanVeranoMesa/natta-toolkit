"""Bounded orchestration of the existing semantic dispatcher; no provider routing."""
from dataclasses import dataclass

import authorization
import planning
import policy
import routing
import semantic_execution as gate
from execution import bounded_text


@dataclass(frozen=True)
class CompoundResult:
    plan: planning.PlanResult
    status: str
    steps: tuple[gate.ExecutionResult | None, ...] = ()
    authorization_satisfied: bool = False
    failed_step: int | None = None
    error: str | None = None

    @property
    def execution_started(self):
        return any(s is not None and s.execution_started for s in self.steps)

    @property
    def execution_succeeded(self):
        if self.status == 'executed':
            return True
        return False if self.failed_step is not None or self.execution_started else None

    @property
    def exit_code(self):
        if self.status == 'executed':
            return 0
        if self.failed_step and self.steps[self.failed_step - 1] is not None:
            return self.steps[self.failed_step - 1].exit_code
        return 1

    def as_dict(self):
        steps = []
        for step, result in zip(self.plan.steps, self.steps):
            data = result.as_dict() if result is not None else {
                **step.as_dict(), 'status': 'not_started', 'execution_started': False,
                'execution_succeeded': None, 'result': None,
                'reason': 'prior_step_failed' if self.failed_step else self.status}
            steps.append({**data, 'index': step.index})
        return {'status': self.status, 'executed': self.execution_started,
                'execution_started': self.execution_started,
                'execution_succeeded': self.execution_succeeded,
                'provider': self.plan.provider, 'plan': self.plan.as_dict(),
                'authorization': self.plan.authorization.value,
                'authorization_satisfied': self.authorization_satisfied,
                'steps': steps, 'failed_step': self.failed_step, 'error': self.error}


def route_for(step):
    """Lossless typed bridge; never resolve natural language or substitute args."""
    return routing.RouteResult('matched', step.capability, project=step.project,
        arguments=step.arguments, arguments_resolved=True, execution_policy=step.execution_policy)


def plan_lines(plan, projects):
    names = {p.alias: bounded_text(p.name) for p in projects}
    lines = []
    for s in plan.steps:
        args = ' '.join(f'{k}={v}' for k, v in s.arguments)
        lines.append(f'{s.index}. {s.capability:<7} {names.get(s.project, s.project)}' + (' ' + args if args else ''))
    return tuple(lines)


def authorization_notice(plan, projects):
    """Approval presentation derived solely from the complete frozen plan."""
    lines = ['Authorization required:',
             '  Pressing y authorizes execution of the displayed plan:']
    lines.extend('    ' + line for line in plan_lines(plan, projects))
    names = {p.alias: bounded_text(p.name) for p in projects}
    for step in plan.steps:
        if step.capability == 'testflight':
            lines.append('  Step ' + str(step.index) + ': ' + authorization.testflight_notice(names[step.project]).replace('\n', '\n  '))
        if step.capability == 'commit':
            lines.append(f'  Step {step.index} includes staging all current changes and creating ONE local commit')
            lines.append(f'  containing all current changes in {names[step.project]}.')
            lines.append('  It will NOT push.')
    return '\n'.join(lines) + '\n'


def run(goal, parser, load_projects, bindings, *, confirm=False, json_mode=False):
    # This is the only planner call. All remaining work uses immutable local data.
    projects = tuple(load_projects())
    plan = planning.propose(goal, parser, projects, bindings)
    if not isinstance(plan, planning.PlanResult):
        raise TypeError('A typed internal plan is required')
    empty = (None,) * len(plan.steps)
    def denied(status, error=None, failed_step=None, steps=empty, approved=False):
        return CompoundResult(plan, status, steps, approved, failed_step, error)
    if plan.status != 'planned' or not plan.valid:
        return denied(plan.status, plan.error)
    # Reuse planner validation as a whole-plan integrity check, without a model.
    checked = planning.validate({'status': 'planned', 'steps': [
        {'capability': s.capability, 'project': s.project, 'arguments': dict(s.arguments)}
        for s in plan.steps]}, parser, projects, bindings)
    if (not checked.valid or checked.steps != plan.steps or
            checked.authorization != plan.authorization or checked.effects != plan.effects):
        return denied('invalid_plan', 'frozen_plan_invalid')
    ids = routing.provider.authoritative_ids() - {'codex', 'route'}
    routes = tuple(route_for(s) for s in plan.steps)
    targets = []
    for step, route in zip(plan.steps, routes):
        target = gate.validate_target(route, parser, load_projects, ids, bindings)
        if isinstance(target, gate.ExecutionResult):
            return denied('execution_denied', target.error, step.index)
        original = next(p for p in projects if p.alias == step.project)
        if target.project != original:
            return denied('execution_denied', 'authorization_target_changed', step.index)
        targets.append(target)
    if not json_mode:
        print('Plan\n' + '\n'.join('  ' + line for line in plan_lines(plan, projects)), flush=True)
    if plan.authorization == policy.Authorization.EXPLICIT and confirm is not True:
        approval = None if json_mode else authorization.request_approval((), heading=None,
            notice=authorization_notice(plan, projects))
        if approval is not True:
            return denied('authorization_required' if approval is None else 'authorization_declined',
                          'explicit_authorization_required' if approval is None else 'authorization_declined')
    # Revalidate the complete authorized unit before even an automatic prefix.
    for step, route, expected in zip(plan.steps, routes, targets):
        fresh = gate.validate_target(route, parser, load_projects, ids, bindings)
        if isinstance(fresh, gate.ExecutionResult) or not expected.same_action(fresh):
            return denied('execution_denied', fresh.error if isinstance(fresh, gate.ExecutionResult)
                          else 'authorization_target_changed', step.index, approved=True)
    results = []
    for step, route, target in zip(plan.steps, routes, targets):
        result = gate.dispatch(route, parser, load_projects, ids, bindings,
                               confirm=True, expected_target=target)
        results.append(result)
        if result.execution_succeeded is not True:
            return denied(result.status, result.error, step.index,
                          tuple(results) + empty[len(results):], approved=True)
    return CompoundResult(plan, 'executed', tuple(results), True)


def render(result):
    lines = []
    for step, execution in zip(result.plan.steps, result.steps):
        status = 'SKIPPED' if execution is None else 'PASS' if execution.execution_succeeded is True else 'FAIL'
        suffix = ''
        if execution is not None:
            if execution.outcome and execution.outcome.commit_result:
                commit = execution.outcome.commit_result
                suffix = ' ' + (commit.commit_hash[:7] if commit.commit_hash else commit.status)
            if execution.outcome and execution.outcome.testflight_result:
                upload = execution.outcome.testflight_result
                if upload.upload_succeeded:
                    suffix += ' uploaded; processing pending; no App Review/public release'
            if status == 'FAIL':
                suffix += ' ' + (execution.error or execution.status)
        lines.append(f'{step.index}. {step.capability:<7} {status}{suffix}')
    lines.append('Result: ' + ('passed' if result.execution_succeeded is True else result.status))
    if result.error:
        lines.append('Error: ' + result.error)
    return '\n'.join(lines)
