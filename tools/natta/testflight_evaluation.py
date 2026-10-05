"""Small additive frozen TestFlight evaluation; reuses the counted extension runner."""
import argparse
import hashlib
import json
from pathlib import Path

import commit_evaluation as evaluation
import natta
import planner_provider_v3 as planner
import router_codex as router

ROOT = Path(__file__).resolve().parent


def sources(mode, projects):
    if mode == 'routing': return router.workspace_contents('concise', router.TOOLKIT_IDS)
    return {'policy': planner.POLICY, 'prompt': planner.PROMPT,
            'schema': json.dumps(planner.output_schema(projects), sort_keys=True),
            'capabilities': planner.contents(projects)['capabilities.json']}


def controls(mode, projects, suite='baseline'):
    if mode not in ('routing', 'planner') or suite != 'baseline': raise ValueError('Unknown TestFlight suite')
    corpus_path = ROOT / f'evaluation/testflight-{mode}-v1-corpus.json'
    control_path = ROOT / f'evaluation/testflight-{mode}-v1-controls.json'
    corpus = json.loads(corpus_path.read_text()); control = json.loads(control_path.read_text())
    digest = lambda b: hashlib.sha256(b).hexdigest()
    hashes = {k:digest(v.encode()) for k,v in sources(mode, projects).items()}
    if (digest(corpus_path.read_bytes()) != control['corpus_sha256']
            or hashes != control['provider_controls_sha256']
            or corpus['identity'] != control['identity']
            or len(corpus['cases']) != control['total_cases']
            or len({c['id'] for c in corpus['cases']}) != len(corpus['cases'])):
        raise ValueError('Frozen TestFlight controls mismatch')
    return corpus, control, {p.name:digest(p.read_bytes()) for p in (corpus_path, control_path)}


def run(mode, authorization, output, registry=natta.DEFAULT_REGISTRY):
    return evaluation.run(mode, authorization, output, registry, control_loader=controls,
                          planner_mode=planner, routing_ids=router.TOOLKIT_IDS, additional_source_files=(
                              'testflight.py', 'testflight_semantics.py', 'planner_provider_v3.py',
                              'testflight_evaluation.py', 'compound_execution.py', 'semantic_execution.py'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('routing', 'planner'))
    parser.add_argument('--allow-external-requests', required=True, type=int)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--registry', type=Path, default=natta.DEFAULT_REGISTRY)
    args = parser.parse_args(argv)
    try: artifact = run(args.mode, args.allow_external_requests, args.output, args.registry)
    except (ValueError, OSError, KeyError) as exc: parser.exit(1, 'TestFlight evaluation: ' + str(exc) + '\n')
    print(json.dumps({'metrics': artifact['metrics'], 'assessment': artifact['assessment']}, indent=2))
    return 0 if artifact['assessment']['passed'] else 1


if __name__ == '__main__': raise SystemExit(main())
