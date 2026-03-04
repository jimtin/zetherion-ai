import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { EncryptedStateStore, defaultStateForTest } from "../src/state-store.mjs";
import { signPayload, verifySignature } from "../src/signing.mjs";
import { WhatsAppBridgeServer } from "../src/server.mjs";

test("state store encrypts + decrypts payload", () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "whatsapp-bridge-state-"));
  const statePath = path.join(tmpDir, "state.enc");
  const stateKey = Buffer.alloc(32, 7).toString("base64");

  const store = new EncryptedStateStore({ statePath, stateKey });
  const state = defaultStateForTest();
  state.bootstrapCompletedAt = "2026-03-04T00:00:00Z";
  state.apiToken = "token-value";
  store.save(state);

  const onDisk = fs.readFileSync(statePath, "utf8");
  assert.equal(onDisk.includes("token-value"), false);

  const loaded = store.load();
  assert.equal(loaded.apiToken, "token-value");
});

test("signing verifies canonical payload", () => {
  const secret = "test-secret";
  const payload = {
    secret,
    tenantId: "tenant-1",
    timestamp: "1700000000",
    nonce: "abc123",
    rawBody: '{"event_type":"x"}'
  };
  const signature = signPayload(payload);
  assert.equal(
    verifySignature({
      ...payload,
      signature
    }),
    true
  );
  assert.equal(
    verifySignature({
      ...payload,
      signature: "bad"
    }),
    false
  );
});

test("server blocks non-ingest endpoint configuration", () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "whatsapp-bridge-server-"));
  const statePath = path.join(tmpDir, "state.enc");
  const stateKey = Buffer.alloc(32, 9).toString("base64");

  assert.throws(
    () =>
      new WhatsAppBridgeServer({
        host: "127.0.0.1",
        port: 8877,
        bootstrapSecret: "b",
        bootstrapRequireOnce: true,
        ingestUrl: "http://localhost:8080/admin/tenants/t1/settings/models/default_provider",
        tenantId: "t1",
        signingSecret: "sig",
        skillsApiSecret: "",
        statePath,
        stateKey,
        outboundNonceTtlSeconds: 600
      }),
    /must target tenant messaging ingest endpoint/
  );
});
