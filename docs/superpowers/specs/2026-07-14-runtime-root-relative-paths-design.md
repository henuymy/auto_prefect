# Runtime-Root Relative Paths Design

## Goal

Remove the `runtime/` prefix from production configuration and runtime-path
callers while preserving a single, externally configured runtime root.

## Contract

- `config/runtime.local.json` owns the absolute `runtime.root` value.
- Runtime values in module and dashboard JSON are paths relative to that root,
  for example `session/cookie_dump.json`.
- Code resolves those values with `runtime_path()` or a dedicated
  `resolve_runtime_relative_path()` helper. It must not join them to the
  repository path.
- Project resources such as `config/...` and `templates/...` remain project
  relative and continue to use project-path helpers.
- The old `runtime/...` prefix is rejected for production runtime values after
  the migration; callers cannot silently fall back to a repository directory.

## Path Categories

`session/...` stores cookies, browser state, locks, and alert state.
`config/...` stores runtime drafts and versions. `logs/...`, `health/...`,
`starter_templates/...`, and `temp/...` hold operational artifacts.
`modules/<name>/output/...` and `flow/<task>/{output,backup,debug,tmp}/...`
hold module and Flow artifacts.

## Migration

1. Add a root-relative resolver with the existing allow-list validation.
2. Update JSON runtime values and defaults to omit the `runtime/` prefix.
3. Update each runtime consumer to use the root-relative resolver; retain
   project resolvers for project-owned files.
4. Update APIs and logs to show root-relative runtime paths without exposing
   absolute host paths.
5. Reject legacy-prefixed production values and add regression tests for
   repository-isolation, JSON configuration, and each path category.

## Verification

Run focused runtime-path, configuration, session, module-output, Flow-output,
and dashboard tests; then run the full suite and `git diff --check`.
