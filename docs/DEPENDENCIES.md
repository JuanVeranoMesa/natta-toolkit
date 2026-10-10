# Dependencies and platform compatibility

## Core and development

Python 3.11+ (uses `tomllib`, modern type syntax and pathlib methods), standard
library only. Tests use `unittest` and disposable Git repositories. No pip packages
are needed. Git must support `--no-optional-locks`, porcelain v1 `-z`, root/branch
inspection, diff checks, attribute inspection (`check-attr -z --stdin`), local
plumbing and the config flags used by commits. Command presence alone is not a
compatibility guarantee. No fabricated Git/Xcode minimum version is claimed.

Python versions on which the full test suite and `natta --help` have actually run
(macOS): 3.12, 3.13 and 3.14. Python 3.11 is the documented minimum and uses the
same eager-annotation semantics as 3.12/3.13, but no 3.11 run has been recorded
yet; a real 3.11 run remains a release-checklist item.

The POSIX `bin/natta` wrapper requires `sh`, `dirname`, and `python3` on PATH.
Its source paths resolve within this checkout. No dotfiles, shell aliases or global
site-package installation is required.

## Optional semantic provider/interface

An installed/authenticated Codex CLI and access to explicit `gpt-6-luna` are needed
for route/execute/plan/do; the interactive `codex` interface requires the CLI.
The source protocol tests use `codex-cli 0.160.0`. The runtime probes required flags
and feature compatibility (including `--no-daemon`, `--ignore-user-config`,
`--ignore-rules`, `--strict-config`, `--ephemeral`, `--output-schema`, JSON events
and host-skill-discovery control). A different CLI or unavailable model fails
closed; mere executable presence is insufficient. No compatibility claim is made
for every released Codex version. Runtime probes use local help/version/features,
not a model request. Provider authentication is managed externally.

## macOS, Xcode and Apple

Only macOS is supported/tested for v1. Native semantic inspection requires
`/usr/bin/sandbox-exec`, `/usr/bin/sw_vers`, `/usr/bin/git`, a compatible interpreter
and exact successful host-local fixture validation. Legacy sandbox availability is
not assumed. Other OSes are not supported despite portable portions of Python/Git.

Xcode-specific workflows require selected command-line tools, `xcodebuild`,
`xcrun`, an available compatible iOS Simulator runtime, and configured schemes/
targets. Level 3 uses `plutil` for Git-visible Apple metadata syntax. External
DerivedData/package/result directories are used; automatic package resolution is
disabled. Required package setup is deliberate work outside Natta.
`xcresulttool get test-results summary` JSON supplies best-effort test counts;
older/incompatible versions provide no invented counts. Exit/check results remain
authoritative. Native build/test/upload compatibility was not rerun on real apps
for extraction: the full suite mocks Apple commands.

TestFlight also invokes the macOS `security find-identity` probe for local
signing identity inspection; it never exports private keys.

TestFlight additionally needs a suitable Apple Developer team, bundle/version/build
metadata, signing/provisioning and App Store Connect access via existing Xcode
account or externally configured API-key authentication. `testflight --check` and
doctor remain local-only and do not establish remote-service readiness. No
credentials, account/signing artifacts or API keys are distributed. See
[the exact release prerequisites](../tools/natta/docs/TESTFLIGHT.md).

## Optional local-model research

Separate Python 3.12 (`>=3.12,<3.13`), an already installed uv-managed interpreter,
`uv`, Apple Silicon/MPS and explicit network-enabled setup. The isolated manifest
pins OpenJEV's source commit and requires torch >=2.0, transformers >=4.51.0 and
huggingface-hub. `uv.lock` pins resolved direct/transitive dependencies; it is kept
without environments or vendored sources. Transformers' Qwen3 floor is intentional.
MPS/FP32/offline inference is validated at runtime with no CPU/cloud fallback.
The source stack is not claimed compatible with every later package/hardware
version. Ordinary Natta commands never install/download/load these packages.
