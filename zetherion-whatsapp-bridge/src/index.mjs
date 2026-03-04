import { loadConfig } from "./config.mjs";
import { WhatsAppBridgeServer } from "./server.mjs";

async function main() {
  const config = loadConfig();
  const server = new WhatsAppBridgeServer(config);
  const listener = await server.listen();

  process.stdout.write(
    JSON.stringify(
      {
        event: "whatsapp_bridge_started",
        host: config.host,
        port: config.port,
        tenant_id: config.tenantId
      },
      null,
      2
    ) + "\n"
  );

  const shutdown = () => {
    listener.close(() => {
      process.exit(0);
    });
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

main().catch((error) => {
  process.stderr.write(`${String(error?.message || error)}\n`);
  process.exit(1);
});
