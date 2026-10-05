"""Explicit counted frozen planner evaluation. No execution or fallback."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import natta
import planner_provider as provider
import planning

ROOT=Path(__file__).resolve().parent
CORPUS=ROOT/'evaluation/planner-corpus-v1.json'
CONTROLS=ROOT/'evaluation/planner-controls-v1.json'


def load_controls(projects):
    controls=json.loads(CONTROLS.read_text());corpus=json.loads(CORPUS.read_text())
    if hashlib.sha256(CORPUS.read_bytes()).hexdigest()!=controls['corpus_sha256']:
        raise ValueError('Frozen corpus hash mismatch')
    checks={'planner_capabilities_sha256':hashlib.sha256(provider.contents(projects)['capabilities.json'].encode()).hexdigest(),
            'planner_policy_sha256':hashlib.sha256(provider.POLICY.encode()).hexdigest(),
            'planner_prompt_sha256':hashlib.sha256(provider.PROMPT.encode()).hexdigest(),
            'planner_schema_sha256':hashlib.sha256(json.dumps(provider.output_schema(projects),sort_keys=True).encode()).hexdigest()}
    if any(controls[key]!=value for key,value in checks.items()):raise ValueError('Frozen planner controls mismatch')
    cases=corpus['cases']
    if (corpus['planner_version']!=provider.VERSION or corpus['max_steps']!=provider.MAX_STEPS
            or len(cases)!=controls['total_cases'] or len({c['id'] for c in cases})!=len(cases)):
        raise ValueError('Frozen corpus/version invalid')
    parser=natta.make_parser();bindings=natta.handler_bindings()
    for case in cases:
        gold=planning.validate(case['expected'],parser,projects,bindings,provider_mode=provider)
        if gold.status!=case['expected']['status']:raise ValueError('Gold does not match current controls')
    return controls,cases


def compact(result):
    return {'status':result.status,'steps':[{'capability':s.capability,'project':s.project,
        'arguments':dict(s.arguments)} for s in result.steps]}


def metrics(cases,results,projects):
    correct=[];sequence=[];project=[];arguments=[];failures=0;incorrect=[]
    group={name:[] for name in ('unsupported','adversarial')}
    allowed_projects={p.alias for p in projects}
    unknown_caps=unknown_projects=0
    for case,result in zip(cases,results):
        actual=compact(result);gold=case['expected'];same=actual['status']==gold['status']
        exact=actual==gold;correct.append(exact)
        for key,target in [('capability',sequence),('project',project),('arguments',arguments)]:
            target.append(same and [s[key] for s in actual['steps']]==[s[key] for s in gold['steps']])
        failure=result.status=='provider_error' or result.error=='malformed_plan'
        failures+=int(failure)
        if case['category'] in group:
            group[case['category']].append(not failure and result.status in ('no_match','invalid_plan') and not result.steps)
        unknown_caps+=sum(s.capability not in provider.ALLOWED for s in result.steps)
        unknown_projects+=sum(s.project not in allowed_projects for s in result.steps)
        if not exact:incorrect.append({'id':case['id'],'expected':gold,'actual':actual,'error':result.error})
    denominator=len(cases)
    ratio=lambda xs:sum(xs)/denominator
    return {'total_cases':denominator,'complete_cases':len(results)-failures,'provider_failures':failures,
        'exact_plan_correct':sum(correct),'exact_plan_accuracy':ratio(correct),
        'capability_sequence_accuracy':ratio(sequence),'project_accuracy':ratio(project),'argument_accuracy':ratio(arguments),
        **{name+'_rejection':sum(values)/len(values) if values else None for name,values in group.items()},
        'accepted_unknown_capabilities':unknown_caps,'accepted_unknown_projects':unknown_projects,'incorrect_cases':incorrect}


def assessment(values,gates):
    checks={
        'complete':values['complete_cases']==gates['complete_cases'],
        'provider_failures':values['provider_failures']==gates['provider_failures'],
        'exact_plan_accuracy':values['exact_plan_accuracy']>=gates['minimum_exact_plan_accuracy'],
        'unsupported_rejection':values['unsupported_rejection']==gates['unsupported_rejection'],
        'adversarial_rejection':values['adversarial_rejection']==gates['adversarial_rejection'],
        'accepted_unknown_capabilities':values['accepted_unknown_capabilities']==0,
        'accepted_unknown_projects':values['accepted_unknown_projects']==0}
    return {'passed':all(checks.values()),'checks':checks,
        'compound_execution_enabled':False,'interpretation':'Planner acceptance gates passed; no compound execution implemented.'
         if all(checks.values()) else 'Planner remains experimental; no compound execution permitted.'}


def run(mode,authorization,output,registry=natta.DEFAULT_REGISTRY):
    if mode not in ('smoke','evaluate'):raise ValueError('Unknown evaluation mode')
    projects=natta.load_registry(registry);controls,cases=load_controls(projects)
    if mode=='smoke':cases=cases[:1]
    expected=len(cases)
    if type(authorization) is not int or authorization!=expected:
        raise ValueError(f'Exact --allow-external-requests {expected} required')
    output=Path(output)
    if output.resolve()!=output.absolute() or output.is_symlink():raise ValueError('Unsafe result path')
    for p in output.parents:
        if p.is_symlink():raise ValueError('Unsafe result parent')
    # Exclusive reservation happens before any model call. Never overwrite artifacts.
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    results=[]
    artifact={'mode':mode,'model':provider.MODEL,'planner_version':provider.VERSION,'complete':False,
        'planned_external_requests':expected,'actual_external_requests':0,
        'selected_case_ids':[c['id'] for c in cases],'predeclared_acceptance':controls['predeclared_acceptance'],
        'control_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (CORPUS,CONTROLS)},
        'source_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'planning.py',ROOT/'planner_provider.py',ROOT/'planner_evaluation.py',ROOT/'router_codex.py',ROOT/'config/routing.json')},
        'project_metadata':provider.project_metadata(projects),'predictions':[]}
    try:
        for case in cases:
            result=planning.propose(case['request'],natta.make_parser(),projects,natta.handler_bindings(),provider_mode=provider)
            results.append(result)
            artifact['actual_external_requests']+=result.external_requests
            artifact['predictions'].append({'id':case['id'],'result':result.as_dict()})
        values=metrics(cases,results,projects);artifact['metrics']=values
        artifact['complete']=values['complete_cases']==len(cases)
        artifact['assessment']=assessment(values,controls['predeclared_acceptance']) if mode=='evaluate' else {'passed':artifact['complete'] and values['exact_plan_accuracy']==1,
            'interpretation':'One-case smoke only; not planner acceptance.'}
        times=[r.inference_seconds for r in results if r.inference_seconds is not None]
        artifact['new_call_timing']={'total':sum(times),'mean':statistics.mean(times) if times else None}
    finally:
        with os.fdopen(fd,'w') as stream:json.dump(artifact,stream,indent=2);stream.write('\n')
    return artifact


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('smoke','evaluate'))
    parser.add_argument('--allow-external-requests',required=True,type=int)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--registry',type=Path,default=natta.DEFAULT_REGISTRY)
    args=parser.parse_args(argv)
    try:artifact=run(args.mode,args.allow_external_requests,args.output,args.registry)
    except (ValueError,OSError,KeyError) as exc:parser.exit(1,'planner evaluation: '+str(exc)+'\n')
    print(json.dumps(artifact.get('metrics',{}),indent=2))
    return 0 if artifact['complete'] and artifact['assessment']['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
