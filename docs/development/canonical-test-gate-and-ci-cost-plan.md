# Canonical Test Gate and Cost Plan

## Purpose

This repository uses a local-first validation contract so GitHub Actions does
not carry the heavy CI load.

All heavy validation runs locally or on the Windows worker.

## Canonical Local Gate

Run this command for full validation:

```bash
./scripts/test-full.sh
```

Windows wrapper:

```powershell
./scripts/test-full.ps1
```

If the change requires required-E2E receipt evidence, run:

```bash
bash scripts/local-required-e2e-receipt.sh
```

## Cost Rules

- GitHub external statuses are the public merge/deploy proof:
  - `zetherion/merge-readiness`
  - `zetherion/deploy-readiness`
- GitHub Actions is manual helper only.
- No GitHub-hosted heavy lint, unit, integration, or E2E gates should run on
  push or pull request.
- Windows-heavy execution belongs on `C:\ZetherionCI\agent-src` in
  Docker/Compose only.

## Helper Workflow Policy

Retained manual helper workflows:

- `Owner CI Bridge`
- `Deploy Windows`
- `Release`
- `CodeQL`
- `CI Maintenance`

Additional documentation helpers may remain manual, but they are not part of
merge or deploy readiness.

## Release Proof

Preferred proof path:

1. local heavy gate passes
2. Zetherion stores readiness and release receipts
3. GitHub shows green external statuses

Legacy fallback proof remains available only while the cutover completes:

- manual `Deploy Windows` helper
- legacy deployment receipt validation in `scripts/check-cicd-success.sh`

## Operating Rule

If merge or deploy readiness is red, fix the local or Windows-worker proof path
instead of adding more GitHub automation.
