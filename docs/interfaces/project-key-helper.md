# External Project-Key Helper Protocol

The `external` project-key backend is a narrow adapter boundary for an operator-owned TPM, PKCS#11, HSM or KMS helper. Sidecar does not claim that the helper is hardware-backed; deployment must provide and audit that implementation.

## Process contract

The configured command receives exactly one JSON object on stdin and returns exactly one JSON object on stdout. It must not log stdin, stdout, key material or request payloads.

Requests:

```json
{"operation":"save","project_id":"project-a","epoch":4,"key_b64":"...","key_fingerprint":"sha256:..."}
{"operation":"get","project_id":"project-a","epoch":4}
{"operation":"delete","project_id":"project-a","epoch":4}
```

Successful responses:

```json
{"ok":true}
{"ok":true,"record":{"project_id":"project-a","epoch":4,"key_b64":"...","key_fingerprint":"sha256:..."}}
```

For an absent key, `get` returns `{"ok":true,"record":null}`. Any non-zero exit, invalid JSON, `ok != true`, incomplete record or project/epoch mismatch is a hard error. Sidecar never falls back to SQLite or the file backend after an external helper failure.

## Hardware adapter guidance

A TPM/PKCS#11/HSM helper should store a wrapped secret or device-backed object under a deterministic opaque label derived from `project_id` and epoch. The helper should enforce device authorization and key deletion policy itself. Prefer a long-running authenticated helper or a carefully permissioned executable; never put key material in command-line arguments.

The repository contains protocol and failure tests but no vendor-specific hardware implementation. A deployment must provide its own helper and run `preflight --project-key-backend external --project-key-command ...` before starting Sidecar.
