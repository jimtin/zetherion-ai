import crypto from "node:crypto";
import http from "node:http";

import { EncryptedStateStore } from "./state-store.mjs";
import { randomNonce, signPayload } from "./signing.mjs";

const JSON_HEADERS = { "content-type": "application/json; charset=utf-8" };

function jsonResponse(res, status, payload) {
  res.writeHead(status, JSON_HEADERS);
  res.end(JSON.stringify(payload));
}

function safeEqual(a, b) {
  const left = Buffer.from(String(a || ""), "utf8");
  const right = Buffer.from(String(b || ""), "utf8");
  if (left.length !== right.length || left.length === 0) {
    return false;
  }
  return crypto.timingSafeEqual(left, right);
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) {
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}

function parseJsonObject(rawBody) {
  if (!rawBody.trim()) {
    return {};
  }
  const parsed = JSON.parse(rawBody);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("JSON body must be an object");
  }
  return parsed;
}

function requireIngestPath(ingestUrl, tenantId) {
  const parsed = new URL(ingestUrl);
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error("WHATSAPP_BRIDGE_INGEST_URL must use http/https");
  }

  const expectedBridgePath = `/bridge/v1/tenants/${tenantId}/messaging/ingest`;
  const expectedAdminPath = `/admin/tenants/${tenantId}/messaging/ingest`;
  if (parsed.pathname !== expectedBridgePath && parsed.pathname !== expectedAdminPath) {
    throw new Error(
      "WHATSAPP_BRIDGE_INGEST_URL path must target tenant messaging ingest endpoint"
    );
  }
}

function randomToken() {
  return crypto.randomBytes(32).toString("base64url");
}

function toIsoNow() {
  return new Date().toISOString();
}

function authTokenFromRequest(req) {
  const explicit = String(req.headers["x-whatsapp-bridge-token"] || "").trim();
  if (explicit) {
    return explicit;
  }

  const authHeader = String(req.headers.authorization || "").trim();
  if (!authHeader) {
    return "";
  }

  const [scheme, token] = authHeader.split(" ", 2);
  if (String(scheme || "").toLowerCase() !== "bearer") {
    return "";
  }
  return String(token || "").trim();
}

function pruneOutboundNonces(state, ttlSeconds) {
  const nowMs = Date.now();
  state.outboundNonces = (state.outboundNonces || []).filter((entry) => {
    const seenAt = Date.parse(String(entry.seenAt || ""));
    if (!Number.isFinite(seenAt)) {
      return false;
    }
    return nowMs - seenAt <= ttlSeconds * 1000;
  });
}

export class WhatsAppBridgeServer {
  constructor(config) {
    this._config = config;
    requireIngestPath(config.ingestUrl, config.tenantId);

    this._store = new EncryptedStateStore({
      statePath: config.statePath,
      stateKey: config.stateKey
    });
    this._state = this._store.load();
  }

  async _dispatchSignedEvent(eventPayload) {
    const rawBody = JSON.stringify(eventPayload);
    const timestamp = String(Math.floor(Date.now() / 1000));
    const nonce = randomNonce();
    const signature = signPayload({
      secret: this._config.signingSecret,
      tenantId: this._config.tenantId,
      timestamp,
      nonce,
      rawBody
    });

    const headers = {
      "content-type": "application/json",
      "x-bridge-timestamp": timestamp,
      "x-bridge-nonce": nonce,
      "x-bridge-signature": signature,
      "x-bridge-key-id": "whatsapp-bridge-v1"
    };

    if (this._config.skillsApiSecret) {
      headers["x-api-secret"] = this._config.skillsApiSecret;
    }

    const response = await fetch(this._config.ingestUrl, {
      method: "POST",
      headers,
      body: rawBody,
      signal: AbortSignal.timeout(10000)
    });

    const contentType = String(response.headers.get("content-type") || "").toLowerCase();
    let responseBody;
    if (contentType.includes("application/json")) {
      responseBody = await response.json();
    } else {
      responseBody = { text: await response.text() };
    }

    return {
      ok: response.ok,
      status: response.status,
      body: responseBody,
      signature,
      nonce,
      timestamp
    };
  }

  _isTokenAuthorized(req) {
    const token = authTokenFromRequest(req);
    return safeEqual(token, this._state.apiToken || "");
  }

  _requireToken(req, res) {
    if (!this._state.apiToken || !this._isTokenAuthorized(req)) {
      jsonResponse(res, 401, { error: "Unauthorized" });
      return false;
    }
    return true;
  }

  async _handleBootstrap(req, res) {
    if (!this._config.bootstrapSecret) {
      jsonResponse(res, 403, { error: "Bootstrap is disabled" });
      return;
    }

    const provided = String(req.headers["x-bootstrap-secret"] || "").trim();
    if (!safeEqual(provided, this._config.bootstrapSecret)) {
      jsonResponse(res, 401, { error: "Unauthorized bootstrap secret" });
      return;
    }

    const rawBody = await readBody(req);
    let data = {};
    try {
      data = parseJsonObject(rawBody || "{}");
    } catch {
      jsonResponse(res, 400, { error: "Invalid JSON body" });
      return;
    }

    if (this._config.bootstrapRequireOnce && this._state.bootstrapCompletedAt) {
      jsonResponse(res, 409, {
        error: "Bootstrap already completed",
        bootstrap_completed_at: this._state.bootstrapCompletedAt
      });
      return;
    }

    const rotateApiToken = data.rotate_api_token !== false;
    if (rotateApiToken || !this._state.apiToken) {
      this._state.apiToken = randomToken();
    }

    this._state.bootstrapCompletedAt = toIsoNow();
    this._store.save(this._state);

    jsonResponse(res, 200, {
      ok: true,
      api_token: this._state.apiToken,
      bootstrap_completed_at: this._state.bootstrapCompletedAt,
      tenant_id: this._config.tenantId,
      ingest_url: this._config.ingestUrl
    });
  }

