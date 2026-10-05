> Standalone v1: current contracts are preserved below. Phase-labelled sections
> record historical domains/rationale; private empirical artifacts are excluded.
> See [documentation map and evaluation provenance](../../../docs/EVALUATION.md).

# Inspection confinement; Xcode workflow confinement deferred

This is the current production decision. macOS inspection confinement is supported
only after validation on the user's host. It uses the available **legacy /
deprecated** /usr/bin/sandbox-exec mechanism; primitive presence alone is not
validation. Xcode workflow confinement is **deferred research**, not a production
build/test/verify prerequisite. See [research findings](../../../docs/EVALUATION.md).

## Execution boundary

| Semantic target | Authorization | Execution protection |
|---|---|---|
| status/context/diff | automatic policy | current inspection validation, native worker, protected-state verifier |
| build/test/verify | explicit terminal approval or --confirm | static existing handler, mandatory pre/post protected-state verifier; no OS sandbox |
| commit | explicit terminal approval or --confirm | commit-aware protected-state verifier; no OS sandbox |
| testflight | explicit terminal approval or --confirm | workflow protected-state verifier and product postconditions; no OS sandbox |
| projects/doctor | automatic policy | semantic execution unavailable because no native mode is validated; direct commands available |

Capability type, declared effects, authorization and confinement are separate
axes. Only build/test/verify, commit and TestFlight have reviewed native-confinement exemptions;
new capabilities do not inherit that exemption merely by being workflows.

Luna selects one bounded capability. Natta validates it, resolves registry project
and bounded arguments, reevaluates policy and authorization, and binds an existing
handler. Raw requests never become handler arguments. An unconfirmed build/test/
verify never starts. No_match, unresolved parameters, forbidden effects, provider
failures and invalid metadata never execute. Route remains reporting-only.

## Native inspection backend

The generic Plan/BackendSession/Invocation/result interface remains separate from
the macOS implementation. A fresh Python worker runs the entire bound inspection
handler under sandbox-exec; the parent never invokes that handler in-process in
the native path. A handshake distinguishes setup failure from handler start.
Started failures still receive post-execution verification when practical.

The unchanged inspection profile uses deny-default, broad file reads/process
execution, sysctl-read, Mach lookup and POSIX IPC runtime allowances, and permits
writes only beneath one canonical private runtime root plus /dev/null. Network*
operations are denied. There is no project/index/HEAD write allowance. Direct and
child writes, symlink escape, runtime writes, network denial, reads and existing
status/context/diff operations are verified with disposable fixtures.

Private parent/runtime directories are 0700, owned by the current user. Generated
profiles are 0600 and ephemeral. Canonical paths and symlink checks fail closed.
Profiles accept no request/model paths. Child processes inherit native confinement
where supported; there is no executable allowlist. Broad reads/Mach/POSIX IPC are
intentional allowances. Services outside the confined process tree may act on a
client's behalf; their effects are not proven constrained. Same-user adversarial
code/path races and complete external-service prevention are not claimed.

## Validation and doctor

`natta confinement validate` performs zero Luna calls and no app/Xcode workflow.
It creates disposable fixtures, exercises the exact inspection profile and bound
inspection handlers, cleans up and saves success only after every check passes.
There is no --xcode or --verbose validation option anymore.

Inspection records remain in ~/Library/Application Support/NattaToolkit/confinement:
private directory 0700, owner-checked bounded JSON file 0600, no symlinks. Identity
includes OS version/build, Python identity, schema/profile hash and relevant
implementation hashes. Missing/stale/invalid records deny semantic inspection;
there is no silent unconfined downgrade. A narrow exact-release compatibility
manifest mechanism can retain explicitly audited exact-release records, but the
public manifest has no previous implementation entries and ships no host receipt. New records omit obsolete Xcode fields;
legacy inspection envelopes are accepted only with scope=null/xcode_validated=false.
Old Xcode records are ignored, not deleted from user runtime state.

Doctor reports primitive/backend presence, inspection validation and a successful
informational check: Xcode native confinement deferred / not required. It reads
structural/version/record facts, performs no native validation fixture or build,
and makes zero Luna requests. Deferred research is not a broken dependency.

## Workflow result and residual risks

For an authorized/resolved build/test/verify:

- confinement.required=false, applied=false, mode=null, backend=null,
  validated=false, reason=not_required_for_workflow.
- policy_denied_effects includes project_write, git_index_write, git_commit,
  git_push, network and external_service_mutation. Policy is not OS enforcement.
- enforced_effects and required_effects are empty.
- observation_only_effects lists project_write/git_index_write/git_commit,
  within the documented protected Git/project snapshot scope.
- unenforced_effects lists network/git_push/external_service_mutation.

Inspection results instead report required=true, backend=macos-sandbox-exec,
mode=inspection, validated/applied according to actual state, and four actively
enforced denied effects: project_write/git_index_write/git_commit/network.
Brokered external-service mutation and Git push are not claimed fully prevented.

Runtime snapshots support unchanged preexisting dirty state and compare tracked
worktree evidence, untracked path sets, index, HEAD/branch and repository identity.
They do not prove absence of ignored-file mutations, untracked-content changes,
transient restored changes, arbitrary filesystem writes elsewhere, network or
brokered service use. Workflow build phases/tests/tooling remain trusted code;
explicit authorization accepts these documented residual risks. Handler success
plus passing required runtime verification is necessary for overall success.
Violations override handler success; failed commands still receive post-checks.
Before-snapshot failure prevents execution; after-snapshot failure prevents overall
success. No rollback is performed.

## Scope retained

Direct status/context/diff/build/test/verify/doctor/projects retain their existing
behavior: no Luna, new confirmation or semantic sandbox requirement. Semantic
execute still makes exactly one Luna call; native confinement and runtime effect
verification make zero. Bounded do reuses the same dispatcher and per-step
protections; no new confinement backend or provider fallback is introduced.

See [local acceptance guidance](../../../docs/EVALUATION.md). Xcode Seatbelt research is
intentionally deferred; there is no next sandbox-debugging step in production.

## Local commit boundary

The reviewed atomic commit target does not use native inspection confinement.
It reports not_required_for_commit; index/HEAD mutation is allowed only by its
explicit policy and checked through mandatory commit-aware runtime verification.
Project_write remains policy-denied/observation-only; network/push/external effects
remain policy-denied and unenforced at OS level. The inspection profile/rules
have not changed. See [COMMIT.md](COMMIT.md).

## Provider and TestFlight scope

This native backend confines deterministic status/context/diff workers, not Codex
routing/planning processes or local-model evaluation. Sterile supplied context is
not OS-enforced host read isolation. TestFlight is explicitly unconfined and may
use Apple network/external services; project/index/HEAD writes remain denied and
observed by mandatory verification. No universal side-effect prevention is claimed.

The public distribution starts with an empty compatibility history and its own
current implementation digest. No personal receipt is carried forward. Source
changes can require fresh explicit local validation; unavailable native acceptance
never relaxes enforcement.
