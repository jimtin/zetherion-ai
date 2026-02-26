import {
  EndSessionOptions,
  ObserverEvent,
  ReplayChunkInput,
  ZetherionObserverOptions,
} from "./types";

const DEFAULT_FLUSH_INTERVAL_MS = 5_000;
const DEFAULT_MAX_BATCH_SIZE = 50;
const SCROLL_MILESTONES = [25, 50, 75, 90] as const;
const SENSITIVE_INPUT_TYPES = new Set([
  "text",
  "email",
  "search",
  "tel",
  "url",
  "number",
  "password",
]);

interface ClickSample {
  timestamp: number;
  selector: string;
}

function normalizeBaseUrl(input: string): string {
  return input.replace(/\/$/, "");
}

function hashString(input: string): number {
  let hash = 2166136261;
  for (let i = 0; i < input.length; i += 1) {
    hash ^= input.charCodeAt(i);
    hash +=
      (hash << 1) +
      (hash << 4) +
      (hash << 7) +
      (hash << 8) +
      (hash << 24);
  }
  return hash >>> 0;
}

function shouldSample(seed: string, rate: number): boolean {
  if (rate <= 0) {
    return false;
  }
  if (rate >= 1) {
    return true;
  }
  const bucket = hashString(seed) / 0xffffffff;
  return bucket < rate;
}

