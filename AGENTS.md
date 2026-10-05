# Natta Toolkit v1 repository contract

Read README.md and the normative contracts under tools/natta/docs/ before changes.
Preserve deterministic-first execution, explicit capability integration, strict
validation, authorization, structured results and fail-closed protection.
Routing selects; workflows compose; adapters implement mechanics. Direct commands
remain primary. Do not add arbitrary script discovery or autonomous execution.

Tests: from tools/natta run `python3 -B -m unittest discover -s tests -v`.
Use disposable repositories and mocked providers/Apple commands. Never mutate
registered real projects, invoke real semantic providers, commit projects or run
archive/upload as part of ordinary development validation. Dependency installation
and local-model downloads are explicit optional user actions.

Keep credentials, local registries, model weights, host receipts and retained
provider/evaluation output out of public source. No personal installation is needed.
