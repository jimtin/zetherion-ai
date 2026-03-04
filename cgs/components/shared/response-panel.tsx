import { toUxHint } from "@/lib/api/ux";
import type { GatewayCallResult } from "@/lib/types/gateway";

import { JsonView } from "@/components/shared/json-view";
import { StatusNotice } from "@/components/shared/status-notice";

interface ResponsePanelProps {
  result: GatewayCallResult | null;
}

export function ResponsePanel({ result }: ResponsePanelProps): JSX.Element {
  if (!result) {
    return (
      <section className="panel result-panel">
        <h3>Response</h3>
        <p>No request sent yet.</p>
      </section>
    );
  }

  return (
    <section className="panel result-panel">
      <h3>Response</h3>
      <p>
        HTTP <strong>{result.status}</strong>
      </p>
      <StatusNotice hint={toUxHint(result.envelope?.error ?? null)} />
      {result.envelope ? <JsonView value={result.envelope} /> : <JsonView value={result.textBody} />}
    </section>
  );
}
