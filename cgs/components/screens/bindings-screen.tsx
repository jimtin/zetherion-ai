"use client";

import { useMemo, useState } from "react";

import { parseJsonInput } from "@/components/shared/json-helpers";
import { ResponsePanel } from "@/components/shared/response-panel";
import { useGatewayRunner } from "@/components/shared/use-gateway-runner";

export function BindingsScreen() {
  const [tenantId, setTenantId] = useState("tenant-demo");
  const [guildId, setGuildId] = useState("");
  const [channelId, setChannelId] = useState("");
  const [bindingPayload, setBindingPayload] = useState('{"status":"active"}');
  const [idempotencyKey, setIdempotencyKey] = useState("");

  const { run, result, loading, errorText } = useGatewayRunner();

  const adminPrefix = `/internal/admin/tenants/${encodeURIComponent(tenantId)}`;
  const headers = useMemo(
    () => (idempotencyKey.trim() ? { "Idempotency-Key": idempotencyKey.trim() } : undefined),
    [idempotencyKey]
  );

  return (
    <div className="screen-grid">
      <section className="panel">
        <p className="eyebrow">Discord Bindings</p>
        <h2>Guild + Channel Bindings</h2>
        {errorText ? <p className="error-text">{errorText}</p> : null}

        <div className="actions-grid">
          <article className="action-card">
            <label>
              Tenant ID
              <input value={tenantId} onChange={(event) => setTenantId(event.target.value)} />
            </label>
            <label>
              Idempotency Key
              <input value={idempotencyKey} onChange={(event) => setIdempotencyKey(event.target.value)} />
            </label>
            <button
              type="button"
              disabled={loading}
              onClick={() => run({ path: `${adminPrefix}/discord-bindings` })}
            >
              List Bindings
            </button>
          </article>

          <article className="action-card">
            <h3>Guild Default</h3>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                run({
                  path: `${adminPrefix}/discord-bindings/guilds/${encodeURIComponent(guildId)}`,
                  method: "PUT",
                  headers,
                  body: parseJsonInput(bindingPayload)
                });
              }}
            >
              <label>
                Guild ID
                <input value={guildId} onChange={(event) => setGuildId(event.target.value)} />
              </label>
              <label>
                Payload JSON
                <textarea
                  rows={4}
                  value={bindingPayload}
                  onChange={(event) => setBindingPayload(event.target.value)}
                />
              </label>
              <button type="submit" disabled={loading || !guildId}>
                Upsert Guild
              </button>
            </form>
          </article>

          <article className="action-card">
            <h3>Channel Override</h3>
            <label>
              Channel ID
              <input value={channelId} onChange={(event) => setChannelId(event.target.value)} />
            </label>
            <div className="inline-actions">
              <button
                type="button"
                disabled={loading || !channelId}
                onClick={() =>
                  run({
                    path: `${adminPrefix}/discord-bindings/channels/${encodeURIComponent(channelId)}`,
                    method: "PUT",
                    headers,
                    body: parseJsonInput(bindingPayload)
                  })
                }
              >
                Upsert Channel
              </button>
              <button
                type="button"
                disabled={loading || !channelId}
                onClick={() =>
                  run({
                    path: `${adminPrefix}/discord-bindings/channels/${encodeURIComponent(channelId)}`,
                    method: "DELETE",
                    headers
                  })
                }
              >
                Delete Channel
              </button>
            </div>
          </article>
        </div>
      </section>

      <ResponsePanel result={result} />
    </div>
  );
}
