# Zetherion Web Observer SDK

Browser-side observer SDK for the Zetherion app watcher.

## What It Captures

- User behavior: `page_view`, `click`, `scroll_depth`, `form_start`, `form_submit`, `conversion`
- Friction signals: `rage_click`, `dead_click`
- Reliability signals: `js_error`, `api_error`
- Performance signals: `web_vitals` (LCP, INP, CLS, FCP)
- Optional replay metadata: replay chunk descriptors (`/api/v1/analytics/replay/chunks`)

## Privacy Defaults

- Input masking is enabled by default.
- Element text masking is enabled by default.
- Replay upload requires explicit consent and sampling.

## Quick Start

```ts
import { createZetherionObserver } from "./src/index";

const observer = createZetherionObserver({
  apiBaseUrl: "https://api.example.com",
  sessionToken: "zt_sess_...",
  externalUserId: "user_42",
  consentReplay: false,
  replaySampleRate: 0.1,
});

observer.start();

// Optional: wrap your fetch implementation for api_error capture
window.fetch = observer.observeFetch(window.fetch.bind(window));

// Track explicit conversion
observer.recordConversion("pricing_cta_click", { plan: "pro" });

// On app/session close
await observer.endSession({
  contactId: "9f4f6a5f-...",
  metadata: { source: "web" },
});
await observer.stop();
```

## Replay Chunk Upload

```ts
await observer.captureReplayChunk({
  sequence_no: 12,
  object_key: "replay/tenant-a/sess-1/chunk-12.bin",
  checksum_sha256: "...",
  chunk_size_bytes: 16384,
});
```

Replay chunks are only accepted when:

- `consentReplay` is true
- session sampling evaluates true
- tenant replay policy allows replay on the server

## Build

```bash
npm install
npm run build
```
