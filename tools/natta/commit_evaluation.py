"""Frozen commit routing/planning extension evaluation. No handlers or Git mutation."""
import argparse
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import time
import natta
import planning
import planner_provider_v2 as planner
import router_codex as router
import routing

ROOT=Path(__file__).resolve().parent


def sources(mode,projects):
    if mode=='planner':
        return {'policy':planner.POLICY,'prompt':planner.PROMPT,
                'schema':json.dumps(planner.output_schema(projects),sort_keys=True),
                'capabilities':planner.contents(projects)['capabilities.json']}
    return router.workspace_contents('concise',router.PRODUCTION_IDS)


def controls(mode,projects,suite="baseline"):
    if suite not in ('baseline','regression','holdout','regression-r2','r1-regression-r2','holdout-r2') or (mode!='routing' and suite!='baseline'):
        raise ValueError('Unknown evaluation suite')
    stem=(f'commit-{mode}-v2' if suite=='baseline' else
          f'commit-routing-{suite}' if suite.endswith('-r2') else f'commit-routing-{suite}-r1')
    corpus_name={'regression':'commit-routing-v2-corpus.json',
                 'regression-r2':'commit-routing-v2-corpus.json',
                 'r1-regression-r2':'commit-routing-holdout-r1-corpus.json'}.get(suite,stem+'-corpus.json')
    corpus_path=ROOT/'evaluation'/corpus_name
    control_path=ROOT/f'evaluation/{stem}-controls.json'
    corpus=json.loads(corpus_path.read_text());control=json.loads(control_path.read_text())
    digest=lambda value:hashlib.sha256(value).hexdigest()
    hashes={k:digest(v.encode()) for k,v in sources(mode,projects).items()}
    if (digest(corpus_path.read_bytes())!=control['corpus_sha256'] or hashes!=control['provider_controls_sha256']
            or len(corpus['cases'])!=control['total_cases'] or corpus['identity']!=control.get('corpus_identity',control['identity'])
            or len({c['id'] for c in corpus['cases']})!=len(corpus['cases'])):
        raise ValueError('Frozen commit extension controls mismatch')
    return corpus,control,{p.name:digest(p.read_bytes()) for p in (corpus_path,control_path)}


def compact_route(result):
    return {'capability':result.capability,'project':result.project,
            'arguments':dict(result.arguments),'arguments_resolved':result.arguments_resolved}


def compact_plan(result):
    return {'status':result.status,'steps':[{'capability':s.capability,'project':s.project,
        'arguments':dict(s.arguments)} for s in result.steps]}


