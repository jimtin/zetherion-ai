export function loadConfig() {
  const host = process.env.WHATSAPP_BRIDGE_HOST || "127.0.0.1";
  const port = Number.parseInt(process.env.WHATSAPP_BRIDGE_PORT || "8877", 10);
  const bootstrapSecret = String(process.env.WHATSAPP_BRIDGE_BOOTSTRAP_SECRET || "").trim();
  const bootstrapRequireOnce =
    String(process.env.WHATSAPP_BRIDGE_BOOTSTRAP_REQUIRE_ONCE || "true").toLowerCase() !==
    "false";

  const ingestUrl = String(process.env.WHATSAPP_BRIDGE_INGEST_URL || "").trim();
  const tenantId = String(process.env.WHATSAPP_BRIDGE_TENANT_ID || "").trim();
  const signingSecret = String(process.env.WHATSAPP_BRIDGE_SIGNING_SECRET || "").trim();
  const skillsApiSecret = String(process.env.WHATSAPP_BRIDGE_SKILLS_API_SECRET || "").trim();

  const statePath =
    String(process.env.WHATSAPP_BRIDGE_STATE_PATH || "").trim() ||
    "/app/data/whatsapp-bridge-state.enc";
  const stateKey = String(process.env.WHATSAPP_BRIDGE_STATE_KEY || "").trim();

  if (!Number.isFinite(port) || port <= 0 || port > 65535) {
    throw new Error("WHATSAPP_BRIDGE_PORT must be a valid TCP port");
  }

  if (!ingestUrl) {
    throw new Error("WHATSAPP_BRIDGE_INGEST_URL is required");
  }
  if (!tenantId) {
    throw new Error("WHATSAPP_BRIDGE_TENANT_ID is required");
  }
  if (!signingSecret) {
    throw new Error("WHATSAPP_BRIDGE_SIGNING_SECRET is required");
  }

  return {
    host,
    port,
    bootstrapSecret,
    bootstrapRequireOnce,
    ingestUrl,
    tenantId,
    signingSecret,
    skillsApiSecret,
    statePath,
    stateKey,
    outboundNonceTtlSeconds: Number.parseInt(
      process.env.WHATSAPP_BRIDGE_OUTBOUND_NONCE_TTL_SECONDS || "600",
      10
    )
  };
}
