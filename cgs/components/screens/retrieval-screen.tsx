"use client";

import { useState } from "react";

import { ResponsePanel } from "@/components/shared/response-panel";
import { useGatewayRunner } from "@/components/shared/use-gateway-runner";

export function RetrievalScreen() {
  const [tenantId, setTenantId] = useState("tenant-demo");
  const [query, setQuery] = useState("Summarize implementation risks in this tenant's documents.");
  const [provider, setProvider] = useState("anthropic");
  const [model, setModel] = useState("claude-sonnet-4-6");
  const [topK, setTopK] = useState("6");

  const { run, result, loading, errorText } = useGatewayRunner();

  return (
    <div className="screen-grid">
      <section className="panel">
        <p className="eyebrow">RAG</p>
        <h2>Retrieval Panel</h2>
        {errorText ? <p className="error-text">{errorText}</p> : null}

        <div className="actions-grid">
          <article className="action-card">
            <h3>Context</h3>
            <label>
              Tenant ID
              <input value={tenantId} onChange={(event) => setTenantId(event.target.value)} />
            </label>
            <div className="inline-actions">
              <button
                type="button"
                disabled={loading}
                onClick={() => run({ path: `/models/providers?tenant_id=${encodeURIComponent(tenantId)}` })}
              >
                Load Provider Catalog
              </button>
            </div>
          </article>

          <article className="action-card">
            <h3>Ask Documents</h3>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                run({
                  path: "/rag/query",
                  method: "POST",
                  body: {
                    tenant_id: tenantId,
                    query,
                    provider,
                    model,
                    top_k: Number(topK || 6)
                  }
                });
              }}
            >
              <label>
                Query
                <textarea rows={5} value={query} onChange={(event) => setQuery(event.target.value)} />
              </label>
              <label>
                Provider
                <select value={provider} onChange={(event) => setProvider(event.target.value)}>
                  <option value="anthropic">anthropic</option>
                  <option value="claude">claude (alias)</option>
                  <option value="openai">openai</option>
                  <option value="groq">groq</option>
                </select>
              </label>
              <label>
                Model
                <input value={model} onChange={(event) => setModel(event.target.value)} />
              </label>
              <label>
                top_k
                <input value={topK} onChange={(event) => setTopK(event.target.value)} />
              </label>
              <button type="submit" disabled={loading}>
                Submit Query
              </button>
            </form>
          </article>
        </div>
      </section>
      <ResponsePanel result={result} />
    </div>
  );
}
