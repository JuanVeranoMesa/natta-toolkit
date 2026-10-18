# Implemented trust model

Natta v1 uses explicit capabilities, not arbitrary command generation. The
semantic selector/planner proposes bounded untrusted data; strict whole-response
JSON and CLI event validation reject unknown fields, duplicate keys, malformed
outputs, action events and unknown choices. Model output has no supported
arbitrary-command execution path. Restricted supplied context excludes repository
contents and authentication material. Planning supplies bounded project identity;
each planned step's project and verify level must match what the goal itself names,
resolved locally, or the whole plan is rejected without a second model call.

Before semantic dispatch, Natta validates CLI/catalog/parameter/binding/policy
parity, resolves project/arguments deterministically, revalidates identity and
authorization, and applies the existing protection requirements. Selection and
policy eligibility do not authorize execution. Explicit policies require terminal
approval after routing, or invocation-local `--confirm --expect CAPABILITY` that
names the action; a route selecting any other capability fails before approval or
execution. JSON/non-TTY cannot prompt. Compound execution freezes at most four
linear steps, authorizes the whole displayed plan before execution, pins targets
and fails fast; `do --confirm` cannot preauthorize a plan with explicit steps,
because the model chose those steps after the flag was given. No retries,
replanning or rollback.

Semantic inspection (`status/context/diff`) requires exact host-local validation
of the macOS inspection backend. The deny-default sandbox profile allows broad
reads/processes but denies network and filesystem writes outside its owned
runtime (with `/dev/null` allowed). It is write/network confinement, not read
confidentiality. The public compatibility list names only audited public source
fingerprints and ships no host receipt.
Semantic `projects` and `doctor` fail closed because they have no validated mode.
Direct commands remain available and do not all run inside this sandbox.

Build/test/verify, local commit and TestFlight enforce their declared authorization
and protected-state verification, without native workflow confinement. Snapshot
comparison checks observable project content/Git effects. Commit permits the
reviewed index/HEAD mutation while rejecting unrelated protected changes.
TestFlight declares its Apple-network effects and keeps credentials external.
Verification failure or unavailable required protection is not reported as success.

Git commands run against the registered repository path with ambient `GIT_*`
variables removed, so `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_CONFIG_*`
and similar routing cannot redirect inspection, snapshots or commits. Protected
snapshots and the direct build/test/verify integrity snapshot read attributes
with `git check-attr` and refuse
(`external_filter_configured`) only when a configured clean/process filter applies
to a tracked or non-ignored untracked file; Natta never executes that filter. A
configured but unused filter, such as a global Git LFS installation in a
repository without `filter=lfs` attributes, does not affect availability.
Natta-owned Git inspection runs with `core.fsmonitor=false`, so a repository-configured
fsmonitor hook is never executed; the repository's configuration is not changed.

The direct build/test/verify integrity snapshot covers tracked files and untracked
files that are not ignored (by `.gitignore`, `info/exclude` or `core.excludesFile`),
hashed in streamed chunks, plus Git HEAD/index/status. Ignored build outputs,
caches and other ignored files are outside it: Natta does not verify every byte
under the repository directory.
Protected/workflow verification does not currently support repositories
containing Git submodules: any submodule (gitlink) in the index is refused with
`submodule_unsupported` (`submodule_commit_unsupported` for commit), read from the
index alone before any Git command could recurse into it or run a filter
configured inside it. Direct `status`, `context` and `diff` are ordinary Git
inspection and are not covered by this refusal.

## Non-guarantees

- Natta does not independently sandbox the provider process from reading the host.
  It can inherit host-level environment/process access according to its runtime.
- Bounded intentionally supplied context is not OS-enforced confidentiality.
  Codex flags and disabled features are protocol controls, not a new isolation system.
- Trusted Xcode phases/tests and other third-party code can write elsewhere or use
  network/external services. Policy-denied effects are not all actively prevented.
- Arbitrary third-party scripts are not safe by default. Registry shell code is not
  evaluated and arbitrary scripts are not drop-in capabilities.
- Post-execution verification is detection within observable scope. It is not proof
  of no external effects, a rollback engine, credential secrecy or malware isolation.
- A successful TestFlight upload is not remote processing completion or public release.

Read [routing](../tools/natta/docs/ROUTING.md),
[execution gate](../tools/natta/docs/EXECUTION_GATE.md),
[confinement](../tools/natta/docs/MACOS_CONFINEMENT.md), and
[runtime effects](../tools/natta/docs/RUNTIME_EFFECTS.md) for normative contracts.
