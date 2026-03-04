import { NextResponse } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

const cookiesMock = vi.fn();
const proxyMock = vi.fn();

vi.mock("next/headers", () => ({
  cookies: (...args: unknown[]) => cookiesMock(...args)
}));

vi.mock("@/lib/server/proxy", () => ({
  proxyToGateway: (...args: unknown[]) => proxyMock(...args)
}));

import { GET } from "@/app/api/gateway/[...path]/route";

describe("BFF auth behavior", () => {
  afterEach(() => {
    cookiesMock.mockReset();
    proxyMock.mockReset();
  });

  it("returns 401 when session cookie is missing", async () => {
    cookiesMock.mockResolvedValue({
      get: () => undefined
    });

    const response = await GET(new Request("http://localhost/cgs/api/gateway/documents"), {
      params: Promise.resolve({ path: ["documents"] })
    });

    expect(response.status).toBe(401);
    const body = await response.json();
    expect(body.error.code).toBe("AI_AUTH_MISSING");
    expect(proxyMock).not.toHaveBeenCalled();
  });

  it("proxies request when session cookie is available", async () => {
    cookiesMock.mockResolvedValue({
      get: (name: string) => (name === "cgs_session" ? { value: "jwt-token" } : undefined)
    });
    proxyMock.mockResolvedValue(NextResponse.json({ ok: true }, { status: 202 }));

    const response = await GET(new Request("http://localhost/cgs/api/gateway/documents?tenant_id=t1"), {
      params: Promise.resolve({ path: ["documents"] })
    });

    expect(response.status).toBe(202);
    expect(proxyMock).toHaveBeenCalledTimes(1);
    expect(proxyMock.mock.calls[0]?.[1]).toMatchObject({
      pathSegments: ["documents"],
      sessionToken: "jwt-token"
    });
  });
});
