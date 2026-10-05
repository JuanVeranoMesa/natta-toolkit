> Standalone v1: current contracts are preserved below. Phase-labelled sections
> record historical domains/rationale; private empirical artifacts are excluded.
> See [documentation map and evaluation provenance](../../../docs/EVALUATION.md).

# Protected observable runtime effects

The production execute chain is Luna selection → local resolution → local policy →
authorization → protected BEFORE capture → bound handler → protected AFTER capture
→ policy-driven effect comparison → final result. Route never invokes the verifier.
A policy declaration is not runtime proof. This layer supplies evidence only for
its explicit local Git/project scope, not all declared effects or tool behavior.

## Shared snapshot infrastructure

`execution.snapshot(cwd)` retains its original behavior and dictionary contract:
SHA256 contents/symlink targets of tracked, untracked and ignored paths, porcelain
status, HEAD/current commit and raw index bytes. Existing direct workflows retain
those checks. The opt-in `snapshot(cwd, protected=True, baseline=...)` returns an
immutable ProtectedSnapshot for semantic execution; it does not hash the entire
repository or read arbitrary untracked file contents.

Protected evidence contains:

- Resolved registered repository root and resolved Git directory. A subdirectory
  masquerading as the registered repository root is refused. The canonical project
  alias is bound by the registry/resolver and remains in ExecutionResult.
- Current commit identity and symbolic branch reference; detached and unborn HEAD
  are supported. Branch/ref movement is compared even if commit bytes are unchanged.
- SHA256 of exact index bytes (or absent index), plus a fingerprint of Git stage
  entries including path/mode/object/stage, covering split-index logical entries.
  This includes staged/index state and
  conservatively notices index-only bookkeeping writes, not just staged content.
- SHA256 of binary-capable Git diff evidence for tracked working-tree state against
  a fixed pre-execution commit/tree baseline. Both captures use the same baseline;
  staging an already-dirty path does not by itself change this working-tree evidence.
  An algorithm-aware empty tree is used for an unborn repository, without writing
  objects. Preexisting dirty files are permitted; further observable edits change
  the diff fingerprint. Tracked membership transitions are conservatively observed.
- Sorted non-ignored untracked path names only. Adding/removing paths is observed;
  modifying contents of an already-present untracked path is explicitly NOT observed.

Git uses explicit argv, no optional index locks/refresh writes, disabled fsmonitor,
external diff/text conversion disabled and no rename heuristics. Repositories with
configured external clean/process filters or assume-unchanged/skip-worktree index
entries are refused before dispatch, rather than silently trusting hidden changes
or executing filter commands. Metadata responses are limited to 1 MiB; binary diff
and index hashing are limited to 64 MiB each. Commands time out after 30 seconds.
Diff evidence is streamed into a digest and never retained/rendered. Snapshots stay
in memory; no history or request logs are added.

Semantic build/test/verify select the protected mode for their existing discovery/
per-check/final integrity guards as well. Direct workflow bindings still use the
original mode. This shares the safe helper without changing direct command behavior
or exposing a new CLI flag. The outer verifier also runs after a workflow guard
raises; it never restores state.

## Mapping to authoritative policy effects

| Observed difference | Effect |
|---|---|
| Tracked diff fingerprint or non-ignored untracked path set | project_write |
| Exact index fingerprint/presence | git_index_write |
| Commit identity or branch/HEAD movement | git_commit |
| Repository/Git-directory redirection | all three, conservatively |

Git_commit here conservatively includes HEAD/ref movement; it is evidence of a
protected Git identity change, not proof that a new commit object was created.
Observed changes are compared with the revalidated authoritative policy's effects.
Only those effects can permit an observation. Raw requests, provider claims and
--confirm cannot waive violations. Inspection/build/test/verify deny all three mutation effects. The separately
reviewed commit capability permits only index and commit changes; branch and
repository redirection remain forbidden regardless of those effects.

