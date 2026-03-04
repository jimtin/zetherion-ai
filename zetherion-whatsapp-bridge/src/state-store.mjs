import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const NONCE_BYTES = 12;
const TAG_BYTES = 16;

function decodeKey(rawKey) {
  const trimmed = String(rawKey || "").trim();
  if (!trimmed) {
    throw new Error("WHATSAPP_BRIDGE_STATE_KEY is required");
  }

  if (/^[0-9a-fA-F]{64}$/.test(trimmed)) {
    return Buffer.from(trimmed, "hex");
  }

  try {
    const decoded = Buffer.from(trimmed, "base64");
    if (decoded.length === 32) {
      return decoded;
    }
  } catch {
    // Fall through to explicit error.
  }

  throw new Error("WHATSAPP_BRIDGE_STATE_KEY must be 32-byte base64 or 64-char hex");
}

function encryptJson(data, key) {
  const nonce = crypto.randomBytes(NONCE_BYTES);
  const cipher = crypto.createCipheriv("aes-256-gcm", key, nonce);
  const plaintext = Buffer.from(JSON.stringify(data), "utf8");
  const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  const tag = cipher.getAuthTag();
  return Buffer.concat([nonce, tag, ciphertext]).toString("base64");
}

function decryptJson(payload, key) {
  const raw = Buffer.from(String(payload || ""), "base64");
  if (raw.length <= NONCE_BYTES + TAG_BYTES) {
    throw new Error("Encrypted state payload is too short");
  }

  const nonce = raw.subarray(0, NONCE_BYTES);
  const tag = raw.subarray(NONCE_BYTES, NONCE_BYTES + TAG_BYTES);
  const ciphertext = raw.subarray(NONCE_BYTES + TAG_BYTES);

  const decipher = crypto.createDecipheriv("aes-256-gcm", key, nonce);
  decipher.setAuthTag(tag);
  const plaintext = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
  const parsed = JSON.parse(plaintext.toString("utf8"));
  if (!parsed || typeof parsed !== "object") {
    throw new Error("Encrypted state is not a JSON object");
  }
  return parsed;
}

const DEFAULT_STATE = {
  version: 1,
  bootstrapCompletedAt: "",
  apiToken: "",
  session: {
    status: "idle",
    pairingCode: "",
    startedAt: "",
    expiresAt: ""
  },
  chats: [],
  outboundNonces: []
};

export class EncryptedStateStore {
  constructor({ statePath, stateKey }) {
    this._path = path.resolve(statePath);
    this._key = decodeKey(stateKey);
  }

  load() {
    if (!fs.existsSync(this._path)) {
      return structuredClone(DEFAULT_STATE);
    }

    const encrypted = fs.readFileSync(this._path, "utf8").trim();
    if (!encrypted) {
      return structuredClone(DEFAULT_STATE);
    }

    const parsed = decryptJson(encrypted, this._key);
    return {
      ...structuredClone(DEFAULT_STATE),
      ...parsed,
      session: {
        ...structuredClone(DEFAULT_STATE).session,
        ...(parsed.session && typeof parsed.session === "object" ? parsed.session : {})
      },
      chats: Array.isArray(parsed.chats) ? parsed.chats : [],
      outboundNonces: Array.isArray(parsed.outboundNonces) ? parsed.outboundNonces : []
    };
  }

  save(state) {
    const payload = {
      ...structuredClone(DEFAULT_STATE),
      ...state,
      session: {
        ...structuredClone(DEFAULT_STATE).session,
        ...(state.session && typeof state.session === "object" ? state.session : {})
      },
      chats: Array.isArray(state.chats) ? state.chats : [],
      outboundNonces: Array.isArray(state.outboundNonces) ? state.outboundNonces : []
    };

    const encrypted = encryptJson(payload, this._key);
    fs.mkdirSync(path.dirname(this._path), { recursive: true });
    fs.writeFileSync(this._path, encrypted, { mode: 0o600 });
  }
}

export function defaultStateForTest() {
  return structuredClone(DEFAULT_STATE);
}
