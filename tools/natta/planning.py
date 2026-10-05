"""Bounded planning only. No handler invocation, process, snapshot or sandbox API."""
from dataclasses import dataclass, field, replace
from enum import Enum
import json
import confinement
import parameters
import planner_provider_v3 as planner_provider
from testflight_semantics import public_release_requested
import policy
import routing


class Reason(str,Enum):
    MALFORMED = 'malformed_plan'
    TOO_MANY = 'too_many_steps'
    UNKNOWN_CAPABILITY = 'unknown_capability'
    UNSUPPORTED_CAPABILITY = 'unsupported_capability'
    UNKNOWN_PROJECT = 'unknown_project'
    AMBIGUOUS_PROJECT = 'ambiguous_project'
    INVALID_ARGUMENT = 'invalid_argument'
    FORBIDDEN = 'forbidden_step'
    MISSING_HANDLER = 'missing_handler'
    CONFIGURATION = 'planner_configuration_invalid'


class InvalidPlan(ValueError):
    def __init__(self,reason):self.reason=reason;super().__init__(reason.value)


@dataclass(frozen=True)
class Step:
    index: int
    capability: str
    project: str
    arguments: tuple[tuple[str,int],...]
    execution_policy: policy.PolicyResult
    confinement_required: bool
    runtime_verification_required: bool

    def as_dict(self):
        return {'index':self.index,'capability':self.capability,'project':self.project,
            'arguments':dict(self.arguments),'arguments_resolved':True,'executed':False,
            'execution_policy':self.execution_policy.as_dict(),
            'authorization':self.execution_policy.authorization.value,
            'confinement':{'required':self.confinement_required,'applied':False,
                'mode':'inspection' if self.confinement_required else None},
            'runtime_verification_required':self.runtime_verification_required}


@dataclass(frozen=True)
class PlanResult:
    status: str
    valid: bool = False
    steps: tuple[Step,...] = ()
    error: str | None = None
    invalid_step: int | None = None
    authorization: policy.Authorization = policy.Authorization.FORBIDDEN
    effects: frozenset[policy.Effect] = frozenset()
    external_requests: int = 0
    inference_seconds: float | None = None
    provider: str = field(default=planner_provider.MODEL,init=False)
    planner_version: str = planner_provider.VERSION
    experimental: bool = field(default=True,init=False)
    executed: bool = field(default=False,init=False)

    def as_dict(self):
        return {'status':self.status,'valid':self.valid,'provider':self.provider,
            'planner_version':self.planner_version,'experimental':True,'executed':False,
            'steps':[s.as_dict() for s in self.steps],'authorization':self.authorization.value,
            'effects':sorted(e.value for e in self.effects),'error':self.error,'invalid_step':self.invalid_step,
            'confinement':{'applied':False,'inspection_step_indexes':[s.index for s in self.steps if s.confinement_required]},
            'external_requests':self.external_requests,'inference_seconds':self.inference_seconds}


def parse(text):
    def pairs(items):
        if len(items)!=len({key for key,_ in items}):raise InvalidPlan(Reason.MALFORMED)
        return dict(items)
    try:
        if not isinstance(text,str) or len(text.encode())>65536:raise InvalidPlan(Reason.MALFORMED)
        value = json.loads(text,object_pairs_hook=pairs)
        pending = [(value, 0)]
        while pending:
            item, depth = pending.pop()
            if depth > 16:raise InvalidPlan(Reason.MALFORMED)
            if isinstance(item,dict):pending.extend((v,depth+1) for v in item.values())
            elif isinstance(item,list):pending.extend((v,depth+1) for v in item)
        return value
    except (ValueError,TypeError,UnicodeError,RecursionError):raise InvalidPlan(Reason.MALFORMED) from None


def canonical_project(reference,projects):
    if not isinstance(reference,str) or not 1<=len(reference)<=128:raise InvalidPlan(Reason.UNKNOWN_PROJECT)
    ref=parameters.normalized(reference)
    matches={p.alias for p in projects if ref in {parameters.normalized(x) for x in (p.alias,p.name,*p.aliases)}}
    if len(matches)>1:raise InvalidPlan(Reason.AMBIGUOUS_PROJECT)
    if not matches:raise InvalidPlan(Reason.UNKNOWN_PROJECT)
    return next(iter(matches))


