> Standalone v1: current contracts are preserved below. Phase-labelled sections
> record historical domains/rationale; private empirical artifacts are excluded.
> See [documentation map and evaluation provenance](../../../docs/EVALUATION.md).

> Current Phase 8B addition: optional `natta do` executes a frozen bounded plan.
> Explicit Phase 4/8A evaluation records retain their historical scope; current
> execution contracts below govern production behavior.
> `route`, `execute` and `plan` retain their existing boundaries; direct CLI remains
> primary. See [compound execution contract](COMPOUND_EXECUTION.md).

# Capability design

The roadmap in `../../../docs/ROADMAP.md`
remains the architectural plan. This document is the implementation design contract.
The source implementation completed its deterministic workflow acceptance. The first Phase 3 interface is the
Codex launcher. Bounded semantic selection is now provided by `natta route`;
other provider interfaces, chat and autonomous orchestration remain future work.

## Three separate axes

| Axis | Meaning | Examples |
| --- | --- | --- |
| Capability type | Architectural scope | atomic, workflow, interface |
| Domain/category | Subject area | project, git, xcode, system, ai, release |
| Execution type | How work is performed | deterministic or AI |

**Atomic:** a focused capability with one clearly explainable outcome. Multiple
implementation steps are allowed. Atomic does not mean one function, tiny file,
one shell command, or absence of complexity. Current project listing and status
inspection are focused atomic outcomes, even though status gathers several facts.

**Workflow:** coordinates focused capabilities/checks/steps toward one exposed
outcome. Composition belongs in workflows. Current build, test, and verify are
workflows: they coordinate discovery, project policy, execution and integrity
checks. Doctor coordinates diagnostic checks toward an environment-health outcome.

**Interface:** exposes or launches another tool, environment, system, or interaction
surface. `natta codex [PROJECT]` is the current interface capability: it resolves
the toolkit checkout root from the harness layout or a registered project via
existing alias lookup, validates the directory and executable, and hands the
terminal to interactive Codex with process replacement. It preserves interactivity
and leaves instruction discovery to Codex. Build/test result capture and
`--verbose`/`--log` do not apply to this interface. Claude, chat and project shell
entry points remain future work. This interface adds no workflow or orchestrator; semantic routing is a separate
selection-only capability.

`ios-xcode` and `generic-git` are adapters: reusable implementation mechanisms,
not capability types. The project registry selects policy; `adapters.py` validates
it and plans checks; `execution.py` executes and renders structured results.
Preserve this small Phase 2 architecture. These classifications are design language,
not a requirement to introduce a general capability framework now.

## Scope and composition

One clearly explainable exposed outcome per capability. Use focused atomic APIs
plus explicit workflow composition instead of unrelated modes in a generic command.
Semantic scope matters, not line count. Routing selects; workflows compose.
If deterministic code reliably performs the task, use it. AI belongs in reasoning,
interpretation, ambiguity, planning, review and semantic work, not replacement of
Git, project discovery, build or test operations. Machine composition consumes
structured state/results, never scraped terminal prose. Human rendering stays
first-class. Raw provider/tool output is diagnostic data, not the normal
interface. Build/test/verify capture it in an owned external temporary execution
directory and render concise structured checks deterministically. `--verbose`
controls terminal output only; `--log` controls persistence only, explicitly
retaining diagnostics at `~/Library/Logs/NattaToolkit/<run-id>/`. Default runs retain no
execution logs; temporary build/test state is removed after normal completion,
including ordinary failures. This is current execution UX, not run history or a
universal logging framework. See [RESULT_CONTRACT.md](RESULT_CONTRACT.md).

Prefer explicit failed, unavailable, skipped, ambiguous, unsupported or no-match
states over guesses. Check/Result use passed/failed/unavailable/skipped. Route, planning, execution
and product results have separate bounded status vocabularies, including unresolved,
no_match, authorization_required and confinement_unavailable.

## Registration direction

Phase 4A's isolated sibling runtime exposes one focused bounded-decision outcome:
caller-defined question/choices to structured scores and selected ID. The approved
OpenJEV/Qwen identities and setup status are in
[the runtime documentation](../../../docs/EVALUATION.md). It selects no
registered capability and dispatches nothing. Explicit bootstrap owns installation;
core doctor only inspects availability. Core workflows/provider interfaces do not
depend on or invoke the model. Local inference was evaluated in the source environment; public users must explicitly set it up; hosted backends, Claude integration and remote nodes are deferred.

Today `~/.config/natta/projects.toml` registers projects and workflow policy, `ADAPTERS`
binds supported implementations, and argparse dispatches explicit commands. There
is a bounded capability-policy catalog plus explicit static handler bindings.
Authoritative CLI IDs, routing definitions, parameter contracts, bindings and policy
must remain synchronized; route, doctor, planning and execution validate their
integrity and fail closed on drift. This is not generic plugin discovery.
Selection validation is not execution authorization. See [ROUTING.md](ROUTING.md).

## Safety and effects

Applications remain independent repositories. Inspection is read-only. Build/test
workflows use external temporary DerivedData, package and result paths; integrity
checks detect repository mutation and stop without restoring anything. Trusted
project build phases/tests are not sandboxed by this detection.

