"use client";

import { useMemo, useState } from "react";

import { ResponsePanel } from "@/components/shared/response-panel";
import { useGatewayRunner } from "@/components/shared/use-gateway-runner";

export function SecretsScreen() {
  const [tenantId, setTenantId] = useState("tenant-demo");
  const [secretName, setSecretName] = useState("openai_api_key");
  const [changeTicketId, setChangeTicketId] = useState("");
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
        <p className="eyebrow">Secrets</p>
        <h2>Metadata + Rotate/Delete</h2>
        {errorText ? <p className="error-text">{errorText}</p> : null}

        <div className="actions-grid">
          <article className="action-card">
            <label>
              Tenant ID
              <input value={tenantId} onChange={(event) => setTenantId(event.target.value)} />
            </label>
            <label>
              Secret Name
              <input value={secretName} onChange={(event) => setSecretName(event.target.value)} />
            </label>
            <label>
              Change Ticket ID
              <input
                placeholder="required for approval-gated mutations"
                value={changeTicketId}
                onChange={(event) => setChangeTicketId(event.target.value)}
              />
            </label>
            <label>
              Idempotency Key
              <input value={idempotencyKey} onChange={(event) => setIdempotencyKey(event.target.value)} />
            </label>
            <div className="inline-actions">
              <button type="button" disabled={loading} onClick={() => run({ path: `${adminPrefix}/secrets` })}>
                List Secret Metadata
              </button>
              <button
                type="button"
                disabled={loading || !secretName}
                onClick={() =>
                  run({
                    path: `${adminPrefix}/secrets/${encodeURIComponent(secretName)}`,
                    method: "PUT",
                    headers,
                    body: {
                      change_ticket_id: changeTicketId || undefined
                    }
                  })
                }
              >
                Rotate Secret
              </button>
              <button
                type="button"
                disabled={loading || !secretName}
                onClick={() =>
                  run({
                    path: `${adminPrefix}/secrets/${encodeURIComponent(secretName)}`,
                    method: "DELETE",
                    headers,
                    body: {
                      change_ticket_id: changeTicketId || undefined
                    }
                  })
                }
              >
                Delete Secret
              </button>
            </div>
          </article>
        </div>
      </section>
      <ResponsePanel result={result} />
    </div>
  );
}
