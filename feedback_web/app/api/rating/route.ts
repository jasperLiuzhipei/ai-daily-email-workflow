import {NextResponse} from "next/server";
import {recordRating} from "@/lib/feedback";
import {jsonError, readSmallJson} from "@/lib/http";
import {verifyFeedbackToken} from "@/lib/token";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  try {
    const payload = await readSmallJson(request);
    const identity = verifyFeedbackToken(String(payload.token ?? ""));
    const result = await recordRating(identity, Number(payload.score), payload.event_id);
    return NextResponse.json(result);
  } catch (error) {
    return jsonError(error);
  }
}