def run(mode,authorization,output,registry=natta.DEFAULT_REGISTRY,*,suite="baseline",
        control_loader=controls, planner_mode=planner, routing_ids=router.PRODUCTION_IDS,
        additional_source_files=()):
    if mode not in ('routing','planner'):raise ValueError('Unknown extension mode')
    projects=natta.load_registry(registry);corpus,control,hashes=control_loader(mode,projects,suite)
    cases=corpus['cases'];count=len(cases)
    if type(authorization) is not int or authorization!=count:
        raise ValueError(f'Exact --allow-external-requests {count} required')
    output=Path(output)
    if output.resolve()!=output.absolute() or any(p.is_symlink() for p in (output,*output.parents)):
        raise ValueError('Unsafe result path')
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    artifact={'identity':control['identity'],'model':router.MODEL,'complete':False,
        'planned_external_requests':count,'actual_external_requests':0,'executed':False,
        'predeclared_acceptance':control['predeclared_acceptance'],'control_hashes':hashes,
        'source_hashes':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
            ('natta.py','commits.py','router_codex.py','routing.py','planning.py','planner_provider.py','planner_provider_v2.py','parameters.py','policy.py','runtime_effects.py','execution.py','commit_evaluation.py',*additional_source_files)},
        'predictions':[]}
    correct=failures=0;incorrect=[];groups={g:[] for g in ('unsupported','adversarial')}
    calls=0;started=time.perf_counter()
    def counted(*args):
        nonlocal calls
        if calls>=count:raise ValueError('External request budget exceeded')
        calls+=1
        return router.run_provider(*args)
    factory=partial(router.CodexDecision,runner=counted)
    try:
        for case in cases:
            if mode=='planner':
                result=planning.propose(case['request'],natta.make_parser(),projects,natta.handler_bindings(),provider_mode=planner_mode)
                actual=compact_plan(result);calls+=result.external_requests
            else:
                result=natta.prepare_route(case['request'],natta.make_parser(),registry,
                    selector=lambda text:routing.select(text,decision_factory=factory,capability_ids=routing_ids))
                actual=compact_route(result)
            failure=result.status in ('provider_error','routing_error') or result.error=='malformed_plan'
            failures+=int(failure);exact=actual==case['expected'];correct+=int(exact)
            if case['category'] in groups:
                rejection=(result.status=='no_match' if mode=='routing' else result.status in ('no_match','invalid_plan') and not result.steps)
                groups[case['category']].append(bool(rejection and not failure))
            artifact['predictions'].append({'id':case['id'],'result':result.as_dict()})
            if not exact:incorrect.append({'id':case['id'],'expected':case['expected'],'actual':actual,'error':result.error})
        values={'total_cases':count,'complete_cases':count-failures,'provider_failures':failures,
            'exact_correct':correct,'exact_accuracy':correct/count,
            **{g+'_rejection':sum(v)/len(v) for g,v in groups.items()},'incorrect_cases':incorrect}
        if mode=='routing':
            values['capability_accuracy']=sum(p['result']['capability']==c['expected']['capability'] for p,c in zip(artifact['predictions'],cases))/count
        else:
            for key,label in (('capability','capability_sequence_accuracy'),('project','project_accuracy'),('arguments','argument_accuracy')):
                values[label]=sum(p['result']['status']==c['expected']['status'] and
                    [s[key] for s in p['result']['steps']]==[s[key] for s in c['expected']['steps']]
                    for p,c in zip(artifact['predictions'],cases))/count
        accepted_caps=routing_ids if mode=='routing' else planner_mode.ALLOWED
        accepted_projects={p.alias for p in projects}
        entries=[s for prediction in artifact['predictions'] for s in
                 ([prediction['result']] if mode=='routing' else prediction['result']['steps'])]
        values['accepted_unknown_capabilities']=sum(e['capability'] is not None and e['capability'] not in accepted_caps for e in entries)
        values['accepted_unknown_projects']=sum(e['project'] is not None and e['project'] not in accepted_projects for e in entries)
        gates=control['predeclared_acceptance']
        passed=(failures==0 and calls==count and correct/count>=gates['minimum_exact_accuracy']
            and all(values[g+'_rejection']==1 for g in groups)
            and values['accepted_unknown_capabilities']==0 and values['accepted_unknown_projects']==0)
        artifact.update(complete=failures==0 and len(artifact['predictions'])==count,metrics=values,
            assessment={'passed':passed,'compound_execution_enabled':False})
    finally:
        artifact.update(actual_external_requests=calls,total_seconds=time.perf_counter()-started)
        with os.fdopen(fd,'w') as stream:json.dump(artifact,stream,indent=2);stream.write('\n')
    return artifact


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('routing','planner'))
    parser.add_argument('--allow-external-requests',required=True,type=int)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--registry',type=Path,default=natta.DEFAULT_REGISTRY)
    parser.add_argument('--suite',choices=('baseline','regression','holdout','regression-r2','r1-regression-r2','holdout-r2'),default='baseline')
    args=parser.parse_args(argv)
    try:artifact=run(args.mode,args.allow_external_requests,args.output,args.registry,suite=args.suite)
    except (ValueError,OSError,KeyError) as exc:parser.exit(1,'commit evaluation: '+str(exc)+'\n')
    print(json.dumps({'metrics':artifact['metrics'],'assessment':artifact['assessment']},indent=2))
    return 0 if artifact['assessment']['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
