"use client";

import { useMemo, useState } from "react";

import { parseJsonInput } from "@/components/shared/json-helpers";
import { ResponsePanel } from "@/components/shared/response-panel";
import { useGatewayRunner } from "@/components/shared/use-gateway-runner";

export function DocumentsScreen() {
  const [tenantId, setTenantId] = useState("tenant-demo");
  const [documentId, setDocumentId] = useState("");
  const [uploadId, setUploadId] = useState("");
  const [fileName, setFileName] = useState("proposal.pdf");
  const [mimeType, setMimeType] = useState("application/pdf");
  const [sizeBytes, setSizeBytes] = useState("1024");
  const [uploadMetadata, setUploadMetadata] = useState('{"source":"portal"}');
  const [fileBase64, setFileBase64] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState("");

  const { run, result, loading, errorText } = useGatewayRunner();

  const mutationHeaders = useMemo(
    () => (idempotencyKey.trim() ? { "Idempotency-Key": idempotencyKey.trim() } : undefined),
    [idempotencyKey]
  );

  const previewUrl = `/cgs/api/gateway/documents/${encodeURIComponent(documentId)}/preview?tenant_id=${encodeURIComponent(tenantId)}`;
  const downloadUrl = `/cgs/api/gateway/documents/${encodeURIComponent(documentId)}/download?tenant_id=${encodeURIComponent(tenantId)}`;

  return (
    <div className="screen-grid">
      <section className="panel">
        <p className="eyebrow">Documents</p>
        <h2>Document Center</h2>
        {errorText ? <p className="error-text">{errorText}</p> : null}

        <div className="actions-grid">
          <article className="action-card">
            <h3>Context</h3>
            <form>
              <label>
                Tenant ID
                <input value={tenantId} onChange={(event) => setTenantId(event.target.value)} />
              </label>
              <label>
                Document ID
                <input value={documentId} onChange={(event) => setDocumentId(event.target.value)} />
              </label>
              <label>
                Upload ID
                <input value={uploadId} onChange={(event) => setUploadId(event.target.value)} />
              </label>
              <label>
                Idempotency Key
                <input
                  placeholder="optional"
                  value={idempotencyKey}
                  onChange={(event) => setIdempotencyKey(event.target.value)}
                />
              </label>
            </form>
          </article>

          <article className="action-card">
            <h3>List / Detail / Streams</h3>
            <div className="inline-actions">
              <button
                type="button"
                disabled={loading}
                onClick={() => run({ path: `/documents?tenant_id=${encodeURIComponent(tenantId)}` })}
              >
                List Documents
              </button>
              <button
                type="button"
                disabled={loading || !documentId}
                onClick={() =>
                  run({
                    path: `/documents/${encodeURIComponent(documentId)}?tenant_id=${encodeURIComponent(tenantId)}`
                  })
                }
              >
                Get Detail
              </button>
              <a href={previewUrl} target="_blank" rel="noreferrer">
                <button type="button" disabled={!documentId}>
                  Preview Stream
                </button>
              </a>
              <a href={downloadUrl} target="_blank" rel="noreferrer">
                <button type="button" disabled={!documentId}>
                  Download Stream
                </button>
              </a>
            </div>
          </article>

          <article className="action-card">
            <h3>Create Upload Intent</h3>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                run({
                  path: "/documents/uploads",
                  method: "POST",
                  headers: mutationHeaders,
                  body: {
                    tenant_id: tenantId,
                    file_name: fileName,
                    mime_type: mimeType,
                    size_bytes: Number(sizeBytes || 0),
                    metadata: parseJsonInput(uploadMetadata)
                  }
                });
              }}
            >
              <label>
                File name
                <input value={fileName} onChange={(event) => setFileName(event.target.value)} />
              </label>
              <label>
                MIME type
                <input value={mimeType} onChange={(event) => setMimeType(event.target.value)} />
              </label>
              <label>
                Size bytes
                <input value={sizeBytes} onChange={(event) => setSizeBytes(event.target.value)} />
              </label>
              <label>
                Metadata JSON
                <textarea
                  rows={3}
                  value={uploadMetadata}
                  onChange={(event) => setUploadMetadata(event.target.value)}
                />
              </label>
              <button type="submit" disabled={loading}>
                Create Upload
              </button>
            </form>
          </article>

          <article className="action-card">
            <h3>Complete Upload (JSON)</h3>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                run({
                  path: `/documents/uploads/${encodeURIComponent(uploadId)}/complete`,
                  method: "POST",
                  headers: mutationHeaders,
                  body: {
                    tenant_id: tenantId,
                    file_base64: fileBase64,
                    metadata: parseJsonInput(uploadMetadata)
                  }
                });
              }}
            >
              <label>
                Base64 file payload
                <textarea
                  rows={4}
                  placeholder="Paste base64 for JSON completion tests"
                  value={fileBase64}
                  onChange={(event) => setFileBase64(event.target.value)}
                />
              </label>
              <button type="submit" disabled={loading || !uploadId}>
                Complete Upload
              </button>
            </form>
          </article>

          <article className="action-card">
            <h3>Reindex Document</h3>
            <button
              type="button"
              disabled={loading || !documentId}
              onClick={() =>
                run({
                  path: `/documents/${encodeURIComponent(documentId)}/index`,
                  method: "POST",
                  headers: mutationHeaders,
                  body: { tenant_id: tenantId }
                })
              }
            >
              Reindex
            </button>
          </article>
        </div>
      </section>

      <ResponsePanel result={result} />
    </div>
  );
}
