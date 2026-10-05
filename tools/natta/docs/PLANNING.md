> Standalone v1: current contracts are preserved below. Phase-labelled sections
> record historical domains/rationale; private empirical artifacts are excluded.
> See [documentation map and evaluation provenance](../../../docs/EVALUATION.md).

> Current Phase 8B addition: optional `natta do` executes a frozen bounded plan.
> Earlier statements below that compound execution is absent describe pre-8B scope.
> `route`, `execute` and `plan` retain their existing boundaries; direct CLI remains
> primary. See [compound execution contract](COMPOUND_EXECUTION.md).

# Current planner version

The source implementation evaluated the bounded planner before adoption.
Private result artifacts are excluded; public corpora and controls are sanitized
reproducible definitions. Production planning uses **testflight-planner-v1**,
adding TestFlight to the commit-v2 domain. The experimental result field, Max4
and non-execution boundary remain unchanged.
See [COMMIT.md](COMMIT.md) for commit's explicit authorization, Git effect union,
whole-goal push/custom-message rejection and exact focused evaluation commands.
The remainder documents the accepted six-capability v1 contract and its preserved
historical evaluator; it must not be read as commit or TestFlight planning evidence.
Current production permits status/context/diff/build/test/verify/commit/testflight,
uses planner_provider_v3.py, and shares local registry/schema/policy/handler
validation. Projects/doctor remain excluded. See TESTFLIGHT.md for additive controls.

# Historical Phase 8A v1 contract (superseded domain/provider)

`natta plan GOAL` and `natta plan --json GOAL` propose an experimental linear plan.
They never execute a step. `route` continues to report one capability; `execute`
continues to authorize and execute one capability through its existing gate.
In this historical Phase 8A scope, do was not implemented; current bounded do is documented separately. No confirmation flag exists on plan.

## Separate provider mode

`planner_provider.py` uses explicit gpt-6-luna and version `phase8a-v1`. Its prompt
and output schema are separate from the accepted single-capability router.
Historical capability-selection evaluation and order-stability acceptance do
not validate orchestration, project suggestions or argument suggestions.

One goal makes one planner request, with no per-step classification, recursive
calls, retries, fallback or execution. The existing Codex isolation argv builder,
CLI-feature probe and fail-closed JSONL parser are reused. Only the developer
policy and structured output schema differ. Read-only sandbox, approval=never,
ignored user config/rules, skipped host skill discovery, disabled tools/features,
web and plugins were disabled. Provider child-shell environment inheritance is
disabled; the provider process itself inherits the host environment and is not
independently containerized by Natta.
Prohibited action/error items and unknown event/item types fail closed.

The separate sterile workspace is generated at
`~/Library/Application Support/NattaToolkit/planner-codex/`. Its only artifacts are
AGENTS.md, capabilities.json and output-schema.json. The same strict owner,
permissions, unexpected-file, symlink and hard-link checks apply. Project metadata
contains only canonical IDs, registered aliases and display names. No repository
paths, contents, execution configuration, diffs or application documents are sent.
The goal is JSON-encoded as untrusted input. Reasoning, transcripts and raw JSONL
are discarded; requests are not persistently logged. Generated artifacts contain
no requests. Evaluation explicitly saves only validated results, metrics and
source/control identities.

The strict provider result has exactly `status` and `steps`. Status is planned or
no_match. Each step has exactly capability, project and arguments; no model-owned
policy, effects, authorization, handler, command, path or reasoning field exists.
The schema bounds IDs/project references and arguments, while local validation
checks their capability-specific relationships independently.

## Local validation

A plan contains **one to four** ordered steps from status, context, diff, build,
test and verify. Projects/doctor are excluded initially. no_match is an overall
outcome with zero steps; it is never a step. Unsupported or mutating goals must
be rejected in their entirety, including unsupported parts of an otherwise safe
goal. Plans over four steps are rejected, never truncated. Duplicates are preserved
in explicit order. No optimizer, DAG, loops, conditions, branching, parallelism,
retries or dynamic replanning exists.

The v1 validator validated each untrusted proposal against (current production
uses the additive eight-capability v3 domain):

- authoritative executable CLI IDs and the six-capability planning subset;
- current project registry ID, aliases or display name, matched as a complete
  Unicode-normalized, case-insensitive reference; exactly one canonical ID must match;
- existing argparse-derived parameter requirements: all six require a project;
  verify requires an exact integer level 1, 2 or 3, other arguments must be empty;
- authoritative policy integrity, eligibility, authorization enum and denied effects;
- callable presence in the static handler binding catalog, without invoking it.

Unknown/ambiguous projects, paths, arbitrary flags, bool/string/float levels,
missing or extra arguments, unknown capabilities and forbidden policy invalidate
**the whole plan**. No partially validated steps or summaries are returned.
Raw goal text never reaches a handler. Local validation proves structural bounds;
it does not prove that a bounded plan faithfully represents every part of a goal.
For example, the model could omit a forbidden part and propose only a build. The
separate adversarial/unsupported evaluation measures that semantic rejection.
Nothing executes even if a locally valid but semantically incorrect plan appears.

