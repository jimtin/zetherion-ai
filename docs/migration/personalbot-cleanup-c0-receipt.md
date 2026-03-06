# Segment C0 Receipt: Preservation Inventory and Ref Backup

## Capability IDs
- `repo.cleanup.preservation-inventory`
- `repo.cleanup.ref-backup`

## Workflow Scenario IDs
- `repo.cleanup.c0.capture-local-personalbot-directory-inventory`
- `repo.cleanup.c0.capture-remote-branch-and-pr-inventory`
- `repo.cleanup.c0.create-bare-ref-backup`

## Generated Artifacts
- Archive root: `/Users/jameshinton/Developer/_archive/personalbot-cleanup-20260307T073510`
- Bare mirror backup: `/Users/jameshinton/Developer/_archive/personalbot-cleanup-20260307T073510/zetherion-ai.git`
- Cleanup manifest: `/Users/jameshinton/Developer/_archive/personalbot-cleanup-20260307T073510/cleanup-manifest.json`

## Canonical Remote Facts
- Origin URL: `https://github.com/jimtin/zetherion-ai.git`
- `origin/main` SHA at capture: `825fa99e41204c09e3409811452bd4b8dc71b259`
- Open PRs at capture: `0`
- Remote heads at capture:
  - `main` -> `825fa99e41204c09e3409811452bd4b8dc71b259`
  - `gh-pages` -> `db81ce1d61f9bbe889a7e39b7b6841792640d096`

## Local PersonalBot Directory Inventory
- `/Users/jameshinton/Developer/PersonalBot`
  - classification: `active_clone`
  - branch: `codex/seg-c0-preservation-inventory`
  - HEAD: `825fa99e41204c09e3409811452bd4b8dc71b259`
- `/Users/jameshinton/Developer/PersonalBot-discord-fix`
  - classification: `backup_clone`
  - detail: `linked_git_worktree`
  - branch: `main`
  - HEAD: `922a7ac7f5951232a45a21793aba8ceee7508264`

## Notes
- This segment is non-destructive. No local directories, remote branches, or PRs were deleted here.
- The machine-readable manifest is archived outside the repo; this receipt records the artifact paths and the captured state.
