> Standalone v1: current contracts are preserved below. Phase-labelled sections
> record historical domains/rationale; private empirical artifacts are excluded.
> See [documentation map and evaluation provenance](../../../docs/EVALUATION.md).

> Current Phase 8B addition: optional `natta do` executes a frozen bounded plan.
> Explicit Phase 4/8A evaluation records retain their historical scope; current
> execution contracts below govern production behavior.
> `route`, `execute` and `plan` retain their existing boundaries; direct CLI remains
> primary. See [compound execution contract](COMPOUND_EXECUTION.md).

# Semantic routing contract

`natta route REQUEST` is the first production-facing semantic selector. GPT-6
Luna is the accepted Natta v1 routing provider. **Luna selects. Natta
validates. Luna does not execute.** `execute` and bounded `do` authorize and dispatch
validated deterministic handlers. Autonomous orchestration is not implemented. Explicit build/test/verify and inspection
commands still bypass semantic routing and perform their deterministic work.

The source implementation selected this provider through staged evaluation.
Private empirical outputs are not distributed and the sanitized public corpus has
not been re-benchmarked. Selection evidence never grants execution authority.
See [public evaluation provenance](../../../docs/EVALUATION.md).

## Production selection boundary

`natta.py` classifies before loading any project registry. After a matched choice,
it derives parameter requirements from the existing parser and loads the registry
only for capabilities requiring a project. `routing.py` returns
an immutable `RouteResult`; it imports no executable handlers, adapters, project
inspection or evaluation scripts. `router_codex.py` is the shared production
provider used by both this command and the Phase 4D evaluator. JSONL parsing,
provider argv construction and local compatibility probes have one implementation.
`config/routing.json` is the production metadata source: an unchanged byte copy of
the frozen benchmark descriptions. The evaluation snapshot remains immutable;
evaluation checks parity with production metadata. Descriptions register no new
handlers: the authoritative argparse registration table in `natta.py` is read
via AST without importing handlers and checked against the fixed routing universe.

The historical Phase 4D domain is projects, status, context, diff, doctor,
build, test, verify and no_match. Current production uses the additive commit and
TestFlight controls (`config/routing-commit-v2.json`, `config/routing-testflight-v1.json`) and eleven
candidates, including no_match. Codex and routing/execution/planning controls are
excluded. One explicit gpt-6-luna request uses concise descriptions.
Only the strict whole JSON object
`{"choice": "KNOWN_ID"}` is accepted; extra fields, duplicate keys, unknown IDs,
invalid output and selections outside the supplied universe fail closed. Natta
validates again before returning matched/no_match. Provider-supplied project names, levels, flags, paths and arbitrary command
arguments are never trusted. Local parameter resolution follows validation below.

