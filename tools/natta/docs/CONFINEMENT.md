> Standalone v1: current contracts are preserved below. Phase-labelled sections
> record historical domains/rationale; private empirical artifacts are excluded.
> See [documentation map and evaluation provenance](../../../docs/EVALUATION.md).

# Current confinement boundary

Inspection uses host-validated native sandbox-exec confinement. Build/test/verify
use explicit authorization and mandatory protected-state observation, without a
native sandbox requirement. See [current architecture](MACOS_CONFINEMENT.md).

Host-specific investigation/acceptance evidence is excluded. Every public
installation requires its own inspection validation; Xcode Seatbelt research
remains deferred. See [public extraction provenance](../../../docs/EVALUATION.md).

No confinement declaration proves all effects. Native inspection has documented
IPC/service limitations; unconfined workflows have documented filesystem/network
residual risks. Runtime verification remains enabled and never rolls back state.
