export type WebEventType =
  | "page_view"
  | "click"
  | "scroll_depth"
  | "form_start"
  | "form_submit"
  | "conversion"
  | "js_error"
  | "api_error"
  | "web_vitals"
  | "rage_click"
  | "dead_click";

export interface ObserverEvent {
  event_type: WebEventType;
  event_name: string;
  page_url?: string;
  element_selector?: string;
  properties: Record<string, unknown>;
  occurred_at: string;
  web_session_id?: string;
}

export interface ReplayChunkInput {
  sequence_no: number;
  object_key: string;
  checksum_sha256?: string;
  chunk_size_bytes: number;
  metadata?: Record<string, unknown>;
}

export interface ZetherionObserverOptions {
  apiBaseUrl: string;
  sessionToken: string;
  externalUserId?: string;
  contactId?: string;
  webSessionId?: string;
  metadata?: Record<string, unknown>;
  consentReplay?: boolean;
  replaySampleRate?: number;
  maskInputs?: boolean;
  maskText?: boolean;
  flushIntervalMs?: number;
  maxBatchSize?: number;
  sampledReplay?: boolean;
  fetchImpl?: typeof fetch;
}

export interface EndSessionOptions {
  contactId?: string;
  metadata?: Record<string, unknown>;
}