  async _handleSessionStart(req, res) {
    if (!this._requireToken(req, res)) {
      return;
    }

    const rawBody = await readBody(req);
    let data = {};
    try {
      data = parseJsonObject(rawBody || "{}");
    } catch {
      jsonResponse(res, 400, { error: "Invalid JSON body" });
      return;
    }

    const ttlSeconds = Math.max(60, Number.parseInt(String(data.ttl_seconds || "300"), 10));
    const startedAt = Date.now();
    const expiresAt = new Date(startedAt + ttlSeconds * 1000).toISOString();

    this._state.session = {
      status: "pairing",
      pairingCode: `${Math.floor(100000 + Math.random() * 900000)}`,
      startedAt: new Date(startedAt).toISOString(),
      expiresAt
    };

    if (Array.isArray(data.seed_chats)) {
      this._state.chats = data.seed_chats
        .map((chat) => {
          if (!chat || typeof chat !== "object") {
            return null;
          }
          const chatId = String(chat.chat_id || "").trim();
          if (!chatId) {
            return null;
          }
          return {
            chat_id: chatId,
            display_name: String(chat.display_name || chatId)
          };
        })
        .filter(Boolean);
    }

    this._store.save(this._state);
    jsonResponse(res, 200, {
      ok: true,
      session: {
        status: this._state.session.status,
        pairing_code: this._state.session.pairingCode,
        started_at: this._state.session.startedAt,
        expires_at: this._state.session.expiresAt
      }
    });
  }

  async _handleSessionStatus(req, res) {
    if (!this._requireToken(req, res)) {
      return;
    }

    jsonResponse(res, 200, {
      status: this._state.session.status,
      pairing_code: this._state.session.pairingCode,
      started_at: this._state.session.startedAt,
      expires_at: this._state.session.expiresAt
    });
  }

  async _handleChats(req, res) {
    if (!this._requireToken(req, res)) {
      return;
    }

    jsonResponse(res, 200, {
      chats: (this._state.chats || []).map((chat) => ({
        chat_id: chat.chat_id,
        display_name: chat.display_name
      }))
    });
  }

  async _handleMessageSend(req, res) {
    if (!this._requireToken(req, res)) {
      return;
    }

    const rawBody = await readBody(req);
    let data;
    try {
      data = parseJsonObject(rawBody);
    } catch {
      jsonResponse(res, 400, { error: "Invalid JSON body" });
      return;
    }

    const chatId = String(data.chat_id || "").trim();
    const message = String(data.message || "").trim();
    if (!chatId || !message) {
      jsonResponse(res, 400, { error: "chat_id and message are required" });
      return;
    }

    const clientNonce = String(data.client_nonce || "").trim();
    pruneOutboundNonces(this._state, this._config.outboundNonceTtlSeconds);
    if (
      clientNonce &&
      this._state.outboundNonces.some((entry) => String(entry.nonce || "") === clientNonce)
    ) {
      jsonResponse(res, 409, { error: "Replay detected for client_nonce" });
      return;
    }

    if (clientNonce) {
      this._state.outboundNonces.push({ nonce: clientNonce, seenAt: toIsoNow() });
      this._store.save(this._state);
    }

    const eventPayload = {
      event_type: "whatsapp.message.send",
      tenant_id: this._config.tenantId,
      chat_id: chatId,
      message,
      occurred_at: toIsoNow(),
      metadata: {
        bridge_source: "local-sidecar",
        client_nonce: clientNonce || null
      }
    };

    let upstream;
    try {
      upstream = await this._dispatchSignedEvent(eventPayload);
    } catch (error) {
      jsonResponse(res, 502, {
        error: "Failed to deliver signed event",
        details: String(error?.message || error)
      });
      return;
    }

    if (!upstream.ok) {
      jsonResponse(res, 502, {
        error: "Ingest endpoint rejected event",
        upstream_status: upstream.status,
        upstream_body: upstream.body
      });
      return;
    }

    jsonResponse(res, 202, {
      ok: true,
      delivered: true,
      ingest_status: upstream.status,
      ingest_response: upstream.body
    });
  }

  async handle(req, res) {
    const url = new URL(req.url || "/", `http://${req.headers.host || "localhost"}`);
    if (req.method === "GET" && url.pathname === "/v1/health") {
      jsonResponse(res, 200, {
        status: "ok",
        tenant_id: this._config.tenantId,
        bootstrapped: Boolean(this._state.bootstrapCompletedAt),
        session_status: this._state.session.status
      });
      return;
    }

    if (req.method === "POST" && url.pathname === "/v1/bootstrap") {
      await this._handleBootstrap(req, res);
      return;
    }

    if (req.method === "POST" && url.pathname === "/v1/session/start") {
      await this._handleSessionStart(req, res);
      return;
    }

    if (req.method === "GET" && url.pathname === "/v1/session/status") {
      await this._handleSessionStatus(req, res);
      return;
    }

    if (req.method === "GET" && url.pathname === "/v1/chats") {
      await this._handleChats(req, res);
      return;
    }

    if (req.method === "POST" && url.pathname === "/v1/messages/send") {
      await this._handleMessageSend(req, res);
      return;
    }

    jsonResponse(res, 404, { error: "Not found" });
  }

  listen() {
    const server = http.createServer((req, res) => {
      this.handle(req, res).catch((error) => {
        jsonResponse(res, 500, {
          error: "Unhandled bridge error",
          details: String(error?.message || error)
        });
      });
    });

    return new Promise((resolve) => {
      server.listen(this._config.port, this._config.host, () => {
        resolve(server);
      });
    });
  }
}
