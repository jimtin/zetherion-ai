# CI/CD

Zetherion now uses a local-first CI/CD model.

## Control Plane

- Zetherion is the CI authority.
- GitHub branch protection should rely on the external status contexts
  `zetherion/merge-readiness` and `zetherion/deploy-readiness`.
- GitHub Actions is manual helper only.
- All heavy validation runs locally or on the Windows worker.

## Canonical Validation

Run the heavy gate locally before push, review, or promotion:

```bash
./scripts/test-full.sh
```

When required E2E evidence is needed, generate the receipt after the heavy gate
passes:

```bash
bash scripts/local-required-e2e-receipt.sh
```

The local gate remains the source of truth for lint, static analysis, unit,
integration, Docker E2E, and Discord E2E proof.

## GitHub Helper Workflows

These workflows are retained only as manual helpers:

- `Owner CI Bridge`
- `Deploy Windows`
- `Release`
- `CodeQL`
- `CI Maintenance`
- `Deploy Documentation`
- `Weekly Docs Gap Triage`
- `Sync Documentation to Wiki`

`Owner CI Bridge` is informational only. It does not make merge or deploy
decisions.

`Deploy Windows` is a manual fallback helper. It is no longer triggered by
GitHub CI.

## Promotion Rules

- Merge is allowed only when `zetherion/merge-readiness` is green.
- Deploy is healthy only when `zetherion/deploy-readiness` is green.
- A release is not healthy because containers are running.
- A release is healthy only when the `ReleaseVerificationReceipt` is green.

## Windows Worker

- The heavy executor runs from `C:\ZetherionCI\agent-src`.
- The deployed runtime path `C:\ZetherionAI` is not a mutable CI workspace.
- Windows validation stays Docker/Compose-only.

## Failure Handling

If the external statuses are red or missing:

1. fix the local or Windows-worker proof path first
2. publish corrected readiness receipts from Zetherion
3. only use GitHub helper workflows for manual fallback operations

If a legacy GitHub workflow run fails during the cutover period, treat it as
fallback evidence only, not the primary release authority.
