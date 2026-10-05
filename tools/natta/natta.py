#!/usr/bin/env python3
"""Deterministic local project control plane (Python 3.11+)."""

import argparse
import contextlib
import io
import json
import routing
import parameters
import policy
import semantic_execution
from functools import partial
from types import MappingProxyType
from dataclasses import asdict, dataclass, field, replace
import commits
import testflight
import adapters
from execution import Check, NattaError, execute, render, snapshot, assert_integrity, ExecutionDirectory
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
import textwrap
import local_model

ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parent.parent
DEFAULT_REGISTRY = Path.home() / ".config/natta/projects.toml"
DOCS = ("agents", "architecture", "roadmap")


@dataclass(frozen=True)
class Project:
    alias: str
    aliases: tuple[str, ...]
    name: str
    path: Path
    type: str
    files: dict[str, Path]
    execution: dict = field(default_factory=dict)


def load_registry(path):
    try:
        data = tomllib.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise NattaError(f"Cannot read registry {path}: {exc}") from exc
    if set(data) != {"schema_version", "projects"} or type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise NattaError("Registry requires schema_version = 1 and projects only")
    if not isinstance(data["projects"], list) or not data["projects"]:
        raise NattaError("Registry must contain at least one [[projects]] entry")
    projects, used = [], set()
    required = {"alias", "name", "path", "type"}
    allowed = required | {"aliases", "version_source", "execution", *DOCS}
    for entry in data["projects"]:
        if not isinstance(entry, dict) or not required <= entry.keys() or entry.keys() - allowed:
            raise NattaError("Project has missing required or unknown fields")
        for key in entry.keys() - {"aliases", "execution"}:
            if not isinstance(entry[key], str) or not entry[key].strip():
                raise NattaError(f"Project {key} must be a nonempty string")
        aliases = entry.get("aliases", [])
        if not isinstance(aliases, list):
            raise NattaError("aliases must be an array of strings")
        for alias in [entry["alias"], *aliases]:
            if not isinstance(alias, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", alias):
                raise NattaError(f"Invalid alias: {alias!r}")
            if alias in used:
                raise NattaError(f"Duplicate alias: {alias}")
            used.add(alias)
        project_path = Path(entry["path"]).expanduser()
        if not project_path.is_absolute():
            project_path = Path(path).resolve().parent / project_path
        project_path = project_path.resolve()
        files = {}
        for key in (*DOCS, "version_source"):
            if key in entry:
                file = (project_path / entry[key]).resolve()
                if not file.is_relative_to(project_path):
                    raise NattaError(f"{key} must be inside project {entry['alias']}")
                files[key] = file
        projects.append(Project(entry["alias"], tuple(aliases), entry["name"], project_path, entry["type"], files, adapters.parse_execution(entry["type"], entry.get("execution", {}), project_path)))
    return projects


def lookup(projects, alias):
    for project in projects:
        if alias in (project.alias, *project.aliases):
            return project
    raise NattaError(f"Unknown project {alias!r}; choose: {', '.join(p.alias for p in projects)}")


def launch_codex(target):
    """Interface: hand the current terminal/process to Codex in a known context."""
    if not target.is_dir():
        raise NattaError(f"Codex launch directory missing: {target}")
    executable = shutil.which("codex")
    if executable is None:
        raise NattaError("Codex CLI unavailable: codex not found on PATH")
    # Resolve a relative PATH entry before changing directories.
    executable = str(Path(executable).absolute())
    try:
        os.chdir(target)
        os.execv(executable, [executable])
    except OSError as exc:
        raise NattaError(f"Cannot launch Codex in {target}: {exc}") from exc
    return 0  # Only reached when exec is mocked in tests.


def git(project, *args):
    # No shell, optional index refresh, pager, external diff or text conversion.
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(project.path), *args],
            capture_output=True, env={**os.environ, "GIT_PAGER": "cat"}, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NattaError(f"Git unavailable for {project.alias}: {exc}") from exc
    if result.returncode:
        raise NattaError(result.stderr.decode(errors="replace").strip())
    return result.stdout.decode(errors="replace")


def parse_status(raw):
    """Porcelain v1 -z: renamed/copied entries have an extra path record."""
    records = raw.split("\0")
    entries, index = [], 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        code, path = record[:2], record[3:]
        original = None
        if "R" in code or "C" in code:
            original = records[index]
            index += 1
        entries.append((code, path, original))
    return entries


