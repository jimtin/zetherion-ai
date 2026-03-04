# zetherion-whatsapp-bridge

Local-only WhatsApp bridge sidecar for signed tenant messaging ingest.

## API

- `POST /v1/bootstrap`
- `GET /v1/health`
- `POST /v1/session/start`
- `GET /v1/session/status`
- `GET /v1/chats`
- `POST /v1/messages/send`

## Security defaults

- Binds to `127.0.0.1` by default.
- Requires `X-Bootstrap-Secret` for bootstrap when enabled.
- Requires token auth (`X-WhatsApp-Bridge-Token` or bearer token) after bootstrap.
- Persists state encrypted at rest via AES-256-GCM (`WHATSAPP_BRIDGE_STATE_KEY`).
- Only dispatches outbound events to tenant messaging ingest paths.
- Signs outbound events with HMAC SHA-256 (`X-Bridge-*` headers).

## Required env

- `WHATSAPP_BRIDGE_TENANT_ID`
- `WHATSAPP_BRIDGE_INGEST_URL`
- `WHATSAPP_BRIDGE_SIGNING_SECRET`
- `WHATSAPP_BRIDGE_STATE_KEY`
