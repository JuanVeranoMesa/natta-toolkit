> Standalone v1: current contracts are preserved below. Phase-labelled sections
> record historical domains/rationale; private empirical artifacts are excluded.
> See [documentation map and evaluation provenance](../../../docs/EVALUATION.md).

> Current Phase 8B addition: optional `natta do` executes a frozen bounded plan.
> Explicit Phase 4/8A evaluation records retain their historical scope; current
> execution contracts below govern production behavior.
> `route`, `execute` and `plan` retain their existing boundaries; direct CLI remains
> primary. See [compound execution contract](COMPOUND_EXECUTION.md).

# Current structured execution contract

The separate Phase 4A local-decision wrapper returns data with selected_id,
candidate probabilities, concentration, model/source identity, MPS/FP32 backend
and measured loading/inference durations. This does not alter Check/Result or
introduce a universal CLI envelope. Candidate-normalized logits and concentration
are uncalibrated scores, not correctness probabilities. Malformed decisions fail
explicitly; no capability is executed. See
[the isolated runtime](../../../docs/EVALUATION.md) for completed Phase 4A MPS/offline acceptance and Phase 4B evaluation.

Phase 2's machine-facing boundary is Python data in `execution.py`, not a JSON
CLI envelope. Human output is rendered deterministically and remains first-class.
There is no universal JSON/schema envelope or persistent run history covering
inspection. Doctor has a health-specific JSON view; route has its own scoped `--json` result below. Do not scrape terminal prose to compose
workflows or future providers.

## Current objects

Both dataclasses are frozen. Fields below describe the actual implementation.

| Object | Field | Meaning/default |
| --- | --- | --- |
| Check | name | Required human check identity |
| Check | command | argv tuple, default empty; never shell source |
| Check | status | Predeclared state, default empty (execute command) |
| Check | reason | Explanation of predeclared state, default empty |
| Check | optional | Whether unavailable is non-fatal, default false |
| Result | check | Original Check object |
| Result | status | passed, failed, unavailable or skipped |
| Result | exit_code | Required integer; predeclared states use 0 |
| Result | duration | Required elapsed seconds; predeclared states use 0 |
| Result | detail | Diagnostic text, default empty; predeclared reason is copied here |
| Result | detail_lines | Deterministic bounded report lines, default empty tuple |
| Result | failing_tests | Distinct structured failing-test identifiers, default empty tuple |

Adapters return ordered lists of Check objects. `execute()` returns ordered lists
of Result objects. Executed command success yields passed; nonzero exit yields
failed. OSError yields failed with exit 127 and diagnostic detail. Predeclared
unavailable/skipped checks are recorded without command execution. These states
must never be interpreted as tests that passed. Current dataclasses do not enforce
an enum, so callers must preserve the supported status vocabulary.

## Workflow aggregation and exit codes

Independent checks continue after ordinary failures, without automatic repair or
retry. Integrity failures stop the workflow through NattaError, without restoration.
Snapshots before discovery and after execution compare contents and Git state.

`render(results)` prints checks and derives the summary and CLI exit code:

- First failed result's exit code wins; negative process codes become 128 + signal.
- Otherwise required unavailable checks yield summary unavailable and exit 1.
- Otherwise any skipped/optional unavailable checks yield passed with omissions,
  exit 0. Optional does not excuse an executed failed check.
- Otherwise summary passed, exit 0.

The aggregate is computed during rendering, not stored as a separate result object.
Exit zero alone is not evidence that all checks ran. Level 3's optional device
unavailability and skipped manual checks do not certify physical/release readiness.
Argument errors exit 2. Registry/discovery/integrity errors caught by the CLI exit 1
and remain stderr diagnostics in human mode. Commands supporting --json instead
return their existing result envelopes for expected pre-dispatch failures. Inspection
retains its human renderer and 0/1 outcome; doctor also supports structured health.

## Command information and limits

