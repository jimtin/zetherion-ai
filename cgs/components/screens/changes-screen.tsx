"use client";

import { useMemo, useState } from "react";

import { parseJsonInput } from "@/components/shared/json-helpers";
import { ResponsePanel } from "@/components/shared/response-panel";
import { useGatewayRunner } from "@/components/shared/use-gateway-runner";

export function ChangesScreen() {
  const [tenantId, setTenantId] = useState("tenant-demo");
  const [action, setAction] = useState("secret.rotate");
  const [target, setTarget] = useState("openai_api_key");
  const [payload, setPayload] = useState('{"reason":"key rollover"}');
  const [reason, setReason] = useState("Routine rotation");
  const [changeId, setChangeId] = useState("");
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
        <p className="eyebrow">Approvals</p>
        <h2>Change Queue</h2>
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
            <button type="button" disabled={loading} onClick={() => run({ path: `${adminPrefix}/changes` })}>
              List Changes
            </button>
          </article>

          <article className="action-card">
            <h3>Submit Change</h3>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                run({
                  path: `${adminPrefix}/changes`,
                  method: "POST",
                  headers,
                  body: {
                    action,
                    target,
                    payload: parseJsonInput(payload),
                    reason
                  }
                });
              }}
            >
              <label>
                Action
                <input value={action} onChange={(event) => setAction(event.target.value)} />
              </label>
              <label>
                Target
                <input value={target} onChange={(event) => setTarget(event.target.value)} />
              </label>
              <label>
                Reason
                <input value={reason} onChange={(event) => setReason(event.target.value)} />
              </label>
              <label>
                Payload JSON
                <textarea rows={4} value={payload} onChange={(event) => setPayload(event.target.value)} />
              </label>
              <button type="submit" disabled={loading}>
                Submit
              </button>
            </form>
          </article>

          <article className="action-card">
            <h3>Approve / Reject</h3>
            <label>
              Change ID
              <input value={changeId} onChange={(event) => setChangeId(event.target.value)} />
            </label>
            <div className="inline-actions">
              <button
                type="button"
                disabled={loading || !changeId}
                onClick={() =>
                  run({
                    path: `${adminPrefix}/changes/${encodeURIComponent(changeId)}/approve`,
                    method: "POST",
                    headers,
                    body: { reason }
                  })
                }
              >
                Approve
              </button>
              <button
                type="button"
                disabled={loading || !changeId}
                onClick={() =>
                  run({
                    path: `${adminPrefix}/changes/${encodeURIComponent(changeId)}/reject`,
                    method: "POST",
                    headers,
                    body: { reason }
                  })
                }
              >
                Reject
              </button>
            </div>
          </article>
        </div>
      </section>
      <ResponsePanel result={result} />
    </div>
  );
}