def validate(candidate,parser,projects,bindings, *, provider_mode=planner_provider):
    """Atomic acceptance; on any error return no steps or partial summaries."""
    if (not isinstance(candidate,dict) or set(candidate)!={'status','steps'}
            or candidate['status'] not in ('planned','no_match') or not isinstance(candidate['steps'],list)):
        return PlanResult('invalid_plan',error=Reason.MALFORMED.value)
    raw=candidate['steps']
    if len(raw)>planner_provider.MAX_STEPS:return PlanResult('invalid_plan',error=Reason.TOO_MANY.value)
    if candidate['status']=='no_match':
        return PlanResult('no_match') if not raw else PlanResult('invalid_plan',error=Reason.MALFORMED.value)
    if not raw:return PlanResult('invalid_plan',error=Reason.MALFORMED.value)
    ids=planner_provider.shared.authoritative_ids()-{'codex','route'}
    try:policy.validate_catalog(ids)
    except (TypeError,ValueError):return PlanResult('invalid_plan',error=Reason.CONFIGURATION.value)
    steps=[]
    for index,item in enumerate(raw,1):
        try:
            if not isinstance(item,dict) or set(item)!={'capability','project','arguments'}:raise InvalidPlan(Reason.MALFORMED)
            cap=item['capability']
            if not isinstance(cap,str):raise InvalidPlan(Reason.MALFORMED)
            if cap not in ids:raise InvalidPlan(Reason.UNKNOWN_CAPABILITY)
            if cap not in provider_mode.ALLOWED:raise InvalidPlan(Reason.UNSUPPORTED_CAPABILITY)
            if not callable(bindings.get(cap)):raise InvalidPlan(Reason.MISSING_HANDLER)
            project=canonical_project(item['project'],projects)
            schema=parameters.schema_for(parser,cap)
            args=item['arguments']
            if not isinstance(args,dict):raise InvalidPlan(Reason.INVALID_ARGUMENT)
            if schema.level_choices:
                if set(args)!={'level'} or type(args['level']) is not int or args['level'] not in schema.level_choices:
                    raise InvalidPlan(Reason.INVALID_ARGUMENT)
            elif args:raise InvalidPlan(Reason.INVALID_ARGUMENT)
            arguments=tuple(sorted(args.items()))
            route=routing.RouteResult('matched',cap,project=project,arguments=arguments,arguments_resolved=True)
            decision=policy.evaluate(route,ids,schema,projects)
            if (not decision.eligible or decision.authorization not in (policy.Authorization.AUTOMATIC,policy.Authorization.EXPLICIT)
                    or decision.effects & policy.forbidden_effects(cap)):raise InvalidPlan(Reason.FORBIDDEN)
            if not schema.project_required:raise InvalidPlan(Reason.CONFIGURATION)
            steps.append(Step(index,cap,project,arguments,decision,cap not in confinement.UNCONFINED_TARGETS,True))
        except InvalidPlan as exc:return PlanResult('invalid_plan',error=exc.reason.value,invalid_step=index)
        except (TypeError,ValueError,KeyError):return PlanResult('invalid_plan',error=Reason.CONFIGURATION.value,invalid_step=index)
    authorization=policy.Authorization.EXPLICIT if any(s.execution_policy.authorization==policy.Authorization.EXPLICIT for s in steps) else policy.Authorization.AUTOMATIC
    effects=frozenset(e for s in steps for e in s.execution_policy.effects)
    return PlanResult('planned',True,tuple(steps),authorization=authorization,effects=effects,planner_version=provider_mode.VERSION)


def propose(goal,parser,projects,bindings, *, provider_mode=planner_provider):
    if not isinstance(goal,str) or not goal.strip() or len(goal)>8192:
        return PlanResult('invalid_request',error='invalid_goal')
    reply=provider_mode.propose(goal,projects)
    if reply.error:
        errors={'planner_configuration_unavailable','provider_failure','provider_timeout','boundary_violation','malformed_output'}
        result=PlanResult('provider_error',error=reply.error if reply.error in errors else 'provider_failure')
    else:
        try:result=validate(parse(reply.text),parser,projects,bindings,provider_mode=provider_mode)
        except InvalidPlan as exc:result=PlanResult('invalid_plan',error=exc.reason.value)
    if (not reply.error and 'testflight' in provider_mode.ALLOWED and public_release_requested(goal)):
        result = PlanResult('no_match')
    return replace(result,external_requests=reply.external_requests,inference_seconds=reply.seconds,planner_version=provider_mode.VERSION)


def render(result):
    lines=['Plan',f'  status: {result.status}']
    for step in result.steps:
        args=' '.join(f'{k}={v}' for k,v in step.arguments)
        lines.append(f'  {step.index}. {step.capability} {step.project}'+(' '+args if args else ''))
    lines.extend([f'Authorization: {result.authorization.value}','Executed: no','Experimental: yes'])
    if result.error:lines.append('Error: '+result.error)
    return '\n'.join(lines)