The original argv is available as `result.check.command`; duration and process exit
code are available directly. Command output is captured in the current external execution directory, not
stored wholesale in Result or proxied to the normal terminal. `--verbose` streams
raw output but does not retain it. `--log` retains per-check/discovery logs and
available result bundles under `~/Library/Logs/NattaToolkit/<run-id>/` and prints that
path. The flags are independent. Without `--log`, no diagnostics persist after
normal completion. Temporary build/test state is removed on success and ordinary
failure, including discovery/integrity failures; ownership checks limit cleanup
to the current Natta-created directory. Forced termination may rely on OS cleanup.
Project, operation/level and execution-directory/retained paths are CLI context,
not Result fields. A Check name is not a registered capability ID.
`detail_lines` carries reliable `xcresulttool` summary JSON counts, at most five
failing identifiers/messages, and at most three explicit Xcode error lines.
`failing_tests` preserves the distinct identifiers for composition. Extracted
summary outcomes never override process status or exit codes. Missing, unsupported
or malformed structured data is omitted. The renderer bounds/sanitizes details,
prints named checks and duration, and suggests `--log`/`--verbose` for failures.
An application test failure is a failed application check, not a harness error.

No automatic content-hash provenance, warnings/errors arrays, metrics dictionary
or effect record is attached to Result. Integrity snapshots are internal guards,
not provenance certificates.

The current supported boundary is ordered Check/Result data with distinct states,
explicit argv and durations. Future consumers should use these objects directly;
human wording is not a machine protocol. A future versioned envelope may converge
around capability identity, status, project, operation, checks, warnings, errors,
metrics, provenance and effects when real consumers need it. Define compatibility
then; do not claim these fields exist now or force speculative fields into Phase 2.

## Separate evaluation result

Phase 4B's opt-in retained JSON has schema_version=1, evaluation_only=true,
total_cases/total_decisions, runtime identity, experiment inputs/orders/prompt,
input hashes, timings, summaries, predictions, exact legacy smoke and verdict.
Per-prediction candidate probabilities are normalized token scores; concentration
is distribution concentration. Correctness comes only from explicit corpus gold
labels. These results never enter execution Check/Result or grant authority.
Default evaluation retains nothing; `--output` exclusively creates one file.

## Phase 4C generated-selection results

New opt-in evaluation outputs use schema_version=2 and evaluation_only=true.
Only a validated known-ID JSON choice becomes selected_id data. Malformed/native
reasoning-boundary failures or unknown choices record failed/null selection and
remain in accuracy denominators. No generated reasoning is stored or authoritative.
Native thinking/final token counts and mean/median inference durations are recorded;
concentration/confidence is not applicable for structured-generation mode.
These results never enter Check/Result, dispatch capabilities or grant authority.
Both OpenJEV model baselines remain immutable, installed evaluation evidence.
See [Phase 4C](../../../docs/EVALUATION.md).

## Route result

`routing.RouteResult` is an immutable, selection-only object separate from
execution Check/Result. `natta route [--json] REQUEST` renders it in human form by
default or as one stable JSON object with these fields:

| Field | Values |
|---|---|
| status | matched, no_match, provider_error, routing_error, invalid_request |
| capability | A validated supplied capability; no_match sentinel; null on failure |
| provider | Always gpt-6-luna |
| executed | Always false; fixed field, not provider supplied |
| project | Registry canonical alias, or null; locally resolved |
| arguments | Object containing validated bounded parameters; currently only verify level |
| arguments_resolved | True only when a matched route has all required local parameters |
| missing_arguments | Array of unmet required parameters (project and/or level) |
| resolution_error | null, ambiguous_project, invalid_argument or conflicting_level |
| error | null on success; a fixed bounded code on failure |
| execution_policy | Immutable local PolicyResult exported as the object below |

```json
{"status":"matched","capability":"verify","project":"sample","arguments":{"level":2},"arguments_resolved":true,"missing_arguments":[],"resolution_error":null,"provider":"gpt-6-luna","executed":false,"error":null}
```

