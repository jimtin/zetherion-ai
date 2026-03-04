export default function OverviewPage(): JSX.Element {
  return (
    <section className="panel hero">
      <p className="eyebrow">Go-Live Control Surface</p>
      <h2>CGS /service/ai/v1 Operator Interface</h2>
      <p>
        This console routes requests through a session-cookie BFF and exposes production flows for
        documents, retrieval, tenant admin, approval queue handling, and audit review.
      </p>
      <div className="overview-grid">
        <article>
          <h3>Security States</h3>
          <p>Step-up required, approval required, and retryable failures are surfaced inline.</p>
        </article>
        <article>
          <h3>Operator Coverage</h3>
          <p>Tenant lifecycle/admin mutations and reporting are mapped to canonical CGS routes.</p>
        </article>
        <article>
          <h3>Delivery Model</h3>
          <p>Next.js app is mounted at <code>/cgs</code> and proxies to <code>/service/ai/v1</code>.</p>
        </article>
      </div>
    </section>
  );
}
