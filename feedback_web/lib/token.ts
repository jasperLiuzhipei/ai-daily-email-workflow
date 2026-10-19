import {createHmac, timingSafeEqual} from "node:crypto";
import {feedbackSecret} from "@/lib/env";

const ISSUE_RE = /^[A-Za-z0-9._-]{1,64}$/;

export class FeedbackInputError extends Error {}

export type FeedbackIdentity = {
  issueId: string;
  recipientId: string;
  expiresAt: number;
};

function fromBase64Url(value: string): Buffer {
  return Buffer.from(value, "base64url");
}

export function verifyFeedbackToken(token: string): FeedbackIdentity {
  try {
    const [encodedPayload, encodedSignature, ...rest] = token.split(".");
    if (!encodedPayload || !encodedSignature || rest.length) {
      throw new FeedbackInputError("Invalid feedback token.");
    }
    const payload = fromBase64Url(encodedPayload);
    const signature = fromBase64Url(encodedSignature);
    const expected = createHmac("sha256", feedbackSecret()).update(payload).digest();
    if (signature.length !== expected.length || !timingSafeEqual(signature, expected)) {
      throw new FeedbackInputError("Invalid feedback token.");
    }
    const data = JSON.parse(payload.toString("utf8")) as {
      i?: unknown;
      r?: unknown;
      e?: unknown;
    };
    const issueId = String(data.i ?? "");
    const recipientId = String(data.r ?? "");
    const expiresAt = Number(data.e);
    if (
      !ISSUE_RE.test(issueId) ||
      !/^[a-f0-9]{32}$/.test(recipientId) ||
      !Number.isInteger(expiresAt)
    ) {
      throw new FeedbackInputError("Invalid feedback token.");
    }
    if (Math.floor(Date.now() / 1000) > expiresAt) {
      throw new FeedbackInputError("Feedback token has expired.");
    }
    return {issueId, recipientId, expiresAt};
  } catch (error) {
    if (error instanceof FeedbackInputError) {
      throw error;
    }
    throw new FeedbackInputError("Invalid feedback token.");
  }
}

export function validateIssueId(issueId: string): string {
  if (!ISSUE_RE.test(issueId)) {
    throw new FeedbackInputError("Invalid issue ID.");
  }
  return issueId;
}

export function validateEventId(eventId: unknown): string {
  const value = String(eventId ?? "").trim();
  if (!value || value.length > 128) {
    throw new FeedbackInputError("Invalid event ID.");
  }
  return value;
}
