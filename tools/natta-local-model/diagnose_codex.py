"""One-shot Luna error diagnostic. Terminal-only; no benchmark or dispatch."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluate_codex as evaluation
router = evaluation.router
MODEL = 'gpt-6-luna'
TIMEOUT = 120
FIELDS = ('code', 'error_code', 'error_type', 'category', 'status', 'status_code',
          'http_status', 'model_available', 'quota_exceeded', 'rate_limited', 'authentication_error')


def redact(text):
    """Bound terminal output and remove common credential representations."""
    # Redact before truncating so truncation cannot expose half a credential.
    text = re.sub(r'(?i)\b(?:bearer|basic)\s+[^\s,;\"\']+', '[REDACTED]', text)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[REDACTED]', text)
    text = re.sub(r'\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[REDACTED]', text)
    text = re.sub(r'(?i)\b(?:[a-z0-9_]*(?:token|secret|password|credential|api[_ -]?key)|authorization)'
                  r'["\']?\s*[=:]\s*(?:"[^"]*"|\'[^\']*\'|[^\s,;]+)', '[REDACTED]', text)
    text = re.sub(r'\b[A-Z][A-Z0-9_]*["\']?\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s,;]+)', '[REDACTED]', text)
    text = re.sub(r'https?://[^\s/@]+:[^\s/@]+@', 'https://[REDACTED]@', text)
    text = re.sub(r'\x1b\][^\x07]*(?:\x07|\x1b\\)', '', text)
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    return ''.join(c if c.isprintable() else ' ' for c in text)[:2000]


def error_fields(item):
    """Only allowlisted scalar metadata, or the error message as a fallback."""
    nested = item.get('error')
    sources = [item] + ([nested] if isinstance(nested, dict) else [])
    result = {}
    for source in sources:
        for key in FIELDS:
            value = source.get(key)
            if type(value) is bool or (type(value) is int and 0 <= value <= 999):
                result[key] = value
            elif isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,96}', value):
                # Metadata can also contain credential-shaped strings.
                result[key] = redact(value)
        if source is nested and isinstance(source.get('type'), str):
            value = source['type']
            if re.fullmatch(r'[A-Za-z0-9_.:-]{1,96}', value):
                result['error_type'] = redact(value)
    if not result:
        message = next((s.get('message') for s in sources if isinstance(s.get('message'), str)), None)
        if message is None and isinstance(nested, str):
            message = nested
        result = {'message': redact(message)} if message is not None else {'diagnostic': 'error_fields_unavailable'}
    return result


def extract(events):
    """Read only item.type=error payloads; no reasoning, messages or other events."""
    errors = []
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue  # Never echo malformed raw lines.
        if not isinstance(event, dict) or event.get('type') not in ('item.started', 'item.updated', 'item.completed'):
            continue
        item = event.get('item')
        if isinstance(item, dict) and item.get('type') == 'error':
            fields = error_fields(item)
            if fields not in errors:
                errors.append(fields)
            if len(errors) == 16:
                break
    return errors


def one_request(argv, cwd, prompt):
    # Same process lifetime controls as router.run_provider; raw stdout stays in memory.
    with subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, start_new_session=True) as child:
        try:
            stdout, _ = child.communicate(prompt, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.communicate()
            raise router.OutputFailure('provider_timeout') from None
        except BaseException:
            os.killpg(child.pid, signal.SIGKILL)
            child.communicate()
            raise
        return child.returncode, stdout


def main(argv=None, runner=one_request):
    # No model override, retry, output-file option, pilot or benchmark path.
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        print('Usage: python3 -B diagnose_codex.py', file=sys.stderr)
        return 2
    try:
        cases, _, _ = evaluation.inputs()
        case, name, _, _, order = evaluation.decisions(cases, 'pilot')[0]
        path = router.workspace_path()
        contents = router.generate(path, name, order)
        # Local help/version/features only; zero inference calls in preflight.
        evaluation.metadata(MODEL)
        returncode, events = runner(router.invocation(path, MODEL), path, router.prompt(case['request'], contents))
        router.validate_workspace(path, contents)
        errors = extract(events)
        # Diagnostic extraction does not change the fail-closed evaluation parser.
        result = {'provider_error_items': errors}
        try:
            final = router.final_message(events)
            if returncode:
                raise router.OutputFailure('provider_failure')
            router.validate_output(final, order)
            result['structured_result_valid'] = True  # Never print agent-message contents.
        except router.OutputFailure as exc:
            result['evaluation_error'] = str(exc)
            if exc.boundary_item_type is not None:
                result['boundary_item_type'] = exc.boundary_item_type
        print(json.dumps(result, ensure_ascii=True))
        return 1 if 'evaluation_error' in result else 0
    except router.OutputFailure as exc:
        print(json.dumps({'diagnostic': str(exc)}))
        return 1
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        # Exception messages/stderr may contain sensitive data: never echo them.
        print('{"diagnostic":"setup_or_provider_failure"}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
