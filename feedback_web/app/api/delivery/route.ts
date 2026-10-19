import {NextRequest, NextResponse} from "next/server";
import {isAdminRequest} from "@/lib/admin";
import {upsertDelivery} from "@/lib/feedback";
import {jsonError, readSmallJson} from "@/lib/http";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  if (!isAdminRequest(request)) {
    return NextResponse.json({error: "Unauthorized."}, {status: 401});
  }
  try {
    const payload = await readSmallJson(request);
    const result = await upsertDelivery({
      issue_id: String(payload.issue_id ?? ""),
      issue_number:
        payload.issue_number === null || payload.issue_number === undefined
          ? null
          : Number(payload.issue_number),
      series: payload.series === null ? null : String(payload.series ?? "") || null,
      sent_at: payload.sent_at === null ? null : String(payload.sent_at ?? "") || null,
      recipient_count: Number(payload.recipient_count ?? 0),
      recipient_group:
        payload.recipient_group === null
          ? null
          : String(payload.recipient_group ?? "") || null,
      recipient_fingerprint:
        payload.recipient_fingerprint === null
          ? null
          : String(payload.recipient_fingerprint ?? "") || null,
    });
    return NextResponse.json(result);
  } catch (error) {
    return jsonError(error);
  }
}
