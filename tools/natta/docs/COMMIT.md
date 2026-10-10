> Standalone v1: current contracts are preserved below. Phase-labelled sections
> record historical domains/rationale; private empirical artifacts are excluded.
> See [documentation map and evaluation provenance](../../../docs/EVALUATION.md).

> Current Phase 8B addition: optional `natta do` executes a frozen bounded plan.
> Earlier statements below that compound execution is absent describe pre-8B scope.
> `route`, `execute` and `plan` retain their existing boundaries; direct CLI remains
> primary. See [compound execution contract](COMPOUND_EXECUTION.md).

# Deterministic local commit — first Git mutation capability

`natta commit PROJECT [--message MESSAGE] [--json]` resolves the existing registry,
requires a repository root and stages all current changes with argv-based
`git add -A -- .`. Preexisting staged changes, unstaged modifications/deletions
and not-ignored untracked files are included together. There is no partial-commit
intelligence. Another registered project works without a new handler or catalog.
Git identity comes from ordinary local/global Git configuration; Natta never
installs or repairs it. Commit does not push, fetch, pull or intentionally contact
a remote. There is no --push, amend, reset, revert, checkout or branch operation.

Default message is `Update <Project Display Name>`. --message is direct-only,
trimmed, nonempty, at most 512 characters, without control characters. It is one
Git -m argv value, never shell source or an option expansion. No AI message
creation or full-diff AI inspection occurs. The default is equally deterministic
in semantic execution.

## Git boundary

`commits.py` implements one local commit. It strips inherited GIT_* overrides,
disables optional locks, fsmonitor, external hooks, GPG signing, split-index
creation and automatic maintenance. Hooks are deliberately disabled by
core.hooksPath=/dev/null so they cannot mutate files, push or launch other tools.
Clean/process filters that apply to repository files (error
`external_filter_configured`; configured but unused filters are allowed), hidden
assume-unchanged/skip-worktree entries, unresolved merges, merge/rebase/
cherry-pick/revert state and registered repos with submodules (error
`submodule_commit_unsupported`, raised from the index before capture reads any
worktree state) are refused in this first version. These
require deliberate direct Git workflows, not automatic repair. Ignored files are
not added. Symlinks are staged as links, not followed outside the repository.

Dirty state is expected input. A clean repository returns no_changes,
committed=false, message/hash=null, exit0; no empty commit is made. Git/add/commit
failures exit1 and retain any staged state. There are no retries, automatic amend,
reset, revert, restoration or rollback.

## Policy and semantic execution

Commit is atomic, explicit and project-required, with no semantic arguments.
Its effects are local_read, project_read, git_read, local_process,
git_index_write and git_commit. Project_write, git_push, network and
external_service_mutation remain forbidden. Only the reviewed explicit commit
catalog entry may authorize index/commit mutation; build/test/verify and inspection
policies cannot acquire those effects merely by declaring them.

`natta route "Commit Example"` reports commit/project/policy and never executes.
`natta execute --json "Commit Example"` returns authorization_required, without a handler
or snapshot sequence. `natta execute --confirm "Commit Example"` routes once, resolves
and validates locally, snapshots, invokes one static commit binding, snapshots
again and checks effects. Custom-message wording, Git flags, push/amend/other
unsupported operations must be no_match; raw request text is never forwarded to
Git. Direct custom messages are separate validated CLI data. The updated semantic
behavior requires its focused host evaluation; old Phase 4D evidence does not
establish commit selection quality.

Commit does not use inspection confinement. Its result reports required=false,
applied=false, mode/backend=null, reason=not_required_for_commit. Authorization
cannot permit an undeclared effect. The native inspection backend remains required
for status/context/diff, with unchanged sandbox rules; the public compatibility manifest starts empty and requires local validation.
Direct existing commands retain their established behavior. Doctor checks policy,
static binding, commit's project-only parameter schema and effect-verifier mapping
without invoking a commit or provider.

## Commit-aware runtime evidence and postconditions

The existing protected_snapshot helper gains an optional bounded content mode,
used only by capture_commit. Ordinary inspection/build/test/verify retain the
accepted fixed-baseline Git-view semantics. Commit snapshots also hash physical
tracked and not-ignored untracked inputs and file modes, using fixed before paths
plus the after set. Missing paths and symlink text are represented without following
links. Evidence is limited to 20,000 paths and 256 MiB of content per capture;
limits or unreadable evidence fail closed. Contents and hashes are never dumped in
normal results. This is additional local evidence, not an AI data source.

Staging an untracked file legitimately removes it from Git's untracked set.
Deleting a previously tracked, already-missing file legitimately removes index
membership. These membership transitions must not fabricate project_write.
The commit-specific physical content/mode comparison distinguishes them from
actual file changes. Scope is bounded_worktree_contents, git_index, git_head,
repository_identity. Index/HEAD changes are allowed by commit policy. Branch
movement or repository/Git-directory redirection always fails independently;
commit authority never permits a branch switch or redirected repository.

Successful commit verifies one new child of the previous HEAD (or one root commit
for an unborn branch), matching returned hash/message, unchanged branch/repository
identity, normal clean Git state, and unchanged protected working-tree contents.
The direct handler verifies these postconditions; the semantic gate also retains
its mandatory outer pre/post verifier. Handler success alone is insufficient.
Failure still attempts after evidence. Detected violations override success and
are never undone. Avoid concurrent Git/file edits during the operation; they can
cause a failure even when the handler itself behaved correctly.

## Result

