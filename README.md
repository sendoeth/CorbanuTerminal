# stress-1201

Experiment branch for CorbanuTerminal #1201 (Windows flake in
`codex-login auth::manager::tests::unauthorized_recovery_uses_external_refresh_for_bearer_manager`).
Contains only the harness; each job checks out upstream CorbanuCore/CorbanuTerminal at a pinned commit.

- `tools/stress_1201.py`: runs the single test N times under idle / cpu_io / selfspawn load and records every
  provider-auth helper wait from the #1203 trace lines.
- `patches/pr1203-trace.diff`: #1203 (test-only timing trace), applied only to the pre-#1229 control commit.
- `.github/workflows/stress-1201.yml`: windows-2022 + windows-2025 on main, windows-2022 control before #1229.
