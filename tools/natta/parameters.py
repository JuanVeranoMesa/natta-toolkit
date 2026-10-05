"""Local bounded parameter resolution. No provider, filesystem or execution access."""
import argparse
from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class ParameterSchema:
    project_required: bool
    level_choices: tuple[int, ...] = ()


@dataclass(frozen=True)
class Resolution:
    project: str | None = None
    arguments: tuple[tuple[str, int], ...] = ()
    arguments_resolved: bool = False
    missing_arguments: tuple[str, ...] = ()
    resolution_error: str | None = None


def schema_for(parser, capability):
    """Read existing argparse registrations; refuse unsupported required parameters.

    argparse exposes registered subparsers/actions through private collections.
    Keep that introspection here, covered by tests against the real parser.
    Optional execution flags are deliberately not extracted.
    """
    commands = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    if capability not in commands.choices or capability in ('route', 'codex'):
        raise ValueError('Unknown parameter schema')
    actions = commands.choices[capability]._actions
    required = {action.dest: action for action in actions if action.required}
    if set(required) - {'project', 'level'}:
        raise ValueError('Unsupported required parameter')
    level = required.get('level')
    if level is not None and (level.type is not int or not level.choices or
                             any(type(value) is not int for value in level.choices)):
        raise ValueError('Unsupported level domain')
    return ParameterSchema('project' in required, tuple(level.choices) if level is not None else ())


def normalized(value):
    value = unicodedata.normalize('NFKC', value).casefold()
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', value)).strip()


def project_matches(request, projects):
    text = normalized(request)
    matches = set()
    for project in projects:
        for identifier in (project.alias, *project.aliases, project.name):
            phrase = normalized(identifier)
            if phrase and re.search(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', text):
                matches.add(project.alias)
    return sorted(matches)


def explicit_levels(request):
    # Read a complete value token, so 2.5, 2/3, 2abc and negative levels fail
    # validation instead of becoming a valid numeric prefix. Sentence punctuation
    # is permitted after an otherwise explicit value.
    tokens = re.findall(r'(?<!\w)level\s+(\S+)', normalized(request))
    return [token.rstrip('.,;:!?)]}') for token in tokens]


def resolve(request, projects, schema):
    """Only registry IDs and parser-domain integers can leave this function."""
    project = None
    missing = []
    error = None
    arguments = ()
    if schema.project_required:
        matches = project_matches(request, projects)
        if len(matches) == 1:
            project = matches[0]
        else:
            missing.append('project')
            if matches:
                error = 'ambiguous_project'
    if schema.level_choices:
        tokens = explicit_levels(request)
        if not tokens:
            missing.append('level')
        elif any(token not in {str(value) for value in schema.level_choices} for token in tokens):
            error = error or 'invalid_argument'
        elif len(set(tokens)) != 1:
            error = error or 'conflicting_level'
        else:
            arguments = (('level', int(tokens[0])),)
    return Resolution(project, arguments, not missing and error is None, tuple(missing), error)
