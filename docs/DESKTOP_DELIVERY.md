# Desktop Delivery — 2026-11-06

Confirmed product baseline: **only 23 native communication Skills**, in four categories (8 node/trust, 4 actor communication, 2 public broadcast, 9 project collaboration). The source installer additionally contains one onboarding Skill, not an extra communication capability. Retired official WeChat manifests are archived on startup; custom Skills and chat data are preserved.

## Implemented

- `desktop/`: Electron desktop shell, isolated renderer, native menus, single instance, connection settings, pinned npm dependencies, icon, per-platform package configuration, source launchers and native application smoke tests.
- `agent_sidecar/desktop.py`: owned loopback runtime, private session cookie, host/origin validation, persistent workspace, UTF-8 stdin and shutdown on parent EOF.
- `.github/workflows/desktop.yml`: Windows x64, Linux x64, macOS arm64 and macOS Intel build targets; frozen-backend and real-window tests run before artifacts are uploaded. This workflow has not yet been pushed or run remotely.
- `scripts/sidecar_setup.py`, `scripts/package_sidecar_installer.py`: source bundles include the desktop code, lockfile, workflow, launchers and static assets without node_modules, credentials or runtime databases. Existing CLI/tunnel installer platform limits remain separate from desktop build targets.
- `tests/test_desktop.py`, `tests/test_agent_install.py`: lifecycle, persistence, authentication, malformed input, Chinese user-directory paths and source bundle coverage.
- `.gitignore`, `README.md`, `desktop/README.md`: build-output exclusions and platform-specific setup/distribution instructions.

## Verified Locally

- `.venv/bin/python -m unittest discover -s tests -v`: **135 tests passed**, log `/tmp/sidecar-23-skill-suite.log`. Old import paths are covered by compatibility tests that return only native communication tools. The onboarding documentation copies match. No test timeout was relaxed.
- `npm test` in `desktop/`: three desktop connection/navigation/command policy tests passed.
- `npm run dist`: completed on macOS arm64. DMG creation uses system `hdiutil`; image integrity verification passed. Installers and SHA-256 checksums are in `desktop/release/`.
- `npm run smoke`: packaged-window gate covers launch without source Python, sandbox/session settings, minimum desktop dimensions, shutdown, restart persistence, all 23 individual UI installations, MCP exposure, retired-ID rejection and remote HTTP rejection. The current result is recorded in `desktop/release/desktop-smoke.json` and `.png`.
- Frozen-backend smoke requires exactly 23 unique `sidecar.*` entries with `native-sidecar` execution, plus authenticated UI/API and clean shutdown from a temporary working directory. Do not infer functionality solely from a count; the communication integration suite exercises the individual tool contracts.
- `node --check` for main process, launcher and smoke-test scripts; `git diff --check`: passed.
- Deterministic and independent-process FL demos passed: `scripts/run_federated_demo.py`, `scripts/run_federated_process_demo.py`.
- Regression Benchmark: `runs/desktop-auth-regression-benchmark/`, 3 nodes, 3 rounds, Secure FedAvg, star topology, no attack. Final toy accuracy 0.9958333333, loss 0.0969663063, 18 modeled network messages, 3405 modeled bytes. Configuration and summary are preserved there. This is a regression experiment, not a security proof or real DeepSeek training result.

## Artifacts

- `desktop/release/Sidecar-0.2.0-mac-arm64.dmg`
- `desktop/release/Sidecar-0.2.0-mac-arm64.zip`
- `desktop/release/SHA256SUMS`
- Source bundle generation: `.venv/bin/python scripts/package_sidecar_installer.py --output dist/sidecar-23-desktop-source-20261106.zip`.

Source code is in the current local checkout. Large binaries, runtime data and build environments are intentionally ignored by Git. No commit, remote push or release publication has been performed.

## Remaining Gates

The previous mixed-catalog test conflict is resolved on the user-confirmed 23-tool baseline. The builder records `source-manifest.json` in the frozen backend and fails if source changes during freezing. The source bundler also rejects changes during export. Older source ZIPs are superseded by the explicitly named 23-Skill bundle above.

1. Push the reviewed desktop source plus necessary existing backend/UI dependencies and run the native Windows/Linux/macOS-Intel matrix. A matrix definition alone is not platform compatibility evidence.
2. Obtain Apple Developer signing/notarization and Windows signing credentials before frictionless public distribution. Current packages are unsigned; do not disable system security to install them.
3. Publish verified installers to a release or download location. Source download alone requires Node.js/Python for building; prebuilt packages do not.

Next highest-priority task: obtain approval to push a scoped branch with the required source and run the native cross-platform build matrix. Until these gates pass, the goal “every downloader can use it, not Mac-only” remains incomplete.
