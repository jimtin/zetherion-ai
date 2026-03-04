import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { resolveSessionToken } from "@/lib/server/auth";
import { proxyToGateway } from "@/lib/server/proxy";

interface RouteContext {
  params: {
    path: string[];
  };
}

function authMissingResponse(): NextResponse {
  return NextResponse.json(
    {
      request_id: "ui-auth-missing",
      data: null,
      error: {
        code: "AI_AUTH_MISSING",
        message: "Session cookie missing. Sign in again.",
        retryable: false
      }
    },
    { status: 401 }
  );
}

async function handleProxy(request: Request, { params }: RouteContext): Promise<NextResponse> {
  const gatewayBaseUrl =
    process.env.CGS_GATEWAY_BASE_URL ?? "http://zetherion-ai-traefik:8443/service/ai/v1";
  const cookieName = process.env.CGS_SESSION_COOKIE_NAME ?? "cgs_session";

  const sessionToken = resolveSessionToken(await cookies(), cookieName);
  if (!sessionToken) {
    return authMissingResponse();
  }

  return proxyToGateway(request, {
    gatewayBaseUrl,
    pathSegments: params.path,
    sessionToken
  });
}

export async function GET(request: Request, context: RouteContext): Promise<NextResponse> {
  return handleProxy(request, context);
}

export async function POST(request: Request, context: RouteContext): Promise<NextResponse> {
  return handleProxy(request, context);
}

export async function PUT(request: Request, context: RouteContext): Promise<NextResponse> {
  return handleProxy(request, context);
}

export async function PATCH(request: Request, context: RouteContext): Promise<NextResponse> {
  return handleProxy(request, context);
}

export async function DELETE(request: Request, context: RouteContext): Promise<NextResponse> {
  return handleProxy(request, context);
}