Current `policy.py` metadata distinguishes local/project/Git reads, local processes,
temporary and persistent runtime writes, simulator use and denied mutation/network
effects. Capability type and authorization are separate enums. A single boolean
cannot describe these effects. Metadata informs enforced execution gating; a
read-only repository label does not prove zero writes elsewhere. Policy evaluation
itself only reports; execute/do enforce authorization and runtime verification,
with native inspection confinement and documented unconfined workflow risks. See
[effect classifications and limits](EXECUTION_POLICY.md).

## Anti-patterns

- Composition or project/build/business logic hidden in routing.
- Arbitrary shell generation, shell evaluation of registry text, permission bypass.
- Automatic model fallback or hidden Codex/Claude multi-agent loops.
- Prose scraping, fabricated success, treating omissions as completed checks.
- Catalog/dispatch drift, duplicate manually synchronized capability definitions.
- Silent dependency installation/repair or application changes to fix harness runs.
- Copying unrelated platform domains or result envelopes wholesale, using a
  source-write boolean for distinct effects, or leaving approval unenforced.

## Phase 4B evaluation boundary

The sibling evaluation harness owns one bounded outcome: labeled requests and
fixed candidates/orders to empirical metrics and an exploratory verdict. This
is not a production capability catalog, dispatcher or workflow. Status/context
currently share behavior, so corpus labels explicitly acknowledge overlap.
Build/test/verify and `natta codex` remain unchanged. Automatic routing through local-model research is disabled.
See [baseline](../../../docs/EVALUATION.md).

## Phase 4C — selection mechanisms, evaluation only

OpenJEV remains an optional evaluated decision mechanism; no installation is shipped. Both Qwen3-0.6B +
OpenJEV and Qwen3-1.7B + OpenJEV are rejected for routing. Private machine results are not distributed.

Phase 4C compares retained OpenJEV scoring with direct constrained structured
generation and native thinking followed by the same bounded structured final
choice. Public sanitized corpus/descriptions/orders and peer no_match are pinned by
public controls; original private empirical outputs are excluded.
Generation results remain evaluation-only data: no execution authority,
production routing through those mechanisms or capability dispatch exists. No additional
model is downloaded. Public B/C benchmarks have not been performed during extraction. See
[experiment and commands](../../../docs/EVALUATION.md) and
[comparison](../../../docs/EVALUATION.md).

## Production semantic selection

`natta route` is an atomic AI capability with one outcome: return a bounded
proposed capability as structured data. Luna selects; Natta validates; Luna does
not execute. The accepted provider is gpt-6-luna, selected through source evaluation. Public definitions retain the bounded
selection contract; private empirical outputs are excluded.

Shared `router_codex.py` owns bounded provider invocation and validation, `routing.py` owns the
selection result, and argparse registrations remain authoritative for executable
IDs. Production metadata in `config/routing.json` matches the frozen descriptions;
the evaluator verifies that immutable snapshot without production imports of
evaluation scripts. This is not a general capability/effect framework. The router performs no project inspection or execution. Local resolution and
validated execute/do are separate layers; chat, autonomous orchestration and
heuristic capability routing are not implemented. See [ROUTING.md](ROUTING.md).

### Bounded deterministic parameter layer

After validated capability selection, production route derives required parameters
from argparse and resolves registry aliases/display names plus explicit verify
levels using pure stdlib `parameters.py`. Luna still makes one capability choice;
Natta resolves and validates locally; nothing executes. The evaluator remains
capability-only. This layer adds no second model request, project catalog, broad
NLP, runtime state or general permission framework. Structured completeness is
planning data, not execution authorization. Missing/ambiguous/invalid parameters
remain explicit. See [matching and syntax](ROUTING.md#deterministic-parameter-resolution).

### Execution-policy reporting layer

After parameter resolution, production route attaches a deterministic immutable
PolicyResult from `policy.py`. Its catalog annotates existing executable routing
IDs and is checked against authoritative registrations by route and doctor.
Read-only targets are automatic; build/test/verify require explicit authorization
in the separate execute gate. Effects include persistent tool/runtime state as well as
temporary writes, with simulator tests only in test and verify levels 2/3.
Capability type, effects and authorization remain separate axes. No handler or
provider call is made by the policy; eligible=true never executes a route.
Invocation-local authorization is consumed by execute or bounded do. Terminal-only approval is supported; do authorizes the complete frozen plan before any step.
See [the complete current table](EXECUTION_POLICY.md#current-target-policies).

## First bounded semantic dispatcher

The separate execute command adds a local authorization/revalidation boundary and
static handler bindings shared with direct CLI commands. It does not change the
reporting-only route command, capability type/effects axes or workflow composition.
Policy alone does not execute; the gate requires complete parameters, fresh registry
validation, exact authoritative policy, valid bindings and satisfied authorization.
Single-capability terminal approval continues the same frozen resolved action; bounded do composes this dispatcher after whole-plan approval.
See [EXECUTION_GATE.md](EXECUTION_GATE.md) for current contracts and limits.

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

## Phase 8A bounded orchestration planning

The separate `plan` surface proposes one to four linear capability intents in one
Luna call. Local Natta validation binds them to registry projects, CLI parameter
schemas, policy and static handler coverage. It computes authorization/effect
summaries but dispatches nothing. Model suggestions grant no authority.
`route` selects/reports one capability, `execute` authorizes one existing handler,
`plan` reports multiple validated intents, and `do` executes a frozen bounded plan through the same gate.
No branching, loops, parallelism, retries or replanning exists. The separate
planner corpus and predeclared gates do not reuse Phase 4D as orchestration
evidence. See [PLANNING.md](PLANNING.md).

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