function toErrorText(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

function getSelector(node: Element | null): string {
  if (!node) {
    return "";
  }
  if (node.id) {
    return `#${node.id}`;
  }

  const parts: string[] = [];
  let current: Element | null = node;
  let depth = 0;
  while (current && depth < 4) {
    let label = current.tagName.toLowerCase();
    if (current.classList.length > 0) {
      label += `.${Array.from(current.classList).slice(0, 2).join(".")}`;
    }
    parts.unshift(label);
    current = current.parentElement;
    depth += 1;
  }
  return parts.join(" > ");
}

function maskText(value: string | null | undefined, masked: boolean): string {
  if (!value) {
    return "";
  }
  if (masked) {
    return "[masked]";
  }
  return value.replace(/\s+/g, " ").trim().slice(0, 200);
}

export class ZetherionObserver {
  private readonly options: ZetherionObserverOptions;

  private readonly fetchImpl: typeof fetch;

  private readonly baseUrl: string;

  private readonly replaySampled: boolean;

  private readonly maskInputs: boolean;

  private readonly maskTextEnabled: boolean;

  private readonly maxBatchSize: number;

  private readonly flushIntervalMs: number;

  private webSessionId?: string;

  private queue: ObserverEvent[] = [];

  private flushTimer?: number;

  private flushInFlight = false;

  private listenersAbort?: AbortController;

  private clickHistory: ClickSample[] = [];

  private seenScrollMilestones = new Set<number>();

  private startedForms = new Set<string>();

  private pendingDeadClickTimerIds = new Set<number>();

  private vitals: Record<string, number> = {};

  private vitalsObserver?: PerformanceObserver;

  private clsValue = 0;

  constructor(options: ZetherionObserverOptions) {
    this.options = {
      consentReplay: false,
      replaySampleRate: 0,
      maskInputs: true,
      maskText: true,
      flushIntervalMs: DEFAULT_FLUSH_INTERVAL_MS,
      maxBatchSize: DEFAULT_MAX_BATCH_SIZE,
      ...options,
    };

    this.fetchImpl = options.fetchImpl ?? fetch.bind(globalThis);
    this.baseUrl = normalizeBaseUrl(options.apiBaseUrl);
    this.webSessionId = options.webSessionId;
    this.maskInputs = this.options.maskInputs !== false;
    this.maskTextEnabled = this.options.maskText !== false;
    this.maxBatchSize = Math.max(1, this.options.maxBatchSize ?? DEFAULT_MAX_BATCH_SIZE);
    this.flushIntervalMs = Math.max(500, this.options.flushIntervalMs ?? DEFAULT_FLUSH_INTERVAL_MS);

    if (typeof this.options.sampledReplay === "boolean") {
      this.replaySampled = this.options.sampledReplay;
    } else {
      this.replaySampled = shouldSample(
        `${this.options.sessionToken}:${this.options.externalUserId ?? "anon"}`,
        this.options.replaySampleRate ?? 0,
      );
    }
  }

  start(): void {
    if (typeof window === "undefined" || typeof document === "undefined") {
      return;
    }
    if (this.listenersAbort) {
      return;
    }

    this.listenersAbort = new AbortController();
    const signal = this.listenersAbort.signal;

    window.addEventListener("click", this.handleClick, { signal, capture: true });
    window.addEventListener("scroll", this.handleScroll, { signal, passive: true });
    window.addEventListener("focusin", this.handleFormFocus, { signal, capture: true });
    window.addEventListener("submit", this.handleFormSubmit, { signal, capture: true });
    window.addEventListener("error", this.handleRuntimeError, { signal });
    window.addEventListener("unhandledrejection", this.handlePromiseRejection, { signal });
    window.addEventListener("popstate", this.handleNavigation, { signal });
    window.addEventListener("hashchange", this.handleNavigation, { signal });
    document.addEventListener("visibilitychange", this.handleVisibilityChange, { signal });

    this.startWebVitals();
    this.track("page_view", "page_view", {
      title: maskText(document.title, this.maskTextEnabled),
      referrer: document.referrer,
    });

    this.flushTimer = window.setInterval(() => {
      void this.flush();
    }, this.flushIntervalMs);
  }

  async stop(): Promise<void> {
    if (this.listenersAbort) {
      this.listenersAbort.abort();
      this.listenersAbort = undefined;
    }

    if (this.flushTimer !== undefined && typeof window !== "undefined") {
      window.clearInterval(this.flushTimer);
      this.flushTimer = undefined;
    }

    for (const timerId of this.pendingDeadClickTimerIds) {
      if (typeof window !== "undefined") {
        window.clearTimeout(timerId);
      }
    }
    this.pendingDeadClickTimerIds.clear();

    if (this.vitalsObserver) {
      this.vitalsObserver.disconnect();
      this.vitalsObserver = undefined;
    }

    await this.flush();
  }

  track(
    eventType: ObserverEvent["event_type"],
    eventName = "",
    properties: Record<string, unknown> = {},
    elementSelector?: string,
  ): void {
    const pageUrl = typeof window !== "undefined" ? window.location.href : undefined;

    const event: ObserverEvent = {
      event_type: eventType,
      event_name: eventName,
      page_url: pageUrl,
      element_selector: elementSelector,
      properties,
      occurred_at: new Date().toISOString(),
      web_session_id: this.webSessionId,
    };

    this.queue.push(event);
    if (this.queue.length > this.maxBatchSize * 10) {
      this.queue = this.queue.slice(-this.maxBatchSize * 10);
    }

    if (this.queue.length >= this.maxBatchSize) {
      void this.flush();
    }
  }

  recordConversion(eventName = "conversion", properties: Record<string, unknown> = {}): void {
    this.track("conversion", eventName, properties);
  }

  observeFetch(fetchFn: typeof fetch = this.fetchImpl): typeof fetch {
    return async (input, init) => {
      const started = Date.now();
      const method = (init?.method ?? "GET").toUpperCase();
      const url = typeof input === "string" ? input : input.url;

      try {
        const response = await fetchFn(input, init);
        if (!response.ok) {
          this.track("api_error", "api_error", {
            status: response.status,
            method,
            url,
            duration_ms: Date.now() - started,
          });
        }
        return response;
      } catch (error) {
        this.track("api_error", "api_error", {
          method,
          url,
          duration_ms: Date.now() - started,
          error: toErrorText(error),
        });
        throw error;
      }
    };
  }

  async captureReplayChunk(chunk: ReplayChunkInput): Promise<boolean> {
    if (!this.options.consentReplay || !this.replaySampled) {
      return false;
    }
    if (!this.webSessionId) {
      await this.flush();
    }
    if (!this.webSessionId) {
      return false;
    }

    const response = await this.fetchImpl(`${this.baseUrl}/api/v1/analytics/replay/chunks`, {
      method: "POST",
      headers: this.requestHeaders,
      body: JSON.stringify({
        web_session_id: this.webSessionId,
        sequence_no: chunk.sequence_no,
        object_key: chunk.object_key,
        checksum_sha256: chunk.checksum_sha256,
        chunk_size_bytes: chunk.chunk_size_bytes,
        consent: true,
        sampled: this.replaySampled,
        metadata: chunk.metadata ?? {},
      }),
    });

    return response.ok;
  }

  async endSession(options: EndSessionOptions = {}): Promise<void> {
    await this.flush();

    await this.fetchImpl(`${this.baseUrl}/api/v1/analytics/sessions/end`, {
      method: "POST",
      headers: this.requestHeaders,
      body: JSON.stringify({
        web_session_id: this.webSessionId,
        contact_id: options.contactId ?? this.options.contactId,
        metadata: options.metadata ?? {},
      }),
    });
  }

  private get requestHeaders(): HeadersInit {
    return {
      "Content-Type": "application/json",
      Authorization: `Bearer ${this.options.sessionToken}`,
    };
  }

  private readonly handleClick = (event: MouseEvent): void => {
    const target = event.target instanceof Element ? event.target : null;
    const selector = getSelector(target);
    const elementText = target ? maskText(target.textContent, this.maskTextEnabled) : "";

    const inputValue =
      target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement
        ? this.maskInputs && SENSITIVE_INPUT_TYPES.has(target.type || "text")
          ? "[masked]"
          : target.value.slice(0, 200)
        : undefined;

    this.track(
      "click",
      "click",
      {
        text: elementText,
        input_value: inputValue,
        tag_name: target?.tagName.toLowerCase() ?? "",
      },
      selector,
    );

    const now = Date.now();
    this.clickHistory = this.clickHistory.filter((sample) => now - sample.timestamp < 1200);
    this.clickHistory.push({ timestamp: now, selector });
    const burstCount = this.clickHistory.filter((sample) => sample.selector === selector).length;
    if (selector && burstCount >= 3) {
      this.track("rage_click", "rage_click", { selector, burst_count: burstCount }, selector);
    }

    const urlAtClick = typeof window !== "undefined" ? window.location.href : "";
    if (typeof window !== "undefined" && selector) {
      const timerId = window.setTimeout(() => {
        if (window.location.href === urlAtClick) {
          this.track("dead_click", "dead_click", { selector }, selector);
        }
        this.pendingDeadClickTimerIds.delete(timerId);
      }, 1500);
      this.pendingDeadClickTimerIds.add(timerId);
    }
  };

  private readonly handleScroll = (): void => {
    if (typeof window === "undefined" || typeof document === "undefined") {
      return;
    }
    const doc = document.documentElement;
    const depth = Math.round(((window.scrollY + window.innerHeight) / Math.max(doc.scrollHeight, 1)) * 100);

    for (const milestone of SCROLL_MILESTONES) {
      if (depth >= milestone && !this.seenScrollMilestones.has(milestone)) {
        this.seenScrollMilestones.add(milestone);
        this.track("scroll_depth", "scroll_depth", { depth_pct: milestone });
      }
    }
  };

  private readonly handleFormFocus = (event: FocusEvent): void => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    const form = target.closest("form");
    if (!form) {
      return;
    }
    const selector = getSelector(form);
    if (!selector || this.startedForms.has(selector)) {
      return;
    }
    this.startedForms.add(selector);
    this.track("form_start", "form_start", { form_selector: selector }, selector);
  };

  private readonly handleFormSubmit = (event: SubmitEvent): void => {
    const form = event.target instanceof HTMLFormElement ? event.target : null;
    const selector = getSelector(form);
    this.track("form_submit", "form_submit", { form_selector: selector }, selector);
  };

  private readonly handleRuntimeError = (event: ErrorEvent): void => {
    this.track("js_error", "js_error", {
      message: event.message,
      filename: event.filename,
      line: event.lineno,
      column: event.colno,
    });
  };

  private readonly handlePromiseRejection = (event: PromiseRejectionEvent): void => {
    this.track("js_error", "unhandled_rejection", {
      reason: toErrorText(event.reason),
    });
  };

  private readonly handleNavigation = (): void => {
    this.track("page_view", "page_view", {
      title: typeof document !== "undefined" ? maskText(document.title, this.maskTextEnabled) : "",
    });
  };

  private readonly handleVisibilityChange = (): void => {
    if (typeof document !== "undefined" && document.visibilityState === "hidden") {
      this.flushVitals();
    }
  };

  private startWebVitals(): void {
    if (typeof PerformanceObserver === "undefined") {
      return;
    }

    try {
      this.vitalsObserver = new PerformanceObserver((entryList) => {
        for (const entry of entryList.getEntries()) {
          if (entry.entryType === "largest-contentful-paint") {
            this.vitals.lcp = Number(entry.startTime.toFixed(2));
          } else if (entry.entryType === "layout-shift") {
            const shift = entry as PerformanceEntry & { hadRecentInput?: boolean; value?: number };
            if (!shift.hadRecentInput) {
              this.clsValue += shift.value ?? 0;
              this.vitals.cls = Number(this.clsValue.toFixed(4));
            }
          } else if (entry.entryType === "first-input") {
            const firstInput = entry as PerformanceEntry & {
              processingStart?: number;
            };
            if (typeof firstInput.processingStart === "number") {
              this.vitals.inp = Number((firstInput.processingStart - entry.startTime).toFixed(2));
            }
          } else if (entry.entryType === "paint" && entry.name === "first-contentful-paint") {
            this.vitals.fcp = Number(entry.startTime.toFixed(2));
          }
        }
      });

      this.vitalsObserver.observe({
        entryTypes: ["largest-contentful-paint", "layout-shift", "first-input", "paint"],
      });
    } catch {
      this.vitalsObserver = undefined;
    }
  }

  private flushVitals(): void {
    if (Object.keys(this.vitals).length === 0) {
      return;
    }
    this.track("web_vitals", "web_vitals", { ...this.vitals });
  }

  private async flush(): Promise<void> {
    if (this.flushInFlight || this.queue.length === 0) {
      return;
    }
    this.flushInFlight = true;

    const batch = this.queue.splice(0, this.maxBatchSize);
    const payload = {
      web_session_id: this.webSessionId,
      external_user_id: this.options.externalUserId,
      consent_replay: this.options.consentReplay,
      metadata: this.options.metadata ?? {},
      events: batch.map((event) => ({
        ...event,
        web_session_id: event.web_session_id ?? this.webSessionId,
      })),
    };

    try {
      const response = await this.fetchImpl(`${this.baseUrl}/api/v1/analytics/events`, {
        method: "POST",
        headers: this.requestHeaders,
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        throw new Error(`analytics event ingest failed (${response.status})`);
      }

      const body = (await response.json()) as { web_session_id?: string };
      if (body.web_session_id && !this.webSessionId) {
        this.webSessionId = body.web_session_id;
      }
    } catch {
      this.queue = [...batch, ...this.queue];
    } finally {
      this.flushInFlight = false;
    }
  }
}

export function createZetherionObserver(options: ZetherionObserverOptions): ZetherionObserver {
  return new ZetherionObserver(options);
}

export type {
  EndSessionOptions,
  ObserverEvent,
  ReplayChunkInput,
  ZetherionObserverOptions,
} from "./types";