Matched and no_match exit zero. provider_error/routing_error/invalid_request exit
one; parser usage errors exit two. Provider errors include provider_failure,
provider_timeout, provider_unavailable, malformed_output, unknown_choice and
boundary_violation. Local failures use workspace_or_definitions_invalid or
unsupported_cli or parameter_configuration_invalid; empty text uses empty_request. Missing authentication, rate
limits and nonzero provider exit are provider_failure without leaking stderr.
No reasoning, confidence, raw prompts, requests, provider JSONL or provider
arguments enter this result. It grants no execution authority and `natta route` never
passes it to a dispatcher. The execute command constructs its own internal route. Project and bounded verify level are resolved deterministically after capability
validation, never supplied by Luna. Internally arguments and missing values use
immutable tuples; JSON exports a fresh object/array. Matched incomplete routes
still exit zero: consumers must inspect arguments_resolved and resolution_error.
Missing level yields missing_arguments=["level"], arguments={}, error=null.
Invalid/conflicting explicit level yields resolution_error with resolved=false;
no default is substituted. Missing project includes "project"; ambiguous projects
also leave that required value unmet and set ambiguous_project. A valid partial
level may remain when the project is unresolved. no_match/provider/configuration
errors always have project=null, arguments={}, arguments_resolved=false and
executed=false. no_match false means no executable argument set exists.

Even complete parameters alone grant no execution authority. The separate execute
gate needs matched status, complete parameters, permitted capability, valid project
and arguments, policy eligibility and satisfied authorization. Broader argument
forms remain deferred. Phase 8A adds separate bounded planning only; Phase 4D
measured capability selection only.

### Execution-policy object

```json
{"eligible":true,"authorization":"explicit","effects":["git_read","local_process","local_read","persistent_runtime_write","project_read","temporary_write"],"capability_type":"workflow","reason":null}
```

This example is a resolved build target. PolicyResult contains eligible (boolean),
authorization (automatic/explicit/forbidden), sorted Effect enum strings,
capability_type (atomic/workflow/interface or null without a known policy target),
and a bounded reason or null. It describes the future target, not routing's own
provider/network/runtime effects. Executed stays false regardless of eligibility
or authorization class. Explicit authorization is a requirement, not satisfied
permission; automatic does not make route run a handler.

No_match has reason=no_match; provider/configuration failures have routing_error;
unknown targets have unknown_capability; incomplete resolution has
unresolved_arguments. Known incomplete targets can report metadata even while
ineligible. Invalid local parameters/project, missing or malformed catalog, and
forbidden entries fail closed. Catalog integrity errors become routing_error with
error=policy_configuration_invalid and nonzero CLI exit. Existing matched-but-
incomplete planning exit-zero semantics remain; eligibility is not implied by
exit status. Policy evaluation defensively revalidates local IDs/argument shape,
and cannot consume provider/user-supplied effects. See [policy details](EXECUTION_POLICY.md).

## Separate semantic ExecutionResult

`natta execute [--json] [--confirm] REQUEST` returns an immutable ExecutionResult,
not a mutated RouteResult. See [EXECUTION_GATE.md](EXECUTION_GATE.md) for the complete
fields/status/exit contract. executed aliases execution_started: handler failures
remain started=true, succeeded=false. Before invocation succeeded is null and result
is null. JSON contains only bounded presentation output and existing structured
workflow checks. Route keeps executed=false permanently. Explicit authorization
uses terminal approval or a dedicated CLI flag for one invocation, never natural-language data.

Runtime effect verification now wraps semantic execute with protected project/Git
before/after evidence, compared against authoritative policy. Handler success is
separate from overall success; violations/unavailable verification fail closed.
Route and direct commands retain their boundaries. No rollback or proof of network/
external-service absence is provided. See [observable scope and contract](RUNTIME_EFFECTS.md).

Semantic status/context/diff use a current host-validated macOS inspection
sandbox and mandatory runtime verification. Build/test/verify require explicit
authorization (terminal approval or --confirm) and mandatory protected-state snapshots, but no native confinement.
Xcode sandbox-exec research is deliberately deferred, not a production dependency.
Policy-denied effects are distinct from actively enforced effects: workflow
network, external services and unrelated filesystem writes are residual risks.
Route never executes; direct commands remain unchanged; bounded `natta do` composes the same validated dispatcher.
See [current confinement boundary](MACOS_CONFINEMENT.md).