`no_match` is a valid semantic outcome and exits zero. It normally covers
insufficient intent and unsupported actions such as commit-and-push, pushing,
writing tests, deleting Downloads or asking for weather. Local-only default-message
commit is now a separately evaluated production extension. Infrastructure failures are
provider_error with null capability and nonzero exit, never fabricated no_match.
Local workspace/definition/compatibility problems are routing_error; empty text
is invalid_request. Failure output uses bounded fixed codes, without raw provider
messages or private reasoning. No retry, Chat mode or Qwen/provider/model fallback
exists. See [the route result contract](RESULT_CONTRACT.md#route-result).

## Deterministic parameter resolution

**Luna selects the capability. Natta resolves and validates parameters. Nothing
executes.** `parameters.py` is a pure stdlib resolver: no provider, filesystem or
execution access. It receives validated registry projects and a parameter schema
derived from argparse. The production CLI applies its immutable result to the
immutable route result. Evaluation still calls only the shared capability provider
and never invokes this resolver. Phase 4D accuracy validates capability selection,
not parameter resolution; local resolution has separate deterministic tests.
There is exactly one Luna classification call per successful route, with unchanged
prompt, schema, candidate order, descriptions, model and provider behavior.

The authoritative source is the existing `~/.config/natta/projects.toml` (or root
`--registry PATH` override) loaded through the existing registry validator. No
second project catalog or new runtime state is created. Project IDs are registry
`alias` values, currently example and sample. Match the canonical alias, registered
aliases and display name after NFKC normalization, Unicode case-folding and
whitespace normalization. Require word boundaries around the entire escaped
identifier/phrase; app does not match happy, applet or app_suffix. Punctuation
can delimit identifiers. Multiple mentions/aliases of one project count once.
Zero matches leave project missing; multiple distinct project matches produce
ambiguous_project and null project. All mentions count; no negation, intent or
fuzzy project disambiguation is attempted. User text never becomes a project path.

Required parameters are introspected from actual subcommand argparse actions:

| Capability | Required project | Required additional parameter |
|---|---|---|
| projects, doctor | no | none |
| status, context, diff, build, test, commit, testflight | yes | none |
| verify | yes | level, currently integer 1/2/3 |

Unknown/unsupported required parameters or incompatible level domains fail with
parameter_configuration_invalid instead of inventing a schema. Optional execution
flags such as verbose/log are not extracted. Projectless capabilities resolve to
project=null, arguments={}, arguments_resolved=true without loading the registry.
They ignore incidental project mentions and level text.

For verify, support explicit case-insensitive `level 1`, `level 2`, `level 3`,
with whitespace and trailing sentence punctuation. Validate the entire value
token against the parser's choices: level 4, negative values, fractions, ranges,
decimals and malformed tokens fail as invalid_argument. No default, semantic
inference, word-number forms or L1/L2/L3 shorthand is supported. Repeated identical
explicit levels agree; differing repeated `level VALUE` forms produce
conflicting_level. Missing explicit level appears in missing_arguments. A known
project or valid level may remain as partial data when another requirement is
unresolved; incomplete results never execute.

Matched capability results retain status=matched and exit zero even with missing,
invalid or ambiguous parameters. Check arguments_resolved, missing_arguments and
resolution_error; zero exit alone does not mean an executable argument set exists.
no_match skips resolution and registry/schema loading, and remains unresolved:
false means no executable argument set exists, not a request for retries or execution.
Provider/configuration failures also skip resolution and return empty parameters.
An invalid registry or unsupported parameter schema returns routing_error with
null capability and parameter_configuration_invalid, nonzero exit, without leaking
raw configuration/request diagnostics.

## Execution-policy reporting

After local resolution, `policy.py` reports target effects and an authorization
requirement. Production routes carry execution_policy with eligible, authorization,
effects, capability_type and reason. The catalog is local immutable metadata,
validated against the authoritative routed CLI IDs. It never reads request text
or provider effect claims and never invokes handlers or providers. Read-only
inspection targets use automatic authorization; build/test/verify use explicit.
No_match, routing failures, unknown targets and incomplete parameters are ineligible.
Automatic and eligible=true describe policy eligibility; neither executes from route.

Verify level 1 has build/Git-check effects without running simulator tests; levels
2/3 compose build and test effects and include simulator use. Temporary and
potential persistent tool/runtime writes are distinguished from project/Git/
external mutation denied to these workflows; only commit has reviewed Git mutation
authority. Classifications are conservative upper bounds over current
adapters, not runtime availability checks or proof that trusted tooling is sandboxed.
See [current effects, authorization, validation and limitations](EXECUTION_POLICY.md).

The separate execute gate requires matched status, complete valid arguments/project,
authoritative capability, permitted effects and satisfied authorization before
invoking one bound handler. Route consumes no authorization and has no execution
flag. Terminal-only execute and whole-plan do confirmation are supported; JSON/non-TTY never prompt.
The Phase 4D benchmark still measures capability selection only; resolver and
policy are independently tested deterministic layers.

## Isolation, runtime and privacy

Every model decision uses a fresh non-interactive ephemeral Codex process,
explicit `--model gpt-6-luna`, read-only sandbox and approval=never. The existing
ignored user config/rules, strict config, host-skill discovery suppression,
unstable warning suppression, disabled execution/provider/tool features,
disabled web and no shell environment inheritance remain in place. Structured
schema and bounded choices are mandatory. Prohibited item types (including
item.type=error), unknown items and unknown event types invalidate the result.
Reasoning/plan content is never retained as a route. The schema/ephemeral protocol
is documented in [official Codex documentation](https://learn.chatgpt.com/docs/non-interactive-mode).

The only request content supplied is the natural-language text, fixed routing
policy, candidate IDs/descriptions and output schema. No application repository,
Git diff, documents, credentials, environment values or unrelated context is
collected for the prompt. Default routing retains no prompts, requests, raw JSONL,
reasoning or persistent logs. The request is delivered by stdin, not provider argv.

`~/Library/Application Support/NattaToolkit/router-codex/` is generated runtime state,
not source, and contains exactly AGENTS.md, capabilities.json and output-schema.json.
A missing directory is created; stale ordinary artifacts are regenerated from
source. Directory/file permissions are enforced as 0700/0600. Existing files and
the directory must be owned by the user. Symlinks (including parent components),
hardlinks, unexpected files and incomplete existing directories are rejected;
Natta never removes unrelated files to make the workspace acceptable. Unsafe
state returns a concise error before any model call. The provider checks exact
artifact snapshots after each decision and fails if they change. No symlinks back
to workspace source and no application/user data are copied into runtime state.

Doctor performs read-only definition/workspace checks and bounded local
`codex --version`, `codex exec --help`, and `codex features list` compatibility
probes. It never makes a Luna request or regenerates files. Missing/stale safe
workspace state is reported as regenerable by route; unsafe state is unavailable.
Semantic-router failures render UNAVAILABLE and do not break deterministic core
health; existing core checks retain their exit semantics. This is structural
readiness, not authentication, rate-limit, network or model-access verification.

## Deferred work and historical local evaluation

This explicit route command always exercises semantic AI. A future orchestrator
may first resolve deterministic intent and call Luna only when needed. There is
no heuristic/regex router here. Runtime project availability checks and broader typed arguments remain separate
concerns. Single-capability execution has its local authorization gate; Phase 8A
introduced separate experimental planning; Phase 8B adds bounded frozen-plan execution. A matched capability does not establish that a specific
project is buildable or authorize execution. Status/context overlap remains the frozen semantics. Local parsing does not
validate simulator availability or other execution prerequisites. Natta Toolkit is the intended eventual public project name;
paths and modules are unchanged.

Qwen/OpenJEV remain research/evaluation artifacts and are rejected as production
production routers. The following historical contracts describe those experiments,
not a fallback path for current production selection.

The approved source is lookski/openjev at
`67eedd02d8863dfe37fcd391c55ce2a7b6d82e37`; the model is Qwen/Qwen3-0.6B at
`c1899de289a04d12100db370d81485cdf75e47ca`, local PyTorch MPS/FP32. The wrapper
allows 2–26 described caller choices and no cloud fallback. Candidate probabilities
and distribution concentration are not calibrated correctness probabilities.
Representative held-out evaluation and calibration are prerequisites for future
threshold-based routing. Claude and remote inference remain deferred.

**Routing selects; workflows compose.** Explicit commands bypass semantic routing.
Keep deterministic operations deterministic. Future orchestration may try bounded deterministic matching first; any semantic
selector must resolve toward registered capabilities, not arbitrary raw commands.

Historical local-selector design goals (broader availability/confidence reporting
remains future work; current bounded selection contracts above take precedence):

1. Select only from registered, available capabilities with real execution bindings.
2. Validate capability identity, project, typed arguments and selection constraints.
3. Return structured selection metadata: selection state, capability identity,
   project/arguments when resolved, reason and confidence where meaningful.
   Ambiguous results should expose candidates; no-match/unavailable/unsupported
   results must remain explicit. These are proposed concepts, not current fields.
4. Reject unknown, disabled, malformed or out-of-scope selections before dispatch.
   Execution must independently enforce permissions/effects; a route is not approval.
5. Let the chosen workflow own composition, project policy and check coordination.

Routing must never become workflow implementation, project/build/business logic,
a shell executor, arbitrary command generation, a permission bypass or a hidden
multi-agent loop. Do not silently fall through to another model or automatically
cycle Codex ↔ Claude. Surface uncertainty and let the user explicitly choose a
provider/escalation where appropriate. Router unavailability must leave deterministic
commands usable.

Providers, workflows and routers should consume structured state/results rather
than human terminal prose. Keep selection metadata distinct from execution results;
see [RESULT_CONTRACT.md](RESULT_CONTRACT.md). The authoritative registration rule in
[TOOL_DESIGN.md](TOOL_DESIGN.md) prevents catalog and execution drift.

## Phase 4B evaluation only

`tools/natta-local-model/evaluate.py` measures described choices across a frozen
72-case corpus, two description sets, three fixed orders, and with/without a
caller-supplied bounded `no_match`. It loads one model and never dispatches.
`no_match` is a candidate, not magical abstention. Project extraction is excluded;
Codex/provider launches are outside this experiment. The original compilation
smoke exposed a confidently wrong selection; its request remains a regression case.
Concentration thresholds report empirical error/coverage, not authorization or
calibrated correctness. Automatic routing through local-model research remains disabled. The optional 1.7B capacity comparison code is retained; see Phase 4C below. See [results](../../../docs/EVALUATION.md).

## Phase 4C — selection mechanisms, evaluation only

OpenJEV remains an optional evaluated decision mechanism; no installation is shipped. Both Qwen3-0.6B +
OpenJEV and Qwen3-1.7B + OpenJEV are rejected for routing. Private machine results are excluded.

Phase 4C compares retained OpenJEV scoring with direct constrained structured
generation and native thinking followed by the same bounded structured final
choice. Public sanitized corpus/descriptions/orders and peer no_match are pinned by
public controls; original private empirical outputs are excluded.
Generation results remain evaluation-only data: no execution authority,
production routing through those mechanisms or capability dispatch exists. No additional
model is downloaded. Public B/C benchmarks have not been performed during extraction. See
[experiment and commands](../../../docs/EVALUATION.md) and
[comparison](../../../docs/EVALUATION.md).

## Separate execution gate

`natta route` permanently reports without executing. `natta execute [--json]
[--confirm] REQUEST` uses the same one-call capability selection and local resolver
and policy, then revalidates before one bound handler invocation. Authorization
comes only from policy and the dedicated invocation-local CLI flag, never request
wording or model output. Automatic permits execution only in execute; explicit
requires terminal approval or --confirm; forbidden cannot execute. See [EXECUTION_GATE.md](EXECUTION_GATE.md).

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

## Separate Phase 8A planner

`natta plan [--json] GOAL` uses planner_provider_v3.py (testflight-planner-v1), reusing the
separate planner workspace and additive frozen controls. The existing single-selection provider remains unchanged.
The planner proposes bounded step-local project/argument suggestions; planning.py
independently validates these against local registry/CLI/policy metadata.
Only status/context/diff/build/test/verify/commit/testflight may appear, at most four ordered steps.
No handler, snapshot or sandbox runs. One planner call replaces per-step model
classification; there is no recursive routing. Phase 4D validates single-capability
selection only. See [PLANNING.md](PLANNING.md) for the separate frozen evaluation
and guarantees.

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

## Supplied context versus process isolation

The router receives the request and bounded routing definitions, not project
contents or registry paths. The planner additionally receives project IDs, aliases
and display names. Owned sterile workspaces, disabled Codex action features,
read-only/approval-never settings and strict JSONL/output validation reduce the
provider surface. They are not a Natta-enforced container or OS read sandbox.
The Codex provider process inherits the host environment and process access;
`shell_environment_policy.inherit=none` configures provider child shells, not the
provider's own environment. Host files/credentials are not proven inaccessible.
No arbitrary model-supplied command is accepted by routing or deterministic dispatch.

## Semantic availability

All ten registered targets have bindings, parameter contracts and policy entries.
Policy eligibility reports authority, not runtime availability. Projects and doctor
remain selectable proposals but semantic execution returns confinement_unavailable
with capability_mode_unvalidated before any handler runs. Their direct commands
remain usable. They are intentionally excluded from the bounded planner domain;
no unconfined fallback is permitted.
