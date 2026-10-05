"""Private sandbox worker. No natural-language request or provider invocation."""
import contextlib
import json
import re
from pathlib import Path
import sys

import natta
from execution import NattaError
from semantic_execution import BoundedOutput


def main():
    data = json.loads(sys.stdin.read(65537))
    if set(data) != {'capability', 'project', 'arguments', 'projects'}:
        raise ValueError('Invalid worker envelope')
    cap, arguments = data['capability'], data['arguments']
    if cap not in ('status','context','diff') or arguments != {}:
        raise ValueError('Unsupported worker operation')
    projects = []
    for p in data['projects']:
        if set(p) != {'alias','aliases','name','path','type','files','execution'}:
            raise ValueError('Invalid worker project')
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*',p['alias']):
            raise ValueError('Invalid canonical project ID')
        root=Path(p['path'])
        if not root.is_absolute() or root.resolve()!=root:
            raise ValueError('Noncanonical worker root')
        files={k:Path(v) for k,v in p['files'].items()}
        if any(k not in (*natta.DOCS,'version_source') or not v.is_relative_to(root) or v.resolve()!=v for k,v in files.items()):
            raise ValueError('Invalid worker metadata path')
        execution=natta.adapters.parse_execution(p['type'],p['execution'],root)
        projects.append(natta.Project(p['alias'],tuple(p['aliases']),p['name'],root,p['type'],files,execution))
    projects=tuple(projects)
    project = next(p for p in projects if p.alias == data['project'])
    if not project.path.is_absolute() or project.path.resolve() != project.path:
        raise ValueError('Noncanonical worker project')
    output = BoundedOutput()
    print(json.dumps({'event':'started'}), flush=True)
    error = None
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            outcome = natta.handler_bindings()[cap](projects, project, ())
        code = outcome.exit_code
        checks = [{'name':r.check.name, 'status':r.status, 'exit_code':r.exit_code,
                   'duration':r.duration, 'detail_lines':list(r.detail_lines[:8]),
                   'failing_tests':list(r.failing_tests[:5])} for r in outcome.checks]
    except Exception as exc:
        code, checks, error = 1, [], 'handler_error'
        if isinstance(exc,(NattaError,OSError)):
            from execution import bounded_text
            output.write('natta: '+bounded_text(str(exc),1000)+'\n')
    print(json.dumps({'event':'result','exit_code':code,'checks':checks,'report':output.value(),
                      'report_truncated':output.truncated,'error':error}),flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
