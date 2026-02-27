# ZetherionDevShell (macOS)

Minimal menu-bar shell for approving dev-environment cleanup policies.

## Prereqs
- macOS 14+
- Xcode 15+ (or Swift 5.10 toolchain)
- Running `zetherion-dev-agent daemon`

## Environment
Set these before launching:

```bash
export ZETHERION_DEV_AGENT_URL="http://127.0.0.1:8787/v1"
export ZETHERION_DEV_AGENT_TOKEN="<api_token_from_~/.zetherion-dev-agent/config.toml>"
```

## Run

```bash
cd macos/ZetherionDevShell
swift run
```

The menu bar app lists pending projects and lets you set:
- `auto_clean` (approve nightly cleanup)
- `never_clean` (deny cleanup)
