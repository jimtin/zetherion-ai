"use client";

import { useMemo, useState } from "react";

import { ResponsePanel } from "@/components/shared/response-panel";
import { useGatewayRunner } from "@/components/shared/use-gateway-runner";

export function AccessScreen() {
  const [tenantId, setTenantId] = useState("tenant-demo");
  const [discordUserId, setDiscordUserId] = useState("");
  const [role, setRole] = useState("member");
  const [targetUserId, setTargetUserId] = useState("");
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
        <p className="eyebrow">Tenant Access</p>
        <h2>Discord Users + Roles</h2>
        {errorText ? <p className="error-text">{errorText}</p> : null}

        <div className="actions-grid">
          <article className="action-card">
            <label>
              Tenant ID
              <input value={tenantId} onChange={(event) => setTenantId(event.target.value)} />
            </label>
            <label>
              Change Ticket ID (for high-risk mutations)
              <input
                placeholder="optional"
                value={changeTicketId}
                onChange={(event) => setChangeTicketId(event.target.value)}
              />
            </label>
            <label>
              Idempotency Key
              <input
                placeholder="optional"
                value={idempotencyKey}
                onChange={(event) => setIdempotencyKey(event.target.value)}
              />
            </label>
            <button
              type="button"
              disabled={loading}
              onClick={() => run({ path: `${adminPrefix}/discord-users` })}
            >
              List Users
            </button>
          </article>

          <article className="action-card">
            <h3>Add User</h3>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                run({
                  path: `${adminPrefix}/discord-users`,
                  method: "POST",
                  headers,
                  body: {
                    discord_user_id: discordUserId,
                    role,
                    change_ticket_id: changeTicketId || undefined
                  }
                });
              }}
            >
              <label>
                Discord User ID
                <input
                  value={discordUserId}
                  onChange={(event) => setDiscordUserId(event.target.value)}
                />
              </label>
              <label>
                Role
                <select value={role} onChange={(event) => setRole(event.target.value)}>
                  <option value="member">member</option>
                  <option value="manager">manager</option>
                  <option value="owner">owner</option>
                </select>
              </label>
              <button type="submit" disabled={loading || !discordUserId}>
                Add
              </button>
            </form>
          </article>

          <article className="action-card">
            <h3>Patch Role / Delete User</h3>
            <label>
              Target Discord User ID
              <input value={targetUserId} onChange={(event) => setTargetUserId(event.target.value)} />
            </label>
            <div className="inline-actions">
              <button
                type="button"
                disabled={loading || !targetUserId}
                onClick={() =>
                  run({
                    path: `${adminPrefix}/discord-users/${encodeURIComponent(targetUserId)}/role`,
                    method: "PATCH",
                    headers,
                    body: {
                      role,
                      change_ticket_id: changeTicketId || undefined
                    }
                  })
                }
              >
                Update Role
              </button>
              <button
                type="button"
                disabled={loading || !targetUserId}
                onClick={() =>
                  run({
                    path: `${adminPrefix}/discord-users/${encodeURIComponent(targetUserId)}`,
                    method: "DELETE",
                    headers,
                    body: {
                      change_ticket_id: changeTicketId || undefined
                    }
                  })
                }
              >
                Delete User
              </button>
            </div>
          </article>
        </div>
      </section>

      <ResponsePanel result={result} />
    </div>
  );
}
