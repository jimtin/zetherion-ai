import crypto from "node:crypto";

export function buildCanonicalPayload({ tenantId, timestamp, nonce, rawBody }) {
  return `${tenantId}.${timestamp}.${nonce}.${rawBody}`;
}

export function signPayload({ secret, tenantId, timestamp, nonce, rawBody }) {
  const canonical = buildCanonicalPayload({ tenantId, timestamp, nonce, rawBody });
  return crypto.createHmac("sha256", secret).update(canonical).digest("hex");
}

export function verifySignature({ secret, tenantId, timestamp, nonce, rawBody, signature }) {
  const expected = signPayload({ secret, tenantId, timestamp, nonce, rawBody });
  const expectedBuf = Buffer.from(expected, "utf8");
  const providedBuf = Buffer.from(String(signature || ""), "utf8");
  if (expectedBuf.length !== providedBuf.length) {
    return false;
  }
  return crypto.timingSafeEqual(expectedBuf, providedBuf);
}

export function randomNonce() {
  return crypto.randomBytes(16).toString("hex");
}