## Result and failure semantics

ExecutionResult adds handler_succeeded (null before invocation, then boolean) and
immutable effect_verification: performed, passed, observed_effects, violations,
scope and reason. Effect identifiers are existing Effect enums, sorted in JSON.
No file contents, diff text or path lists enter verification output.

Performed means verification was attempted, not that every capture completed.
Passed=true requires completed before/after evidence and no unauthorized observation.
A failed attempt uses passed=false and a bounded reason. Scope lists attempted
observable dimensions; it does not prove those dimensions passed after capture failure.
No project target (projects/doctor) reports performed=false, passed=null, empty
scope/effects/violations, reason=no_project_target. This makes no claim about other
repositories inspected by doctor. Denials before execution report not_started.

- Valid handler exit zero plus required verification pass: executed, started=true,
  handler_succeeded=true, execution_succeeded=true. Projectless success has no
  applicable verification requirement.
- BEFORE unavailable/invalid: verification_unavailable, started=false, handler not
  called, handler_succeeded=null, reason=pre_execution_verification_failed, exit 1.
- AFTER unavailable/invalid: verification_unavailable, started=true, actual handler
  success preserved, overall success=false, post_execution_verification_failed,
  exit 1. This includes invalid comparison data; no success is fabricated.
- Unauthorized observation: effect_violation, started=true, actual handler success
  preserved, overall success=false, observed effects/violations reported, exit 1.
- Handler failure with unchanged protected state: execution_failed, started=true,
  handler_succeeded=false, overall success=false; existing handler exit preserved.
- Handler failure plus violation: effect_violation takes priority while preserving
  handler_succeeded=false and the handler's nonzero result.exit_code.

Result.exit_code remains the handler outcome; the CLI exit becomes 1 for effect
violations or verification unavailability even when the handler returned zero.
Executed still aliases execution_started. Exceptions still attempt AFTER capture.
Interrupt/forced process termination may prevent a final envelope; no success is
fabricated. Authorization-required/no_match/unresolved/provider-error paths take
no runtime snapshots. Exactly one Luna classification remains; verifier makes zero
provider calls. Doctor checks helper/effect-map integrity without snapshots, handler
invocation or provider requests.

## Enforcement gaps

This is before/after evidence, not continuous monitoring or rollback. A mutation
restored before AFTER may be invisible. Concurrent user/tool activity can cause a
violation without proving handler causation; avoid app edits during host acceptance.
The Git diff view follows Git content normalization/stat semantics; it is not a
forensic byte audit. Ignored untracked files/directories, existing untracked content,
other repositories, full recursive submodule contents, other refs/reflogs/config,
external derived artifacts, caches, simulator/runtime state and arbitrary filesystem
writes are not comprehensively verified. No locks prevent races during captures.

Network, pushes and external-service effects are not proven absent. They remain
policy-denied, but network/sandbox enforcement requires a separate design. Temporary,
persistent runtime, local process and simulator effects are declared, not measured
by this verifier. Violations are detected and reported, never reset/cleaned/restored.
No automatic rollback, compound orchestration or natta do is introduced.

## Commit-specific evidence

The existing protected_snapshot helper adds an optional bounded physical content/
mode fingerprint, selected only by capture_commit. Ordinary callers retain their
existing behavior and do not read untracked contents. Commit hashes tracked and
not-ignored untracked inputs (fixed before paths plus after paths), at most 20,000
paths/256 MiB per capture; symlink text is hashed without following targets.
Normal untracked-to-tracked/index membership changes after add/commit are allowed
without inventing project_write. Changed physical bytes/modes still violate commit
policy. Scope is bounded_worktree_contents, git_index, git_head, repository_identity.
Branch movement and repository redirection always fail. No content is exposed in
results. Ignored/transient/external/network effects remain outside proof. See
[COMMIT.md](COMMIT.md) for postconditions, failure truthfulness and no rollback.
