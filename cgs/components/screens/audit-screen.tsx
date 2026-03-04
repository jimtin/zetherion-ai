"use client";

import { useState } from "react";

import { ResponsePanel } from "@/components/shared/response-panel";
import { useGatewayRunner } from "@/components/shared/use-gateway-runner";

export function AuditScreen() {
  const [tenantId, setTenantId] = useState("tenant-demo");
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");
  const [limit, setLimit] = useState("50");

  const { run, result, loading, errorText } = useGatewayRunner();

  const query = new URLSearchParams();
  if (actor.trim()) {
    query.set("actor_sub", actor.trim());
  }
  if (action.trim()) {
    query.set("action", action.trim());
  }
  if (limit.trim()) {
    query.set("limit", limit.trim());
  }

  return (
    <div className="screen-grid">
      <section className="panel">
        <p className="eyebrow">Audit</p>
        <h2>Audit Timeline</h2>
        {errorText ? <p className="error-text">{errorText}</p> : null}

        <div className="actions-grid">
          <article className="action-card">
            <label>
              Tenant ID
              <input value={tenantId} onChange={(event) => setTenantId(event.target.value)} />
            </label>
            <label>
              Actor Filter
              <input value={actor} onChange={(event) => setActor(event.target.value)} />
            </label>
            <label>
              Action Filter
              <input value={action} onChange={(event) => setAction(event.target.value)} />
            </label>
            <label>
              Limit
              <input value={limit} onChange={(event) => setLimit(event.target.value)} />
            </label>
            <button
              type="button"
              disabled={loading}
              onClick={() =>
                run({
                  path: `/internal/admin/tenants/${encodeURIComponent(tenantId)}/audit?${query.toString()}`
                })
              }
            >
              Query Audit
            </button>
            <button
              type="button"
              disabled={!result?.envelope}
              onClick={() => {
                if (!result?.envelope) {
                  return;
                }
                const blob = new Blob([JSON.stringify(result.envelope, null, 2)], {
                  type: "application/json"
                });
                const url = URL.createObjectURL(blob);
                const link = document.createElement("a");
                link.href = url;
                link.download = `audit-export-${tenantId}.json`;
                link.click();
                URL.revokeObjectURL(url);
              }}
            >
              Export JSON
            </button>
          </article>
        </div>
      </section>
      <ResponsePanel result={result} />
    </div>
  );
}
