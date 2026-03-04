import type { UxHint } from "@/lib/api/ux";

interface StatusNoticeProps {
  hint: UxHint | null;
}

export function StatusNotice({ hint }: StatusNoticeProps): JSX.Element | null {
  if (!hint) {
    return null;
  }

  return (
    <div className={`status-notice ${hint.tone}`}>
      <strong>{hint.title}</strong>
      <p>{hint.message}</p>
    </div>
  );
}