def git_state(project):
    if not project.path.is_dir():
        raise NattaError(f"Project path missing: {project.path}")
    top = Path(git(project, "rev-parse", "--show-toplevel").strip()).resolve()
    if top != project.path:
        raise NattaError(f"Project path is not a repository root: {project.path}")
    branch = git(project, "branch", "--show-current").strip() or "detached HEAD"
    return branch.strip(), parse_status(git(project, "status", "--porcelain=v1", "-z", "--untracked-files=all"))


def version(project):
    if project.type != "ios-xcode":
        return "not configured"
    sources = [project.files["version_source"]] if "version_source" in project.files else sorted(project.path.glob("*.xcodeproj/project.pbxproj"))
    if not sources:
        return "unavailable (no Xcode metadata)"
    summaries = []
    for source in sources:
        try:
            text = source.read_text()
        except OSError as exc:
            raise NattaError(f"Cannot read version metadata {source}: {exc}") from exc
        values = []
        for key in ("MARKETING_VERSION", "CURRENT_PROJECT_VERSION"):
            found = sorted(set(re.findall(r"\b" + key + r"\s*=\s*\"?([^;\"\n]+)\"?\s*;", text)))
            values.append(", ".join(value.strip() for value in found) or "unavailable")
        # Report all distinct values; never pretend multiple targets agree.
        summaries.append(f"{values[0]} (build {values[1]})")
    return "; ".join(summaries)


def orientation(project):
    branch, entries = git_state(project)
    try:
        latest = git(project, "log", "-1", "--format=%h %s").strip()
    except NattaError:
        if git(project, "rev-list", "--all", "--count").strip() == "0":
            latest = "no commits yet"
        else:
            raise
    lines = [f"Project: {project.name} ({project.alias})", f"Path: {project.path}",
             f"Type: {project.type}", f"Branch: {branch}",
             f"Working tree: {'dirty' if entries else 'clean'} ({len(entries)} changed paths)",
             f"Latest commit: {latest}", f"Version: {version(project)}", "Canonical documents:"]
    for key in DOCS:
        file = project.files.get(key)
        lines.append(f"  {key}: {file} [{'present' if file.is_file() else 'MISSING'}]" if file else f"  {key}: not configured")
    return "\n".join(lines)


def doctor(projects, *, json_mode=False):
    checks = [(True, "Registry schema and unique aliases")]
    checks.append((shutil.which("git") is not None, "git available"))
    launcher = shutil.which("natta")
    expected = WORKSPACE_ROOT / "bin/natta"
    checks.append((launcher is not None and Path(launcher).resolve() == expected.resolve() and os.access(expected, os.X_OK), f"Managed launcher on PATH: {launcher or 'missing'}"))
    # Optional runtime inspection is explicit about omissions, without making
    # deterministic core health depend on installation of ML dependencies.
    local_checks = local_model.doctor_checks()
    routing_checks = [(shutil.which("codex") is not None, "Codex CLI available (optional semantic provider/interface)"),
                      *routing.doctor_checks(), *semantic_execution.confinement.doctor_checks(projects),
                      *testflight.doctor_checks(projects)]
    checks.extend(policy.doctor_checks(routing.provider.authoritative_ids() - {'codex', 'route'}))
    checks.extend(semantic_execution.doctor_checks(routing.provider.authoritative_ids() - {'codex', 'route'}, handler_bindings(), make_parser()))
    for project in projects:
        checks.append((project.path.is_dir(), f"{project.alias}: project directory"))
        for key, file in project.files.items():
            checks.append((file.is_file(), f"{project.alias}: {key} ({file})"))
        if shutil.which("git") and project.path.is_dir():
            try:
                orientation(project)
                checks.append((True, f"{project.alias}: repository and metadata readable"))
            except NattaError as exc:
                checks.append((False, f"{project.alias}: {exc}"))
        try:
            checks.extend((passed, f"{project.alias}: {label}") for passed, label in adapters.resolve(project).diagnose())
        except NattaError as exc:
            checks.append((False, f"{project.alias}: {exc}"))
    core_ready = all(passed for passed, _ in checks)
    optional_checks = [*local_checks, *routing_checks]
    if json_mode:
        rows = [Check(label, status='passed' if passed else 'failed') for passed, label in checks]
        rows.extend(Check(label, status='passed' if passed else 'unavailable', optional=True)
                    for passed, label in optional_checks)
        print(json.dumps({'status': 'passed' if core_ready else 'failed', 'core_ready': core_ready,
                          'checks': [asdict(check) for check in rows], 'error': None}, sort_keys=True))
    else:
        for passed, label in checks:
            print(f"{'OK' if passed else 'FAIL'}  {label}")
        for passed, label in optional_checks:
            print(f"{'OK' if passed else 'UNAVAILABLE'}  {label} (optional)")
        print(f"Core Natta: {'ready' if core_ready else 'not ready'}; optional checks do not determine core readiness.")
    return 0 if core_ready else 1


