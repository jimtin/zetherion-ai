"use client";

import { useState } from "react";

import { callGateway } from "@/lib/api/client";
import type { GatewayCallResult } from "@/lib/types/gateway";

interface RunOptions {
  path: string;
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  headers?: Record<string, string>;
}

export function useGatewayRunner() {
  const [result, setResult] = useState<GatewayCallResult | null>(null);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function run(options: RunOptions): Promise<GatewayCallResult | null> {
    setLoading(true);
    setErrorText(null);
    try {
      const init: RequestInit = {
        method: options.method ?? "GET",
        headers: options.headers
      };

      if (options.body !== undefined) {
        init.body = JSON.stringify(options.body);
      }

      const next = await callGateway(options.path, init);
      setResult(next);
      return next;
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unknown gateway request failure";
      setErrorText(message);
      return null;
    } finally {
      setLoading(false);
    }
  }

  return {
    run,
    result,
    errorText,
    loading
  };
}
