import {NextRequest, NextResponse} from "next/server";
import {isAdminRequest} from "@/lib/admin";
import {listIssueSummaries} from "@/lib/feedback";
import {jsonError} from "@/lib/http";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  if (!isAdminRequest(request)) {
    return NextResponse.json({error: "Unauthorized."}, {status: 401});
  }
  try {
    return NextResponse.json({issues: await listIssueSummaries()});
  } catch (error) {
    return jsonError(error);
  }
}