CommitResult is frozen and contains status, canonical project, committed,
commit_hash, message, bounded changed_paths (first 30) and changed_path_count,
pushed=false, exit_code, execution_started, git_commit_started, bounded error/
detail and effect_verification. Human success is `PASS <short hash> <message>`;
there are no full diffs. The semantic HandlerOutcome carries this same structured
object as ExecutionResult.result.commit.

committed=true means HEAD mutation was observed, including failure after a commit
was created. committed=false means no commit was observed; null means a started
Git commit's outcome could not be determined because post-evidence failed.
execution_started records entry into staging; git_commit_started records entry
into Git commit. Semantic execution_started retains its existing handler-start
meaning. Overall success requires handler/postconditions/verifier success.
If a created commit fails a later postcondition, its hash is reported truthfully
and left in place. Git diagnostics are at most 512 terminal-safe characters.

No push is invoked by the implementation. pushed=false is that command-level
fact, not a network sandbox certificate. Unconfined Git/local execution and
snapshots cannot prove absence of network/brokered external effects, ignored-file
writes, writes elsewhere or transient restored changes. Remote-tracking-ref
comparison during acceptance is extra local evidence, not remote-server proof.

## Separate frozen extension evidence

Historical Phase 4D corpora/descriptions/canonical/stability/Qwen/OpenJEV results
and accepted planner-v1 corpus, controls and results remain byte-identical.
router_codex retains the historical ROUTING_IDS domain for its old evaluator;
PRODUCTION_IDS adds commit. Original descriptions stay in config/routing.json;
the additive config/routing-commit-v2.json defines only the new capability.
Historical prompt/schema domains remain separate. Public corpora are sanitized
and their control hashes regenerated; private result artifacts are excluded.

planner_provider_v2 adds commit through phase8a-commit-v2 and reuses the existing
isolated planner transport. Its separate policy rejects whole goals containing
push, custom messages, arbitrary Git flags, shell or unsupported mutation. Plans
remain max4, ordered, non-executing. Commit contributes explicit authorization,
Git effects, no native confinement and mandatory future runtime verification.
Source v1 planning evaluation covered its six-step-capability universe;
it does not prove commit planning. The historical v1 evaluator remains usable
with its original controls/provider mode.

New frozen evidence:

- commit-routing-v2: 16 cases; commit/project variations, ambiguity/invented
  projects, push/custom-message/amend rejection and adversarial Git/shell requests.
  Exact results include deterministic project resolution. A generic capability
  selection may remain commit for an invented/missing project but must be unresolved.
- commit-planner-v2: 20 cases; commit, build/test/verify then commit, commit then
  status, project variation, push/safe-prefix rejection, custom messages, arbitrary
  Git flags, adversarial reset and ambiguous/invented projects.

Predeclared gates: complete corpus, zero provider/output failures, >=95% exact
accuracy, 100% unsupported/adversarial rejection, zero accepted unknown IDs/projects.
That requires 16/16 routing exact and at least 19/20 planner exact. Projection
metrics are descriptive only. The controls freeze source prompts/schema/
capabilities and corpus hashes before any real request. Both evaluators invoke no
handlers, snapshots or Xcode. Each case makes one call, no retries/fallback; exact
request authorization is required and outputs exclusively create new private files.
No compound execution is enabled by a passing result.

Historical routing revisions have separate regression and holdout definitions.
Their private result artifacts are excluded. Stale original routing controls still
reject revised provider inputs; current r2 controls and planner-v2 definitions
remain checked. See [evaluation provenance](../../../docs/EVALUATION.md).

Planner-only host command (20 requests, planning only):

```sh
cd tools/natta
python3 -B commit_evaluation.py planner --allow-external-requests 20 \
  --output "$PWD/evaluation/commit-planner-luna-v2.json"
```

## Host acceptance — disposable first

This zero-Luna procedure creates only a temporary Git fixture, proves default and
custom commit messages, mixed changes, clean no_changes, verification and cleanup:

```sh
cd tools/natta
python3 -B commit_acceptance.py fixture
```

Only after disposable acceptance passes and commit-focused routing acceptance
passes, knowingly choose ONE real project whose ALL current changes you want
committed. Do not choose merely because it is dirty. Review diffs yourself first;
Natta performs no content review. This example selects Example; substitute sample if
that is the intended project. Review takes a protected snapshot, prints Git status,
runs the semantic command without --confirm (one Luna request), proves denial and
unchanged protected state, and writes one private external review record:

```sh
natta_commit_project=example
python3 -B commit_acceptance.py review "$natta_commit_project" \
  --state "$PWD/evaluation/commit-host-review.json"
```

Stop here and inspect the displayed changes/default message. Only if you want
that ONE real commit, run the separate explicitly authorized step (one more Luna
request). It refuses if protected state changed since review, invokes semantic
execute --confirm, checks parent/hash/message/identity/effects and local remote
tracking refs, and never pushes or undoes the commit:

```sh
python3 -B commit_acceptance.py confirm \
  --state "$PWD/evaluation/commit-host-review.json" --confirm-real-project
```

No real app commits or Luna requests are run during development. Push, partial
staging, amend/revert/reset/checkout, AI messages, semantic custom messages
and submodule workflows remain future work. Bounded compound execution/natta do
is implemented separately and reuses the commit handler and verification.

Interactive semantic authorization is terminal-only and continues the same frozen
resolved action without a second Luna call. --json/non-TTY never prompt; --confirm
preauthorizes one invocation. Direct commands stay noninteractive. See
[authorization contract](EXECUTION_GATE.md#interactive-authorization).
