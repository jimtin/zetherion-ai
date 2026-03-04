"use client";

import { useMemo, useState } from "react";

import { ResponsePanel } from "@/components/shared/response-panel";
import { useGatewayRunner } from "@/components/shared/use-gateway-runner";

export function SettingsScreen() {
  const [tenantId, setTenantId] = useState("tenant-demo");
  const [namespace, setNamespace] = useState("runtime");
  const [settingKey, setSettingKey] = useState("allow_attachments");
  const [value, setValue] = useState("true");
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
        <p className="eyebrow">Settings</p>
        <h2>Allowlisted Key Editor</h2>
        {errorText ? <p className="error-text">{errorText}</p> : null}

        <div className="actions-grid">
          <article className="action-card">
            <label>
              Tenant ID
              <input value={tenantId} onChange={(event) => setTenantId(event.target.value)} />
            </label>
            <label>
              Namespace
              <input value={namespace} onChange={(event) => setNamespace(event.target.value)} />
            </label>
            <label>
              Key
              <input value={settingKey} onChange={(event) => setSettingKey(event.target.value)} />
            </label>
            <label>
              Value
              <input value={value} onChange={(event) => setValue(event.target.value)} />
            </label>
            <label>
              Idempotency Key
              <input value={idempotencyKey} onChange={(event) => setIdempotencyKey(event.target.value)} />
            </label>
            <div className="inline-actions">
              <button
                type="button"
                disabled={loading}
                onClick={() => run({ path: `${adminPrefix}/settings` })}
              >
                List Settings
              </button>
              <button
                type="button"
                disabled={loading || !namespace || !settingKey}
                onClick={() =>
                  run({
                    path: `${adminPrefix}/settings/${encodeURIComponent(namespace)}/${encodeURIComponent(settingKey)}`,
                    method: "PUT",
                    headers,
                    body: { value }
                  })
                }
              >
                Upsert Setting
              </button>
              <button
                type="button"
                disabled={loading || !namespace || !settingKey}
                onClick={() =>
                  run({
                    path: `${adminPrefix}/settings/${encodeURIComponent(namespace)}/${encodeURIComponent(settingKey)}`,
                    method: "DELETE",
                    headers
                  })
                }
              >
                Delete Setting
              </button>
            </div>
          </article>
        </div>
      </section>
      <ResponsePanel result={result} />
    </div>
  );
}
