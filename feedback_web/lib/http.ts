import {NextResponse} from "next/server";
import {FeedbackInputError} from "@/lib/token";

export function jsonError(error: unknown): NextResponse {
  if (error instanceof FeedbackInputError) {
    return NextResponse.json({error: error.message}, {status: 400});
  }
  console.error(error);
  return NextResponse.json({error: "服务暂时不可用，请稍后重试。"}, {status: 500});
}

export async function readSmallJson(request: Request): Promise<Record<string, unknown>> {
  const length = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(length) && length > 8192) {
    throw new FeedbackInputError("Invalid request body.");
  }
  const value = (await request.json()) as unknown;
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new FeedbackInputError("JSON body must be an object.");
  }
  return value as Record<string, unknown>;
}