def make_parser():
    parser = argparse.ArgumentParser(
        prog="natta",
        description=textwrap.fill(("Natta is a local-first development toolkit/control plane. "
                     "It knows registered projects and provides deterministic inspection, "
                     "build, test, verification, and environment diagnostics."), width=78),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  natta projects
  natta status example
  natta build example
  natta test example
  natta verify example --level 2
  natta doctor
  natta codex
  natta codex example
  natta route "Build Example for the simulator"
""")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY,
                        metavar="PATH", help="Project registry TOML (default: ~/.config/natta/projects.toml)")
    commands = parser.add_subparsers(dest="command", required=True, title="commands")
    pages = {
        "route": ("Propose a capability using Luna; never execute", "Bounded semantic capability selection using gpt-6-luna. Natta validates the result and never executes it. Projects and bounded arguments are resolved locally from the registry and CLI schema. Sends only the request and routing definitions; no repository inspection.", 'natta route "Build Example for the simulator"'),
        "codex": ("Launch Codex at the toolkit checkout or a registered project", "Launch the interactive Codex CLI from the toolkit checkout root, or from a registered project's repository when PROJECT is supplied. Uses Codex's normal instruction discovery; no prompt is injected.", "natta codex"),
        "projects": ("List registered projects", "List registered projects with alias, display name, adapter type, and repository path.", "natta projects"),
        "status": ("Inspect project and Git status", "Deterministic, read-only inspection of branch, working-tree status, version/build, latest commit, and canonical document paths.", "natta status example"),
        "context": ("Show compact project orientation", "Compact orientation/context summary for humans and future provider launchers. Currently shares status fields and rendering; it does not invoke AI or read document contents.", "natta context sample"),
        "diff": ("Inspect changed paths and Git differences", "Read-only Git/project difference inspection: changed paths and staged/unstaged summaries. Does not stage, reset, clean, or commit.", "natta diff example"),
        "doctor": ("Diagnose environment and project health", "Diagnostic-only environment, configuration, and project health checks. Phase 2 checks include adapters, Xcode availability, and container/scheme metadata. Does not build, test, verify, install, or repair; simulator availability is checked during test discovery.", "natta doctor"),
        "build": ("Run a registered development build", "Run the registered deterministic development build. No AI chooses build commands. Uses external temporary build state and reports failures without fixing them.", "natta build example"),
        "test": ("Run a registered normal test workflow", "Run the registered deterministic normal test workflow. Simulator or external prerequisites may be unavailable. Uses external temporary test state; no automatic fixes.", "natta test sample"),
        "testflight": ("Archive and upload a build for TestFlight", "Archive a Release build and upload it to App Store Connect for TestFlight. Requires explicit confirmation; never submits App Review or releases publicly. --check validates local prerequisites only.", "natta testflight example"),
        "commit": ("Commit all project changes locally", "Stage all current changes inside the registered repository and create one local commit. No push, hooks, signing or rollback.", "natta commit example"),
        "verify": ("Run project-specific validation by level", "Run a registered deterministic verification workflow. Exact checks are project-specific; missing external prerequisites may produce unavailable/skipped states. No automatic fixes. Level 3 is not physical-device or release certification.", "natta verify example --level 2"),
    }
    # Reviewed Git mutation remains an atomic, project-scoped direct capability.
    for command, (summary, description, example) in pages.items():
        epilog = "Example:\n  " + example
        if command == "codex":
            epilog = """Invocation forms:
  natta codex
    Launch Codex from the toolkit checkout root.
  natta codex <project>
    Launch Codex from a registered project's repository.

Examples:
  natta codex
  natta codex example
  natta codex sample"""
        if command == "verify":
            epilog = """Validation levels:
  Level 1: Localized / low-risk validation.
  Level 2: Business-logic validation with stronger automated checks.
  Level 3: Platform, persistence, integration, or release-sensitive validation.

""" + epilog
        sub = commands.add_parser(command, help=summary, description=textwrap.fill(description, width=78),
                                  epilog=epilog, formatter_class=argparse.RawDescriptionHelpFormatter)
        if command not in ("projects", "doctor", "route"):
            sub.add_argument("project", metavar="PROJECT",
                             help="Registered Natta project alias, such as example or sample",
                             **({"nargs": "?"} if command == "codex" else {}))
        if command == "doctor":
            sub.add_argument("--json", action="store_true", help="Structured core and optional health checks")
        if command == "commit":
            sub.add_argument('--message',help='Commit message (1–512 characters); default Update <Display Name>')
            sub.add_argument('--json',action='store_true',help='Print the structured commit result')
        if command == "route":
            sub.add_argument("request", metavar="REQUEST", help="Natural-language request to classify")
            sub.add_argument("--json", action="store_true", help="Print the structured route result")
        if command in ("build", "test", "verify"):
            sub.add_argument("--verbose", action="store_true",
                             help="Stream raw tool output to the terminal; does not retain logs")
            sub.add_argument("--log", action="store_true",
                             help="Retain diagnostics in ~/Library/Logs/NattaToolkit/<run-id>/; does not increase terminal verbosity")
        if command == "testflight":
            sub.add_argument("--confirm", action="store_true", help="Authorize archive and TestFlight upload")
            sub.add_argument("--check", action="store_true", help="Check local prerequisites only; never archive/upload")
            sub.add_argument("--json", action="store_true", help="Structured result; never prompt")
        if command == "verify":
            sub.add_argument("--level", type=int, choices=(1, 2, 3), required=True,
                             help="Validation level (see levels below)")
    # Control command outside pages: never a bounded semantic target itself.
    sub = commands.add_parser('execute', help='Execute one resolved route after local authorization')
    sub.add_argument('request', metavar='REQUEST')
    sub.add_argument('--json', action='store_true', help='Print the structured execution result')
    sub.add_argument('--confirm', action='store_true', help='Authorize explicit-policy execution for this invocation only')
    sub = commands.add_parser('plan', help='Propose a bounded experimental plan; never execute')
    sub.add_argument('goal', metavar='GOAL')
    sub.add_argument('--json', action='store_true', help='Print the structured planning result')
    sub = commands.add_parser('do', help='Execute one bounded plan after whole-plan authorization')
    sub.add_argument('goal', metavar='GOAL')
    sub.add_argument('--json', action='store_true', help='Print the structured compound result')
    sub.add_argument('--confirm', action='store_true', help='Authorize the complete validated plan for this invocation')
    sub = commands.add_parser('confinement', help='Validate native confinement without Luna')
    actions = sub.add_subparsers(dest='confinement_action', required=True)
    validate = actions.add_parser('validate', help='Disposable inspection confinement probes; no Luna or app workflows')
    return parser


def prepare_route(request, parser, registry, *, selector=None):
    result = (routing.select if selector is None else selector)(request)
    schema, projects = None, ()
    if result.status == "matched":
        try:
            schema = parameters.schema_for(parser, result.capability)
            projects = load_registry(registry) if schema.project_required else ()
            resolution = parameters.resolve(request, projects, schema)
            result = replace(result, **asdict(resolution))
        except (NattaError, OSError, ValueError):
            result = routing.RouteResult("routing_error", None, "parameter_configuration_invalid")
    capability_ids = routing.provider.authoritative_ids() - {'codex', 'route'}
    try:
        policy.validate_catalog(capability_ids)
    except (ValueError, TypeError):
        result = routing.RouteResult('routing_error', None, 'policy_configuration_invalid')
    result = replace(result, execution_policy=policy.evaluate(result, capability_ids, schema, projects))
    return result


def handle_projects(projects, project, arguments):
    for project in projects:
        print(f"{project.alias:8} {project.name} | {project.type} | {project.path}")
    return semantic_execution.HandlerOutcome(0)


def handle_doctor(projects, project, arguments, *, json_mode=False):
    return semantic_execution.HandlerOutcome(doctor(projects, json_mode=json_mode))


def handle_inspection(operation, projects, project, arguments):
    print(orientation(project))
    if operation == "diff":
        for code, path, original in parse_status(git(project, "status", "--porcelain=v1", "-z", "--untracked-files=all")):
            print(f"  {code} {path!r}" + (f" (from {original!r})" if original else ""))
        for title, extra in (("Unstaged diff summary", []), ("Staged diff summary", ["--cached"])):
            print(f"\n{title}:")
            print(git(project, "diff", "--no-ext-diff", "--no-textconv", *extra, "--stat").rstrip() or "  No tracked changes")
    return semantic_execution.HandlerOutcome(0)


def handle_workflow(operation, projects, project, arguments, *, verbose=False, log=False, protected_state=False):
    level = dict(arguments).get('level')
    git_state(project)  # Require a repository root before any workflow.
    before = snapshot(project.path, protected=True) if protected_state else snapshot(project.path)
    session = ExecutionDirectory(projects, project, operation, level,
                                 verbose=verbose, log=log)
    print(f"{operation.capitalize()}: {project.name}", flush=True)
    if operation == "verify":
        print(f"Level: {level}", flush=True)
    try:
        adapter = adapters.resolve(project, session.run)
        checks = adapter.plan(operation, level, session.path)
        assert_integrity(project.path, before, "workflow discovery")
        results = execute(checks, project.path, baseline=before, session=session)
        return semantic_execution.HandlerOutcome(render(results), tuple(results))
    finally:
        try:
            assert_integrity(project.path, before, "workflow")
        finally:
            try:
                session.retain()
            finally:
                session.cleanup()


def handle_testflight(projects, project, arguments):
    if arguments: raise NattaError('Semantic TestFlight accepts no arguments')
    result = testflight.execute(projects, project)
    print(testflight.render(result, project))
    return semantic_execution.HandlerOutcome(result.exit_code, testflight_result=result)


def authorize_testflight(route, project):
    import authorization
    from execution import bounded_text
    return authorization.request_approval((
        'Project: ' + bounded_text(project.name),
        'Scheme: ' + bounded_text(project.execution.get('scheme', 'unconfigured')),
        'Configuration: Release'), heading='TestFlight upload',
        notice='Authorization required:\n' + authorization.testflight_notice(project.name))


def handle_commit(projects, project, arguments):
    if arguments:raise NattaError('Semantic commit accepts no arguments')
    result=commits.create(project)
    print(commits.render(result,project))
    return semantic_execution.HandlerOutcome(result.exit_code,commit_result=result)


def handler_bindings():
    # Static local callables only; never imports/argv derived from a selected ID.
    return MappingProxyType({
        'projects': handle_projects, 'doctor': handle_doctor, 'commit': handle_commit,
        'testflight': handle_testflight,
        'status': partial(handle_inspection, 'status'),
        'context': partial(handle_inspection, 'context'),
        'diff': partial(handle_inspection, 'diff'),
        'build': partial(handle_workflow, 'build', protected_state=True),
        'test': partial(handle_workflow, 'test', protected_state=True),
        'verify': partial(handle_workflow, 'verify', protected_state=True),
    })


def authorize_resolved(route, project):
    """Display only the frozen local action, then collect terminal approval."""
    import authorization
    from execution import bounded_text
    action = route.capability + '  ' + bounded_text(project.name if project else 'none')
    if route.arguments:
        action += ' ' + ' '.join(f'{key}={value}' for key, value in route.arguments)
    return authorization.request_approval((action,), notice=authorization.action_notice(
        route.capability, project.name if project else 'none', route.arguments))


def main(argv=None):
    parser = make_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    # A literal request after -- is not a CLI JSON flag. Keep argparse's human
    # diagnostics, but make expected usage failures machine-readable in JSON mode.
    json_requested = '--json' in argv[:argv.index('--') if '--' in argv else len(argv)]
    if json_requested:
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                args = parser.parse_args(argv)
            except SystemExit as exc:
                if exc.code != 2:
                    raise
                print(json.dumps({'status': 'invalid_request', 'error': 'invalid_arguments',
                                  'executed': False}, sort_keys=True))
                return 2
    else:
        args = parser.parse_args(argv)
    try:
        if args.command == 'do':
            import compound_execution
            result = compound_execution.run(args.goal, parser, lambda: load_registry(args.registry),
                handler_bindings(), confirm=args.confirm, json_mode=args.json)
            print(json.dumps(result.as_dict(), sort_keys=True) if args.json else compound_execution.render(result))
            return result.exit_code
        if args.command == 'plan':
            import planning
            result = planning.propose(args.goal, parser, load_registry(args.registry), handler_bindings())
            print(json.dumps(result.as_dict(), sort_keys=True) if args.json else planning.render(result))
            return 0 if result.status in ('planned', 'no_match') else 1
        if args.command == 'confinement':
            import macos_confinement
            code, result = macos_confinement.validate()
            print(json.dumps(result, sort_keys=True))
            return code
        if args.command in ('route', 'execute'):
            result = prepare_route(args.request, parser, args.registry)
            if args.command == 'route':
                print(json.dumps(result.as_dict(), sort_keys=True) if args.json else routing.render_route(result))
                return 0 if result.status in ('matched', 'no_match') else 1
            result = semantic_execution.dispatch(
                result, parser, lambda: load_registry(args.registry),
                routing.provider.authoritative_ids() - {'codex', 'route'},
                handler_bindings(), confirm=args.confirm,
                authorize=None if args.json else authorize_resolved)
            print(json.dumps(result.as_dict(), sort_keys=True) if args.json else semantic_execution.render_result(result))
            return result.exit_code
        if args.command == "codex":
            target = lookup(load_registry(args.registry), args.project).path if args.project else WORKSPACE_ROOT
            return launch_codex(target)
        projects = load_registry(args.registry)
        if args.command == "projects":
            return handle_projects(projects, None, ()).exit_code
        if args.command == "doctor":
            return handle_doctor(projects, None, (), json_mode=args.json).exit_code
        project = lookup(projects, args.project)
        if args.command == 'testflight':
            if args.check:
                result = testflight.execute(projects, project, check=True)
                print(json.dumps(result.as_dict(), sort_keys=True) if args.json else testflight.render(result, project))
                return result.exit_code
            ids = routing.provider.authoritative_ids() - {'codex', 'route'}
            route = routing.RouteResult('matched', 'testflight', project=project.alias, arguments_resolved=True)
            route = replace(route, execution_policy=policy.evaluate(route, ids,
                parameters.schema_for(parser, 'testflight'), projects))
            result = semantic_execution.dispatch(route, parser, lambda: load_registry(args.registry),
                ids, handler_bindings(), confirm=args.confirm,
                authorize=None if args.json else authorize_testflight)
            data = result.as_dict()
            # Direct capability result stays first-class; the shared gate retains
            # authoritative authorization/runtime fields, including after failures.
            if result.outcome and result.outcome.testflight_result:
                data = {**result.outcome.testflight_result.as_dict(), 'execution': data}
                if result.status in ('effect_violation', 'verification_unavailable'):
                    data.update(status='verification_failed', stage='verification', error=result.error)
            print(json.dumps(data, sort_keys=True) if args.json else
                  (result.report.rstrip() if result.execution_succeeded is True else semantic_execution.render_result(result)))
            return result.exit_code
        if args.command == 'commit':
            result=commits.create(project,args.message)
            print(json.dumps(result.as_dict(),sort_keys=True) if args.json else commits.render(result,project))
            return result.exit_code
        if args.command in ('build', 'test', 'verify'):
            arguments = (('level', args.level),) if args.command == 'verify' else ()
            return handle_workflow(args.command, projects, project, arguments,
                                   verbose=args.verbose, log=args.log).exit_code
        return handle_inspection(args.command, projects, project, ()).exit_code
    except (NattaError, OSError) as exc:
        if getattr(args, 'json', False):
            print(json.dumps(cli_failure(args), sort_keys=True))
        else:
            print(f"natta: {exc}", file=sys.stderr)
        return 1


def cli_failure(args):
    """Expected pre-dispatch failures reuse the command's existing result contract.

    Do not expose configuration/provider diagnostics (which may contain private
    paths), and do not catch unexpected programmer exceptions at this boundary.
    """
    if args.command in ('plan', 'do'):
        import planning
        plan = planning.PlanResult('invalid_plan', error='planner_configuration_invalid')
        if args.command == 'do':
            import compound_execution
            return compound_execution.CompoundResult(plan, 'invalid_plan', error=plan.error).as_dict()
        return plan.as_dict()
    if args.command == 'commit':
        return commits.CommitResult('failed', args.project, error='cli_configuration_invalid').as_dict()
    if args.command == 'testflight':
        return testflight.TestFlightResult('prerequisites_failed', args.project,
            stage='prerequisites', error='cli_configuration_invalid').as_dict()
    if args.command == 'doctor':
        return {'status': 'failed', 'core_ready': False, 'checks': [], 'error': 'cli_configuration_invalid'}
    route = routing.RouteResult('routing_error', None, error='parameter_configuration_invalid')
    if args.command == 'execute':
        return semantic_execution.ExecutionResult(route, 'routing_error', error=route.error).as_dict()
    return route.as_dict()


if __name__ == "__main__":
    sys.exit(main())
