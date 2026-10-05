> Standalone v1: current contracts are preserved below. Phase-labelled sections
> record historical domains/rationale; private empirical artifacts are excluded.
> See [documentation map and evaluation provenance](../../../docs/EVALUATION.md).

# Declared effects and execution policy

The current boundary is Luna capability selection → local capability validation →
deterministic parameter resolution → deterministic effects/authorization policy →
structured planning result. **Route never executes.** The separate `natta execute` gate now invokes one existing handler only after
revalidation and invocation-local authorization; see [EXECUTION_GATE.md](EXECUTION_GATE.md).
Interactive terminal confirmation and bounded `natta do` are implemented; JSON/non-TTY never prompt.
`natta route` has no execution flag. Neither eligible=true nor automatic causes
handler invocation. Direct explicit CLI commands retain their existing behavior.

## Source and separate axes

`policy.py` contains one immutable capability-policy catalog. It annotates the
existing authoritative CLI registrations; it is not a second executable registry.
Doctor and production route validate exact coverage of the routed executable IDs:
projects, status, context, diff, doctor, build, test, verify, commit, testflight. Codex and route are
outside this routing universe; no_match is a sentinel, not an executable capability.
No handler is imported, planned or invoked by policy evaluation.

Three independent fields describe a target:

- capability_type: atomic, workflow or interface. This is architectural scope,
  not risk. Doctor/build/test/verify are workflows; the inspection operations and commit are atomic. Codex remains an interface outside routing targets.
- effects: typed Effect enum values in an immutable set, with sorted JSON output.
- authorization: automatic, explicit or forbidden. This is an execution requirement,
  not proof of authorization being granted or satisfied.

Automatic permits execution without an additional confirmation only within
the separate execute gate's validated policy. Explicit requires invocation-local
--confirm or affirmative terminal approval. Forbidden means no current authorization path exists. Policy evaluation only reports these categories; it does not prompt or consume
permission. The separate execution gate consumes the dedicated CLI signal.

## Current target policies

Effect abbreviations below expand to actual stable JSON enum values:

- R = local_read
- P = project_read
- G = git_read
- L = local_process
- T = temporary_write
- W = persistent_runtime_write
- S = simulator

| Capability | Type | Authorization | Effects |
|---|---|---|---|
| projects | atomic | automatic | R |
| status | atomic | automatic | R, P, G, L |
| context | atomic | automatic | R, P, G, L |
| diff | atomic | automatic | R, P, G, L |
| doctor | workflow | automatic | R, P, G, L, W |
| testflight | workflow | explicit | R, P, G, L, T, W, network, external_service_mutation |
| commit | atomic | explicit | R, P, G, L, git_index_write, git_commit |
| build | workflow | explicit | R, P, G, L, T, W |
| test | workflow | explicit | R, P, G, L, T, W, S |
| verify level 1 | workflow | explicit | R, P, G, L, T, W |
| verify level 2 | workflow | explicit | R, P, G, L, T, W, S |
| verify level 3 | workflow | explicit | R, P, G, L, T, W, S |

These are conservative capability-level upper bounds across existing adapters,
not project-specific plans or availability guarantees. GenericGit verification
may only run Git checks; its build/test and stronger verification can report
unavailable. Policy eligibility does not establish that Xcode, simulator, project
files, scheme, dependencies or workflows are available. No discovery is run here.

Classification follows the implementation:

- Projects renders registry fields without inspecting repository contents.
- Status/context/diff run read-only Git queries and read project metadata; Git
  optional locks, external diff and text conversion are disabled where applicable.
- Doctor reads local/project state, diagnoses adapters and runs local CLI/Xcode
  metadata probes. Such tools may initialize local cache/runtime files, so W is
  conservatively included even though repository inspection is read-only. No
  Luna decision is made by doctor.
- Workflows allocate an external ExecutionDirectory and discovery/process logs,
  DerivedData, packages and result bundles. Default state is temporary and cleaned
  after ordinary completion/failure; interrupted cleanup can leave temporary
  state. Xcode/cache initialization and simulator/test data may persist outside
  repositories, represented by W. Direct --log additionally retains diagnostics;
  routing does not extract or authorize that flag.
- IOSXcode verify level 1 composes a simulator-target build with Git checks, without
  booting/running simulator tests. Levels 2/3 add unit/UI simulator tests. Level 3
  also runs read-only syntax checks; physical-device validation is unavailable and
  manual release checks are skipped. Those omissions do not confer effects or
  authorization to install, release, deploy or use physical devices.

Build/test effect sets are composed into verify level profiles in the catalog.
Those profiles must cover the actual parser's allowed level domain. No adapter
plan or build/test discovery is invoked to classify a route. This keeps selection
and policy separate from workflow implementation.

## Limits of declared effects

