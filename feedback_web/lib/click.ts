import {createHmac, timingSafeEqual} from "node:crypto";
import {feedbackSecret} from "@/lib/env";
import {FeedbackInputError} from "@/lib/token";

const LINK_ID_RE = /^[a-z0-9][a-z0-9_-]{0,95}$/;
const AUTOMATION_USER_AGENT =
  /\b(?:bot|crawler|spider|slurp|preview|scanner|safelinks|proofpoint|mimecast|barracuda|symantec|trend[ -]?micro|fortinet|urlscan|websense|phish|security|googleimageproxy|curl|wget|python-requests|postman|headless|facebookexternalhit|slackbot|discordbot|telegrambot)\b/i;

export type ClickClassification = {
  qualified: boolean;
  reason: string;
};

function clickPayload(
  token: string,
  linkId: string,
  destinationUrl: string,
  linkTitle: string,
): string {
  return ["ai-daily-click-v1", token, linkId, destinationUrl, linkTitle].join("\0");
}

export function validateLinkId(value: unknown): string {
  const linkId = String(value ?? "").trim();
  if (!LINK_ID_RE.test(linkId)) {
    throw new FeedbackInputError("Invalid link ID.");
  }
  return linkId;
}

export function normalizeClickTitle(value: unknown): string {
  const title = String(value ?? "").trim().replace(/\s+/g, " ");
  if (title.length > 240) {
    throw new FeedbackInputError("Link title is too long.");
  }
  return title;
}

export function validateClickDestination(value: unknown): string {
  const destination = String(value ?? "").trim();
  if (destination.length < 1 || destination.length > 4096) {
    throw new FeedbackInputError("Invalid click destination.");
  }
  try {
    const parsed = new URL(destination);
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
      throw new FeedbackInputError("Invalid click destination.");
    }
  } catch (error) {
    if (error instanceof FeedbackInputError) {
      throw error;
    }
    throw new FeedbackInputError("Invalid click destination.");
  }
  return destination;
}

export function verifyClickSignature(
  token: string,
  linkId: string,
  destinationUrl: string,
  linkTitle: string,
  encodedSignature: string | null,
): void {
  if (!encodedSignature) {
    throw new FeedbackInputError("Invalid click signature.");
  }
  try {
    const signature = Buffer.from(encodedSignature, "base64url");
    const expected = createHmac("sha256", feedbackSecret())
      .update(clickPayload(token, linkId, destinationUrl, linkTitle), "utf8")
      .digest();
    if (signature.length !== expected.length || !timingSafeEqual(signature, expected)) {
      throw new FeedbackInputError("Invalid click signature.");
    }
  } catch (error) {
    if (error instanceof FeedbackInputError) {
      throw error;
    }
    throw new FeedbackInputError("Invalid click signature.");
  }
}

export function classifyClickRequest(request: Request): ClickClassification {
  const userAgent = request.headers.get("user-agent")?.trim() ?? "";
  const purpose = [
    request.headers.get("purpose"),
    request.headers.get("sec-purpose"),
    request.headers.get("x-purpose"),
  ]
    .filter(Boolean)
    .join(" ");
  const scannerHeader = [
    "x-proofpoint-orig-guid",
    "x-proofpoint-vulnerability",
    "x-safelinks-url",
    "x-mimecast-spam-score",
  ].some((name) => request.headers.has(name));

  if (/\b(?:prefetch|preview)\b/i.test(purpose)) {
    return {qualified: false, reason: "prefetch_header"};
  }
  if (scannerHeader) {
    return {qualified: false, reason: "scanner_header"};
  }
  if (!userAgent) {
    return {qualified: false, reason: "missing_user_agent"};
  }
  if (AUTOMATION_USER_AGENT.test(userAgent)) {
    return {qualified: false, reason: "automated_user_agent"};
  }
  return {qualified: true, reason: "browser_request"};
}
