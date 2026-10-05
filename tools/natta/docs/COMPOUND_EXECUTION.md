> Standalone v1: current contracts are preserved below. Phase-labelled sections
> record historical domains/rationale; private empirical artifacts are excluded.
> See [documentation map and evaluation provenance](../../../docs/EVALUATION.md).

# Phase 8B — bounded compound execution

Every useful Natta capability remains directly accessible through a short,
predictable human command. `natta do` is optional orchestration; `build example`,
`test example`, `commit example`, `doctor` and `codex example` retain their behavior.
`route` reports one capability, `execute` executes one, and `plan` only plans.

```sh
natta do "Check what changed in Example, build it, then test it"
natta do --confirm "Build Example, then test it"
natta do --json "Build Example, then test it"
```

## Architecture and frozen authorization unit

`compound_execution.run` calls the current planner-v3 once, yielding the existing
frozen PlanResult/Step objects. The allowed domain remains status/context/diff/
build/test/verify/commit/testflight, with one to four linear ordered steps. It locally checks
the complete plan with the existing planner validator and shared semantic
`validate_target` preflight. No snapshots or handlers run before authorization.
The existing policy computes automatic authorization only if every step is automatic.

Human output displays the entire canonical plan (display names and exact verify
levels). Any explicit step requires one whole-plan approval before step 1,
including before an automatic prefix. The shared terminal authorization helper
accepts only y/yes, case-insensitively. Immediately before the prompt, a local
summary names every frozen step and its arguments and states that y authorizes
execution of that displayed plan. Commit steps additionally state staging all
current changes, ONE local commit containing all current changes in the selected
project, and no push. JSON and non-TTY never prompt. Without
--confirm they return authorization_required and start nothing. Decline returns
authorization_declined and starts nothing. --confirm preauthorizes this invocation's
validated plan; wording never grants authorization.

After approval the entire unit is revalidated before step 1, then each exact typed
route uses the existing `semantic_execution.dispatch` with authorization satisfied
and its pinned project, schema, policy and handler. Project configuration, policy,
argument schema or handler changes fail closed rather than substitute targets.
The original frozen plan remains attached to the result. There is no second planner
call, per-step route, natural-language resolution, argument mutation or replan.

## Execution and failure boundary

Each dispatcher retains mandatory pre/post protected-state evidence. Inspection
uses the same host-validated confinement; build/test/verify remain unconfined
explicit workflows. Commit uses its existing commit-aware evidence and handler,
permitting only accepted index/HEAD mutation, staging all current project changes
and using the deterministic default message. It never pushes. TestFlight uses its reviewed explicit Apple-network workflow, product postconditions
and mandatory verification. No compound code executes Git or shell directly. Existing residual risks of unconfined workflows
and observable effect verification remain unchanged.

Steps execute sequentially, once per planned occurrence (explicit duplicate steps
remain separate occurrences). Any unsuccessful step, denied revalidation, handler
failure, effect violation, verification unavailability or confinement failure stops
all subsequent steps. A failed build/test cannot start a later commit. No retries,
rollback or partial-plan authorization exist. A commit created before a later
failure remains in place and its existing result reports the mutation truthfully.

The public inspection compatibility manifest starts empty. OS, interpreter,
profile and source identity still have to match a successful host-local validation
record; missing/stale validation stops execution.

## Compound result

Frozen CompoundResult contains the unchanged plan and ordered ExecutionResult-or-
not_started slots. JSON exports status, executed (alias of execution_started),
execution_started, execution_succeeded, provider, plan, authorization,
authorization_satisfied, steps, failed_step and error. Successful execution returns
status=executed, succeeded=true, failed_step=null and exit0. Failed steps preserve
their existing status and exit code; compound status mirrors the stopping step.
Denials/provider/no_match/invalid plans exit1. Before execution, succeeded=null;
a failed preflight/step has succeeded=false even if no handler started.

Each attempted step exports the complete existing ExecutionResult, including
runtime verification, confinement, workflow checks and commit result, plus index.
Unattempted steps have status=not_started, started=false, succeeded=null and a
bounded reason. A preflight failure can identify failed_step while every step is
not_started. Human output stays concise: complete plan, one optional prompt,
PASS/FAIL/SKIPPED rows, commit hash when present and final status.

## Local acceptance sequence

Run on the host; these are real Luna requests and B/C can run real workflows.
Check each displayed plan before answering. Start without commit:

```sh
# A: automatic read-only compound (requires existing inspection validation)
natta do "Show Example status, then show what changed in Example"
# B: type no; neither build nor test should start
natta do "Build Example, then test it"
# C: type yes after reviewing the complete build/test plan
natta do "Build Example, then test it"
# D: authorization_required, execution_started=false; no workflow runs
natta do --json "Build Example, then test it"
```

Only after A–D, review all current Example changes yourself and deliberately run:

```sh
natta do "Check Example, build it, test it, then commit it"
```

Review the displayed complete plan. Type yes only if you intend its final local
commit of **all** current Example changes. Earlier failures prevent commit. This
acceptance has not been run from Codex.

Deferred: push/deploy, arbitrary shell/Git, custom semantic or AI commit messages,
branching/loops/parallelism, retries/replanning/rollback, plan editing, more than
four steps, persistent run history and Xcode sandbox research.

## TestFlight extension

The direct `natta testflight PROJECT` product capability now archives and uploads
beta builds using Apple's supported Xcode distribution tooling. Its explicit
network/external-service policy is specific to TestFlight; source/Git writes stay
denied. Route/execute and bounded plan/do include the additive capability, using
one approval and existing per-step verification. Historical provider controls and
historical provider domains remain represented by public definitions. App Store public release is not implemented. Read the
[TestFlight contract](TESTFLIGHT.md) for exact prerequisites, results and boundaries.
