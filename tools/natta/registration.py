"""Project registration: add, remove and validate entries in the user's registry.

Registration is configuration, not a capability. These commands write only the
registry file, never a registered project. Every write is staged next to the
registry, re-parsed, checked to change exactly one entry and fully validated by
natta.load_registry before it atomically replaces the original. Remove deletes
only the target entry's lines, so comments elsewhere in the file are preserved.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib

import adapters
from execution import NattaError, bounded_text, git_environment

DOC_CANDIDATES = {
    'agents': ('AGENTS.md',),
    'architecture': ('ARCHITECTURE.md', 'docs/ARCHITECTURE.md'),
    'roadmap': ('ROADMAP.md', 'docs/ROADMAP.md'),
}
ALIAS = re.compile(r'[a-z0-9][a-z0-9_-]*')
HEADER = re.compile(r'\s*\[\[\s*projects\s*\]\]\s*(#.*)?')
FILLER = re.compile(r'\s*(#.*)?')
FIELD_ORDER = ('alias', 'aliases', 'name', 'path', 'type', 'agents', 'architecture', 'roadmap', 'version_source')
EXECUTION_ORDER = ('project', 'workspace', 'scheme', 'configuration', 'unit_targets', 'ui_targets',
                   'test_targets', 'ui_tests')


def toml_value(value):
    # JSON string escapes are a subset of TOML basic-string escapes; the staged
    # file is re-parsed, so anything TOML rejects fails before replacement.
    if isinstance(value, list):
        return '[' + ', '.join(json.dumps(item, ensure_ascii=False) for item in value) + ']'
    return json.dumps(value, ensure_ascii=False)


def render_entry(entry):
    lines = ['[[projects]]', *(f'{key} = {toml_value(entry[key])}' for key in FIELD_ORDER if key in entry)]
    if entry.get('execution'):
        lines += ['', '[projects.execution]',
                  *(f'{key} = {toml_value(entry["execution"][key])}' for key in EXECUTION_ORDER
                    if key in entry['execution'])]
    return '\n'.join(lines) + '\n'


def display_path(path):
    """Prefer ~/... so a registry stays valid for the same layout under another account."""
    home = Path.home().resolve()
    return '~/' + path.relative_to(home).as_posix() if path.is_relative_to(home) and path != home else str(path)


def repository_root(path):
    proc = subprocess.run(('git', '--no-optional-locks', '-c', 'core.fsmonitor=false', '-C', str(path),
                           'rev-parse', '--show-toplevel'),
                          capture_output=True, text=True, env=git_environment(), timeout=30)
    if proc.returncode or not proc.stdout.strip():
        raise NattaError(f'{path} is not inside a Git repository')
    return Path(proc.stdout.strip()).resolve()


def detect_type(root):
    return 'ios-xcode' if any(root.glob('*.xcodeproj')) or any(root.glob('*.xcworkspace')) else 'generic-git'


def detect_docs(root):
    """The first existing candidate for each document; none is invented."""
    docs = {}
    for key, names in DOC_CANDIDATES.items():
        found = next((name for name in names if (root / name).is_file()), None)
        if found:
            docs[key] = found
    return docs


def detect_container(root):
    workspaces, projects = sorted(root.glob('*.xcworkspace')), sorted(root.glob('*.xcodeproj'))
    if len(workspaces) == 1:
        return 'workspace', workspaces[0].name
    if not workspaces and len(projects) == 1:
        return 'project', projects[0].name
    found = ', '.join(p.name for p in (*workspaces, *projects)) or 'none'
    raise NattaError(f'Cannot choose an Xcode container automatically (found: {found}); pass --container')


def detect_scheme(root, container):
    schemes = sorted(p.stem for p in (root / container).glob('xcshareddata/xcschemes/*.xcscheme'))
    if len(schemes) != 1:
        raise NattaError(f'Cannot choose a shared scheme automatically (found: {", ".join(schemes) or "none"}); '
                         'pass --scheme')
    return schemes[0]


def xcode_execution(root, args):
    if not (args.unit_target or args.ui_target or args.test_target or args.ui_test):
        if args.container or args.scheme:
            raise NattaError('Xcode execution needs at least one --unit-target; omit --container/--scheme '
                             'for an inspection-only registration')
        return {}
    if args.container:
        if Path(args.container).suffix not in ('.xcodeproj', '.xcworkspace'):
            raise NattaError('--container must name a .xcodeproj or .xcworkspace')
        key = 'workspace' if Path(args.container).suffix == '.xcworkspace' else 'project'
        container = args.container
    else:
        key, container = detect_container(root)
    execution = {key: container, 'scheme': args.scheme or detect_scheme(root, container),
                 'configuration': args.configuration}
    for name, values in (('unit_targets', args.unit_target), ('ui_targets', args.ui_target),
                         ('test_targets', args.test_target or args.unit_target), ('ui_tests', args.ui_test)):
        if values:
            execution[name] = list(values)
    return execution


def build_entry(args):
    if not ALIAS.fullmatch(args.alias):
        raise NattaError(f'Invalid alias {bounded_text(args.alias, 64)!r}: use lowercase letters, digits, '
                         'hyphens or underscores, starting with a letter or digit')
    path = Path(args.path).expanduser().resolve()
    if not path.is_dir():
        raise NattaError(f'Project path is not a directory: {path}')
    root = repository_root(path)
    if root != path:
        raise NattaError(f'Project path must be the repository root: {root}')
    kind = args.type or detect_type(root)
    if kind not in adapters.ADAPTERS:
        raise NattaError(f'Unsupported adapter type {kind!r}; supported: {", ".join(sorted(adapters.ADAPTERS))}')
    entry = {'alias': args.alias, 'name': args.name or root.name, 'path': display_path(root), 'type': kind}
    if args.extra_alias:
        entry['aliases'] = list(args.extra_alias)
    entry.update(detect_docs(root))
    if kind == 'ios-xcode':
        execution = xcode_execution(root, args)
        if execution:
            adapters.parse_execution(kind, execution, root)
            entry['execution'] = execution
    elif any((args.container, args.scheme, args.unit_target, args.ui_target, args.test_target, args.ui_test)):
        raise NattaError(f'{kind}: Xcode execution options are not supported')
    return entry


def read(registry):
    try:
        text = registry.read_text()
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        raise NattaError(f'Cannot read registry {registry}: {exc}') from exc
    try:
        return text, tomllib.loads(text)
    except ValueError as exc:
        raise NattaError(f'Cannot read registry {registry}: {exc}') from exc


def replace_registry(registry, text, expected_projects, load_registry):
    """Stage, verify and atomically install a new registry text."""
    if tomllib.loads(text).get('projects') != expected_projects:
        raise NattaError('Registry update would change entries other than the requested one; edit the file manually')
    registry.parent.mkdir(parents=True, exist_ok=True)
    fd, staged = tempfile.mkstemp(prefix='.projects-', suffix='.toml', dir=registry.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(text)
        if registry.exists():
            os.chmod(staged, registry.stat().st_mode & 0o777)
        # Relative paths resolve against the registry directory, which the staged file shares.
        load_registry(staged)
        os.replace(staged, registry)
    except BaseException:
        Path(staged).unlink(missing_ok=True)
        raise


def add(registry, args, load_registry):
    text, data = read(registry)
    entry = build_entry(args)
    if text is None:
        text, existing = 'schema_version = 1\n', []
    else:
        existing = data.get('projects', [])
    used = {alias for item in existing if isinstance(item, dict)
            for alias in (item.get('alias'), *item.get('aliases', []))}
    clash = sorted(used & {entry['alias'], *entry.get('aliases', [])})
    if clash:
        raise NattaError(f'Alias already registered: {", ".join(clash)}')
    separator = '' if text.endswith('\n\n') else '\n' if text.endswith('\n') else '\n\n'
    replace_registry(registry, text + separator + render_entry(entry), [*existing, entry], load_registry)
    return entry


def remove(registry, alias, load_registry):
    text, data = read(registry)
    if text is None:
        raise NattaError(f'No registry at {registry}')
    existing = data.get('projects', [])
    index = next((i for i, item in enumerate(existing) if isinstance(item, dict) and item.get('alias') == alias), None)
    if index is None:
        raise NattaError(f'Unknown project {bounded_text(alias, 64)!r}; remove takes the canonical alias')
    if len(existing) == 1:
        raise NattaError('Refusing to remove the only registered project; a registry needs at least one')
    lines = text.splitlines(keepends=True)
    headers = [i for i, line in enumerate(lines) if HEADER.fullmatch(line.rstrip('\n'))]
    if len(headers) != len(existing):
        raise NattaError('Registry layout is not one [[projects]] header per entry; edit the file manually')
    start = headers[index]
    end = headers[index + 1] if index + 1 < len(headers) else len(lines)
    # Comment lines directly above a header belong to that entry; so do the
    # comments and blank lines directly above the next one.
    while start > 0 and lines[start - 1].lstrip().startswith('#'):
        start -= 1
    while index + 1 < len(headers) and end - 1 > start and FILLER.fullmatch(lines[end - 1].rstrip('\n')):
        end -= 1
    remaining = existing[:index] + existing[index + 1:]
    replace_registry(registry, ''.join(lines[:start] + lines[end:]), remaining, load_registry)
    return existing[index]


def validate(registry, load_registry):
    """Load the registry, then check every project is a repository root with its documents."""
    projects = load_registry(registry)
    problems = []
    for project in projects:
        if not project.path.is_dir():
            problems.append(f'{project.alias}: project directory missing ({project.path})')
            continue
        try:
            root = repository_root(project.path)
        except NattaError as exc:
            problems.append(f'{project.alias}: {exc}')
            continue
        if root != project.path:
            problems.append(f'{project.alias}: path is not the repository root ({root})')
        problems += [f'{project.alias}: {key} missing ({file})' for key, file in project.files.items()
                     if not file.is_file()]
    return projects, problems
