# Segment C4 Receipt: Fresh Clone at `~/Developer/PersonalBot`

## Capability IDs
- `repo.cleanup.fresh-clone-swap`
- `repo.cleanup.local-runtime-asset-restore`

## Workflow Scenario IDs
- `repo.cleanup.c4.archive-live-checkout-before-reclone`
- `repo.cleanup.c4.clone-origin-main-into-canonical-path`
- `repo.cleanup.c4.restore-approved-runtime-assets-only`

## Fresh Clone Result
- Canonical repo path: `/Users/jameshinton/Developer/PersonalBot`
- Archived previous live checkout: `/Users/jameshinton/Developer/_archive/personalbot-cleanup-20260307T073510/PersonalBot-pre-fresh-clone-20260307T102640`
- Fresh clone source: `https://github.com/jimtin/zetherion-ai.git`
- Fresh clone branch: `main`
- Fresh clone HEAD: `dbf9cb32aad06eb1356dee8c4acfb6cf2496569e`
- `origin/main` after clone: `dbf9cb32aad06eb1356dee8c4acfb6cf2496569e`

## Restored Local Runtime Assets
- Restored:
  - `.env`
- Intentionally left in the archived checkout:
  - `.venv`
  - `data/`
  - `logs/`

## Verification Results
- Fresh clone worktree status after `.env` restore: clean
- Active git worktrees after reclone:
  - `/Users/jameshinton/Developer/PersonalBot`
- `Documents`-based PersonalBot workspaces present: `0`
- No compatibility symlink was created under `Documents`.

## Result
- `~/Developer/PersonalBot` is now a true GitHub-backed fresh clone.
- Only the approved local secret file (`.env`) was restored into the active checkout.
- Previous local runtime state remains preserved in the archived pre-clone snapshot if later inspection is needed.
