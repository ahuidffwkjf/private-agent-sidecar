# Agent onboarding preview acceptance (2026-11-05)

## Delivered surface

- `scripts/sidecar_setup.py`: isolated installation, local device enrollment,
  authenticated configuration import, supervised open, status and Skill import.
- `scripts/issue_sidecar_access.py`: explicit administrator-approved access
  bundle issuance, bound to the client public key and source digest.
- `scripts/package_sidecar_installer.py` and `scripts/export_agent_skills.py`:
  credential-free source distribution, checksums and 361 operation Skill folders.
- `skills/sidecar-onboarding/`: self-contained onboarding Skill and installation
  reference; one onboarding plus 361 operation Skills are installed together.
- Tailcat launchers and `deploy/tailcat/` templates, with TLS and explicit allowlists.

This branch includes the current product modules needed by the installer and
Dashboard, not only the installer diff from the original initial-import main.
It excludes runtime identities, API tokens, TLS keys, databases, virtualenvs,
the historical source copy and plugins. Existing workspaces were not deleted.

## Verification

Commands used (the workspace Python runtime was used because this worktree has
no project virtualenv):

```text
python -m unittest tests.test_agent_install -v
python -m unittest discover -s tests -v
python scripts/verify_agent_install.py --tailcat /opt/homebrew/bin/tailcat --report runs/onboarding-release/acceptance.json
python scripts/run_federated_demo.py
python scripts/run_federated_process_demo.py
python -m agent_sidecar benchmark --config configs/benchmark/small.json --output runs/benchmark-onboarding-20261105
```

- Installer tests: 12 passed. Entire suite: 121 passed.
- Real Tailcat v0.7.0 local acceptance: package extraction into a fresh directory,
  dependency installation, private device identity generation, administrator
  bundle issuance, device-bound import, HTTPS registration through a real
  Tailcat forwarder, identity verification and Dashboard HTTP response passed.
- Repeat installation preserved the device identity; shutdown stopped managed
  processes and preserved data. Installation of all 362 Skill directories passed.
- Deterministic FL demo: 10 rounds, accuracy 0.995833, loss 0.046439.
- Independent-process FL demo: 3 rounds completed.
- Benchmark: 90 scenario runs; clean accuracy 0.995833. Per-scenario config and
  summary are retained in the output directory with matrix/comparison reports.
- Skill-format validation covers 362 generated/onboarding entrypoints. This
  verifies packaging syntax, not that 361 product capabilities have executors.

## Limits and next acceptance

This is a **source installer preview**, not a signed macOS application or a
fully offline bundle. Python 3.10+, venv/pip and network access are prerequisites.
Tailcat v0.7.0 is copied from a verified local install on macOS; Linux automatic
downloads use fixed official asset hashes. Linux/ARM runtime and systemd
deployment have not been exercised on actual target hardware in this release.

The live acceptance uses separate local processes, not independent physical
hosts. It does not prove cross-machine NAT traversal, DERP path quality or
production deployment. Existing server SSH still requires interactive user
authentication; no server was redeployed by this work.

The operation Skills retain `disabled-by-default`; they are agent-loadable
capability guidance, not 361 implemented WeChat/Sidecar executors. There is no
real WeChat automation or financial action execution in this package.

Next priority: approve an independent external device on a staging Relay and
repeat the installation/enrollment/open flow with verified TLS, recording
cross-machine connectivity without exposing Tailcat addresses or credentials.