Project_write and git_push remain forbidden. Only the reviewed explicit commit
entry may authorize git_index_write/git_commit; only the reviewed explicit
TestFlight entry may authorize network/external_service_mutation. Catalog validation
rejects these effects on other targets, and forbidden entries are never eligible.
The network restriction describes the proposed executable target, not the
separate Luna classification request's network usage. Local policy evaluation
makes zero provider requests or processes.

Read-only repositories do not imply zero writes elsewhere. Existing integrity
snapshots detect repository/source/index changes and stop without automatic
restoration. Trusted Xcode build phases, tests and tooling are not filesystem or
network sandboxed by that check: they can misbehave, including writing project/Git
state or using the network. Declared metadata is not proof or enforcement of tool
behavior. The current gate enforces validated policy and authorization before
dispatch and checks observable postconditions; it does not universally sandbox
trusted tools or prove absence of unobserved effects.

## Result and failure semantics

Each production route includes execution_policy with eligible, authorization,
effects, capability_type and reason. Effects come only from local catalog metadata
and validated level arguments. User text and provider-supplied effect/authorization
claims have no input path. Typed metadata, exact coverage and enums are validated.
The evaluator defensively checks parameter completeness, registered project ID,
argument shape and bounded integer level. Eligibility requires matched status,
resolved valid arguments, a registered capability and non-forbidden policy.
It still does not satisfy explicit authorization or execution prerequisites.

- no_match: eligible=false, authorization=forbidden, empty effects, reason=no_match.
- Provider/configuration/invalid-request failures: ineligible, reason=routing_error.
- Unknown local policy target: ineligible, reason=unknown_capability.
- Missing, invalid, ambiguous or conflicting resolution: ineligible,
  reason=unresolved_arguments. Known metadata can still report effects/authorization.
- Invalid local project/argument data: invalid_project or invalid_parameters.
- Invalid/missing/unknown metadata: policy_configuration_invalid; production route
  returns a structured routing_error and nonzero exit when catalog integrity fails.
- Forbidden policy: ineligible, authorization=forbidden, reason=forbidden_policy.

Matched-but-ineligible planning results retain the existing matched exit-zero
convention. Consumers must inspect eligible and arguments_resolved. JSON has the
full policy; human output adds a concise effects/authorization/eligibility summary.
No request or policy logging/runtime state is introduced. Doctor reports policy
integrity as a deterministic core check: missing entries, extra unknown capabilities
or invalid effect/type/authorization enums cause FAIL and nonzero doctor exit.

## Future additions

Commit is the reviewed local-only exception above. Edit/push/deploy are not
registered or implemented. Adding any future
capability requires an actual authoritative CLI/handler definition, explicit typed
effect/type/authorization metadata, validated parameter requirements and a
separately reviewed change to currently denied effects before eligibility is
possible. Adding an enum or model-selected string alone grants no permission.
The execution gate independently checks capability, parameters/project,
effects, authorization and execution policy; it cannot treat eligible=true as
blanket authorization. No routing/evaluation benchmark validates that executor.

Runtime effect verification now wraps semantic execute with protected project/Git
before/after evidence, compared against authoritative policy. Handler success is
separate from overall success; violations/unavailable verification fail closed.
Route and direct commands retain their boundaries. No rollback or proof of network/
external-service absence is provided. See [observable scope and contract](RUNTIME_EFFECTS.md).

Semantic status/context/diff use a current host-validated macOS inspection
sandbox and mandatory runtime verification. Build/test/verify require explicit
terminal approval or --confirm and mandatory protected-state snapshots, but no native confinement.
Xcode sandbox-exec research is deliberately deferred, not a production dependency.
Policy-denied effects are distinct from actively enforced effects: workflow
network, external services and unrelated filesystem writes are residual risks.
Route never executes; direct commands remain unchanged; bounded `natta do` composes the same validated dispatcher.
See [current confinement boundary](MACOS_CONFINEMENT.md).

## Reviewed local commit exception

Commit is atomic and explicit. It changes index and HEAD, preserves physical
working-tree contents and branch/repository identity, and invokes no remote Git
operation. Its optional message exists only on the direct CLI. Native inspection
confinement does not apply; runtime verification remains mandatory. See
[COMMIT.md](COMMIT.md) for hooks/filter restrictions, postconditions and residual
risks. Authorization never waives a forbidden effect.

## TestFlight extension

The direct `natta testflight PROJECT` product capability now archives and uploads
beta builds using Apple's supported Xcode distribution tooling. Its explicit
network/external-service policy is specific to TestFlight; source/Git writes stay
denied. Route/execute and bounded plan/do include the additive capability, using
one approval and existing per-step verification. Historical provider controls and
historical provider domains remain represented by public definitions. App Store public release is not implemented. Read the
[TestFlight contract](TESTFLIGHT.md) for exact prerequisites, results and boundaries.
