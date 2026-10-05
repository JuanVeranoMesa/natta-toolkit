# Implemented trust model

Natta v1 uses explicit capabilities, not arbitrary command generation. The
semantic selector/planner proposes bounded untrusted data; strict whole-response
JSON and CLI event validation reject unknown fields, duplicate keys, malformed
outputs, action events and unknown choices. Model output has no supported
arbitrary-command execution path. Restricted supplied context excludes repository
contents and authentication material. Planning supplies bounded project identity.

Before semantic dispatch, Natta validates CLI/catalog/parameter/binding/policy
parity, resolves project/arguments deterministically, revalidates identity and
authorization, and applies the existing protection requirements. Selection and
policy eligibility do not authorize execution. Explicit policies require terminal
approval or invocation-local `--confirm`; JSON/non-TTY cannot prompt. Compound
execution freezes at most four linear steps, authorizes the whole plan before
execution, pins targets and fails fast. No retries, replanning or rollback.

Semantic inspection (`status/context/diff`) requires exact host-local validation
of the macOS inspection backend. The deny-default sandbox profile allows broad
reads/processes but denies network and filesystem writes outside its owned
runtime (with `/dev/null` allowed). It is write/network confinement, not read
confidentiality. The public compatibility list starts empty and ships no receipt.
Semantic `projects` and `doctor` fail closed because they have no validated mode.
Direct commands remain available and do not all run inside this sandbox.

Build/test/verify, local commit and TestFlight enforce their declared authorization
and protected-state verification, without native workflow confinement. Snapshot
comparison checks observable project content/Git effects. Commit permits the
reviewed index/HEAD mutation while rejecting unrelated protected changes.
TestFlight declares its Apple-network effects and keeps credentials external.
Verification failure or unavailable required protection is not reported as success.

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
