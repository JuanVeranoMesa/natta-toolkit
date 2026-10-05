"""Bounded semantic selection only. No project inspection or execution imports."""
from dataclasses import asdict, dataclass, field
import json
import policy
import shutil
import subprocess
import router_codex as provider
from testflight_semantics import public_release_requested

ERRORS = frozenset(('malformed_output', 'unknown_choice', 'provider_failure',
                    'provider_timeout', 'boundary_violation'))


@dataclass(frozen=True)
class RouteResult:
    status: str
    capability: str | None
    error: str | None = None
    provider: str = field(default=provider.MODEL, init=False)
    executed: bool = field(default=False, init=False)
    project: str | None = None
    arguments: tuple[tuple[str, int], ...] = ()
    arguments_resolved: bool = False
    missing_arguments: tuple[str, ...] = ()
    resolution_error: str | None = None
    execution_policy: policy.PolicyResult = field(default_factory=policy.PolicyResult)

    def as_dict(self):
        result = asdict(self)
        result['arguments'] = dict(self.arguments)
        result['missing_arguments'] = list(self.missing_arguments)
        result['execution_policy'] = self.execution_policy.as_dict()
        return result


def select(request, *, decision_factory=None, capability_ids=None):
    """One explicit Luna decision; no registry, project, argument or dispatch path."""
    if not isinstance(request, str) or not request.strip():
        return RouteResult('invalid_request', None, 'empty_request')
    ids = provider.TOOLKIT_IDS if capability_ids is None else capability_ids
    try:
        provider.generate(provider.workspace_path(), 'concise', ids)
    except (OSError, ValueError):
        return RouteResult('routing_error', None, 'workspace_or_definitions_invalid')
    try:
        provider.probe_cli(provider.workspace_path())
    except (OSError, subprocess.SubprocessError):
        return RouteResult('provider_error', None, 'provider_unavailable')
    except ValueError:
        return RouteResult('routing_error', None, 'unsupported_cli')
    try:
        answer = (provider.CodexDecision if decision_factory is None else decision_factory)(model=provider.MODEL).decide(request, 'concise', ids)
        if not isinstance(answer, dict):
            return RouteResult('provider_error', None, 'malformed_output')
        if answer.get('status') != 'passed' or answer.get('error') is not None:
            code = answer.get('error')
            return RouteResult('provider_error', None, code if isinstance(code, str) and code in ERRORS else 'provider_failure')
        # Revalidate at the production boundary, even when the provider adapter is supplied/mocked.
        choice = provider.validate_output(json.dumps({'choice': answer.get('selected_id')}), ids)
        if 'testflight' in ids and public_release_requested(request):
            choice = 'no_match'
        return RouteResult('no_match' if choice == 'no_match' else 'matched', choice)
    except provider.OutputFailure as exc:
        return RouteResult('provider_error', None, str(exc) if str(exc) in ERRORS else 'provider_failure')
    except subprocess.TimeoutExpired:
        return RouteResult('provider_error', None, 'provider_timeout')
    except (OSError, subprocess.SubprocessError):
        return RouteResult('provider_error', None, 'provider_failure')
    except ValueError:
        return RouteResult('routing_error', None, 'workspace_or_definitions_invalid')


def render_route(result):
    lines = ['Route', f'  status: {result.status}',
             f'  capability: {result.capability if result.capability is not None else "none"}',
             f'  project: {result.project if result.project is not None else "none"}']
    if result.arguments:
        lines.append('  arguments:')
        lines.extend(f'    {name}: {value}' for name, value in result.arguments)
    if result.missing_arguments:
        lines.append('  missing: ' + ', '.join(result.missing_arguments))
    if result.resolution_error:
        lines.append('  resolution error: ' + result.resolution_error)
    lines.extend([f'  provider: {result.provider}',
                  f'  resolved: {"yes" if result.arguments_resolved else "no"}', '  executed: no'])
    decision = result.execution_policy
    if decision.reason != 'not_evaluated':
        if decision.effects:
            lines.append('  effects: ' + ', '.join(effect.value.replace('_', ' ') for effect in sorted(decision.effects, key=lambda e: e.value)))
        lines.append('  authorization: ' + decision.authorization.value)
        lines.append('  policy eligible: ' + ('yes' if decision.eligible else 'no'))
        if decision.reason:
            lines.append('  policy reason: ' + decision.reason)
    if result.error:
        lines.append(f'  error: {result.error}')
    return '\n'.join(lines)


def doctor_checks():
    """Read-only local readiness, not authentication/network/model availability."""
    checks = []
    try:
        expected = provider.workspace_contents('concise', provider.TOOLKIT_IDS)
        checks.append((True, 'Semantic router: gpt-6-luna / concise / canonical / no_match; definitions valid'))
    except (OSError, ValueError):
        return [(False, 'Semantic router definitions invalid')]
    path = provider.workspace_path()
    try:
        provider.reject_symlinks(path)
        if path.exists():
            provider.validate_workspace(path)
            try:
                provider.validate_workspace(path, expected)
                label = 'Semantic router workspace valid'
            except ValueError:
                label = 'Semantic router workspace stale; route regenerates owned routing artifacts'
        else:
            label = 'Semantic router workspace missing; route generates minimal routing artifacts'
        checks.append((True, label))
    except (OSError, ValueError):
        checks.append((False, 'Semantic router workspace unsafe; unexpected files/links are never removed automatically'))
    if shutil.which('codex') is None:
        checks.append((False, 'Semantic router Codex CLI missing'))
    else:
        try:
            provider.probe_cli()
            checks.append((True, 'Semantic router CLI supports isolation; auth/network/model access not probed'))
        except (OSError, ValueError, subprocess.SubprocessError):
            checks.append((False, 'Semantic router CLI compatibility unavailable; no provider request made'))
    return checks