ExecutionResult.confinement now includes policy_denied_effects separately from
enforced_effects. Authorized local workflows report required=false, applied=false,
mode=null, backend=null and reason=not_required_for_workflow. Their observable
project/index/HEAD effects remain observation_only_effects, while network/Git-push/
external-service effects are unenforced_effects. This is not sandbox proof; see
MACOS_CONFINEMENT.md for the protected scope and residual risks.

## Separate Phase 8A PlanResult

`natta plan [--json] GOAL` returns a frozen PlanResult with ordered frozen Step
objects, never ExecutionResult. Status is planned/no_match/invalid_plan/
invalid_request/provider_error; valid=true only for a wholly validated plan.
All result/step executed fields are fixed false; experimental is fixed true.
Arguments are immutable tuples, effects a frozenset, policy immutable.

The JSON envelope includes provider/version, steps, locally computed authorization
and effect union, confinement summary (applied=false), bounded error/invalid_step
and request count/timing. Steps include canonical project, bounded arguments,
arguments_resolved=true, local execution_policy/authorization, confinement
requirement and runtime_verification_required. Invalid plans return no partial
steps or summaries. no_match has empty steps/valid=false/exit0; other failure
statuses exit1. Planning neither consumes authorization nor tests host execution
readiness. It takes no snapshots and invokes no handler. Complete fields, errors,
privacy and evaluation semantics are in [PLANNING.md](PLANNING.md).

## First reviewed Git mutation — commit

Commit is atomic, project-required and explicit, using one local static handler.
Direct syntax is `natta commit PROJECT [--message MESSAGE] [--json]`; semantic
execute requires explicit authorization and uses only the deterministic default message.
Route never executes. Commit has no native confinement requirement and uses
mandatory commit-aware runtime evidence; only index/HEAD mutation is permitted.
Working-tree/branch/repository changes fail, with no rollback. ExecutionResult
result.commit carries the immutable CommitResult, including truthful mutation
state after failure. Planner v2 may report commit intents, never execute them.
Historical provider domains remain separate; public definitions have sanitized
project names/control hashes and private results are excluded. See [COMMIT.md](COMMIT.md).

Interactive semantic authorization is terminal-only and continues the same frozen
resolved action without a second Luna call. --json/non-TTY never prompt; --confirm
preauthorizes one invocation. Direct commands stay noninteractive. See
[authorization contract](EXECUTION_GATE.md#interactive-authorization).

## TestFlight extension

The direct `natta testflight PROJECT` product capability now archives and uploads
beta builds using Apple's supported Xcode distribution tooling. Its explicit
network/external-service policy is specific to TestFlight; source/Git writes stay
denied. Route/execute and bounded plan/do include the additive capability, using
one approval and existing per-step verification. Historical provider controls and
historical provider domains remain represented by public definitions. App Store public release is not implemented. Read the
[TestFlight contract](TESTFLIGHT.md) for exact prerequisites, results and boundaries.

## CLI JSON failures and doctor health

For commands accepting --json, expected NattaError/OSError failures before normal
result construction use that command's existing result shape and nonzero exit:
plan/do report invalid_plan with planner_configuration_invalid; direct commit and
TestFlight report failed/prerequisites_failed with cli_configuration_invalid.
Route/execute retain their routing/execution failure envelopes. Raw configuration
or provider diagnostics are not copied into JSON. Unexpected programmer exceptions
are not converted by this CLI boundary; interruptions may terminate without a result.
Malformed CLI arguments requested with --json produce status=invalid_request,
error=invalid_arguments, executed=false and exit 2. Human argparse diagnostics remain.

Doctor --json reports status=passed/failed, core_ready, error and Check-shaped
checks with name/status/optional. Mandatory failures determine exit 1; otherwise
exit 0. Missing Codex, semantic readiness, local models, native confinement and
TestFlight prerequisites remain visible optional unavailable checks, not passed
checks and not proof those capabilities work. Human output labels mandatory FAIL,
optional UNAVAILABLE and the core readiness summary. Invalid registry/configuration
produces failed/core_ready=false/checks=[]/cli_configuration_invalid and exit 1.