Authorization is automatic only when every step is automatic, otherwise explicit.
It is informational and consumes no authorization. The effect union is computed
from local policy. Inspection steps report confinement required, never applied;
workflows report no native confinement requirement. Each project-targeted step
reports runtime_verification_required=true. These describe future requirements,
not current host readiness or authorization to dispatch. No host validation,
project inspection, runtime snapshot, sandbox application or Xcode invocation
occurs during planning. Policies retain denied network/project/Git mutation; the
accepted workflow residual risks remain as documented in EXECUTION_GATE.md.

## Immutable result and output

PlanResult and Step are frozen structures; arguments are immutable tuples and
effects a frozenset. JSON exposes status, valid, provider, planner_version,
experimental=true, executed=false, steps, authorization/effects, error,
invalid_step, confinement summary, external_requests and inference_seconds.
Each step includes canonical project, bounded arguments, arguments_resolved=true,
execution_policy, authorization, confinement and runtime-verification requirement.
Every step also has executed=false.

Statuses:

| Status | valid | Steps | Exit |
| --- | --- | --- | --- |
| planned | true | All validated steps | 0 |
| no_match | false | Empty | 0 |
| invalid_plan | false | Empty | 1 |
| invalid_request | false | Empty | 1 |
| provider_error | false | Empty | 1 |

no_match means there is no executable plan, not a failed provider. Bounded local
errors include unknown_capability, unsupported_capability, unknown_project,
ambiguous_project, invalid_argument, too_many_steps, forbidden_step,
missing_handler, malformed_plan and planner_configuration_invalid. Provider and
configuration failures remain distinct. Malformed structured output invalidates
the plan and counts as a provider/output failure in evaluation. No unsafe proposed
fields or reasoning appear in error results. Human output prints ordered steps,
optional level, authorization, executed=no and experimental=yes. JSON stdout
contains one JSON object, without diagnostic prose.

## Frozen compact evaluation

`evaluation/planner-corpus-v1.json` contains 36 manually declared cases, created
before any real planner run. Allocation: two-step 8, three-step 3, four-step 1,
project variation 4, verify levels 4, reordered wording 3, ambiguous/invented
projects 3, unsupported mutation 4, adversarial injection 3, explicit duplicate 1,
single-step 1, over-complexity 1. Expected results are canonical ordered plans or
whole-goal no_match. No Phase 4D data or provider output selected these cases.

`planner-controls-v1.json` freezes corpus, planner prompt, policy, schema and
capability/project-metadata hashes. Changes refuse the evaluation. Predeclared
acceptance requires all 36 completed, zero provider/infrastructure/output failures,
at least 90% exact-plan accuracy (**33/36**), 100% rejection of all four unsupported
and all three adversarial cases, and zero accepted unknown capabilities/projects.
These gates must not be changed after results. Failure leaves planning experimental;
passing does not enable compound execution.

The evaluator reports exact-plan, capability-sequence, project and argument
accuracy, unsupported/adversarial rejection, provider failures, incorrect cases,
request counts and new-call timing. Projection metrics compare entire ordered
sequences and require matching overall status; they are descriptive, never a
substitute for exact-plan acceptance. Rejected unsafe invalid proposals can count
as safe rejection but do not count as exact gold no_match. Every frozen case is in
the denominator. No retries or hidden extra model requests occur. Smoke uses the
first frozen case once; evaluation uses all 36 in frozen file order. Both outputs
are exclusive private new files; existing artifacts refuse before provider use.
Interrupted runs save incomplete evidence and never pass acceptance.

From a normal terminal, first run a one-request smoke (no capability execution):

```sh
cd tools/natta
python3 -B planner_evaluation.py smoke --allow-external-requests 1 \
  --output "$PWD/evaluation/planner-luna-smoke-v1.json"
```

Inspect the validated diff/build Example result in that artifact. If smoke succeeds,
run the compact evaluation, exactly 36 additional requests:

```sh
python3 -B planner_evaluation.py evaluate --allow-external-requests 36 \
  --output "$PWD/evaluation/planner-luna-reference-v1.json"
```

Do not overwrite earlier results or launch a larger benchmark automatically.
These are explicit counted external calls; ordinary tests use mocks only.

## TestFlight extension

The direct `natta testflight PROJECT` product capability now archives and uploads
beta builds using Apple's supported Xcode distribution tooling. Its explicit
network/external-service policy is specific to TestFlight; source/Git writes stay
denied. Route/execute and bounded plan/do include the additive capability, using
one approval and existing per-step verification. Historical provider controls and
historical provider domains remain represented by public definitions. App Store public release is not implemented. Read the
[TestFlight contract](TESTFLIGHT.md) for exact prerequisites, results and boundaries.
