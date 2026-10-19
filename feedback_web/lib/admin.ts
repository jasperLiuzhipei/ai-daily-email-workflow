import {createHmac, timingSafeEqual} from "node:crypto";
import type {NextRequest} from "next/server";
import {adminKey} from "@/lib/env";

export const ADMIN_COOKIE = "ai_daily_admin";

function sessionValue(): string {
  return createHmac("sha256", adminKey()).update("ai-daily-admin-session:v1").digest("base64url");
}

function safeEqual(left: string, right: string): boolean {
  const leftBytes = Buffer.from(left);
  const rightBytes = Buffer.from(right);
  return leftBytes.length === rightBytes.length && timingSafeEqual(leftBytes, rightBytes);
}

function basicPassword(request: NextRequest): string {
  const header = request.headers.get("authorization") ?? "";
  if (!header.startsWith("Basic ")) {
    return "";
  }
  try {
    const decoded = Buffer.from(header.slice(6), "base64").toString("utf8");
    const separator = decoded.indexOf(":");
    return separator >= 0 ? decoded.slice(separator + 1) : "";
  } catch {
    return "";
  }
}

export function validAdminPassword(candidate: string): boolean {
  return Boolean(candidate) && safeEqual(candidate, adminKey());
}

export function isAdminRequest(request: NextRequest): boolean {
  const headerKey = request.headers.get("x-admin-key") ?? "";
  if (validAdminPassword(headerKey) || validAdminPassword(basicPassword(request))) {
    return true;
  }
  const cookie = request.cookies.get(ADMIN_COOKIE)?.value ?? "";
  return Boolean(cookie) && safeEqual(cookie, sessionValue());
}

export function adminSessionCookie(): string {
  return sessionValue();
}

export function validAdminSession(candidate: string): boolean {
  return Boolean(candidate) && safeEqual(candidate, sessionValue());
}
