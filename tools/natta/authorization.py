"""Terminal approval for already-validated actions; no routing or execution."""
import sys

from execution import bounded_text


def request_approval(actions, *, stdin=None, stdout=None, heading="Natta resolved:",
                     notice="This action requires authorization."):
    """Return True/False for a TTY response, or None when prompting is unavailable.

    Callers supply locally rendered frozen targets, never raw semantic requests.
    Optional presentation labels support the same approval semantics for a plan.
    """
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    if not stdin.isatty() or not stdout.isatty():
        return None
    if heading is not None:
        stdout.write(heading + '\n')
    for action in actions:
        stdout.write('  ' + action + '\n')
    stdout.write('\n' + notice + '\nContinue? [y/N]: ')
    stdout.flush()
    try:
        response = stdin.readline()
    except (EOFError, OSError):
        return False
    return response.strip().casefold() in ('y', 'yes')


def action_notice(capability, project_name, arguments):
    """Describe only a caller's frozen, locally validated explicit action."""
    name = bounded_text(project_name)
    if capability == 'testflight':
        return testflight_notice(name)
    if capability == 'commit':
        return (f'Authorize staging all current changes and creating ONE local commit '
                f'containing all current changes in {name}?\nIt will NOT push.')
    detail = ''.join(f' at level {value}' if key == 'level' else f' {key}={value}'
                     for key, value in arguments)
    return f'Authorize {capability} of {name}{detail}?'


def testflight_notice(project_name):
    name = bounded_text(project_name)
    return (f'Archive and upload a new {name} Release build to App Store Connect for TestFlight.\n'
            'This creates an external build record on Apple servers and permits Xcode-managed signing/provisioning.\n'
            'It will NOT submit the app for App Review or release it publicly.')
