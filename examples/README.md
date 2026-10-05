# Register an existing repository

Copy `projects.toml` to `~/.config/natta/projects.toml` and edit it. Delete the
Xcode example if you have no Xcode repository. Example paths intentionally do not
resolve to bundled applications. `natta --registry examples/projects.toml projects`
can demonstrate parsing without executing a workflow.

A Python repository uses `generic-git`: status/context/diff, Level 1 diff checks,
and explicit local commit. No Python test runner is registered. Add `aliases` to
provide alternate CLI names and bounded semantic matching. Exact display names
are also matched by local semantic resolution; multiple projects remain ambiguous.

A relative path such as `../repos/example` is relative to the registry directory.
A home path such as `~/Projects/example` expands to the current user's home.
Document paths and Xcode selections must stay within the registered repository.

For `ios-xcode`, choose exactly one project/workspace and its actual scheme.
`test_targets` select normal test execution; `unit_targets` and `ui_tests` select
verification coverage. Level 1 runs simulator build and diff checks; Level 2 adds
unit/UI smoke tests; Level 3 adds syntax checks and explicit manual/optional
omissions. No simulator UDID is stored: compatible destinations are discovered.
Doctor validates discovery metadata but never builds. See
[adapter/result details](../tools/natta/docs/RESULT_CONTRACT.md).

Keep authentication, Apple keys, signing material and shell commands out of the
registry. These are selections for existing deterministic mechanics, not plugins.
