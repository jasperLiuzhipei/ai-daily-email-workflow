import {NextRequest, NextResponse} from "next/server";
import {isAdminRequest} from "@/lib/admin";
import {issueSummary} from "@/lib/feedback";
import {jsonError} from "@/lib/http";

export const dynamic = "force-dynamic";

export async function GET(
  request: NextRequest,
  {params}: {params: Promise<{issueId: string}>},
) {
  if (!isAdminRequest(request)) {
    return NextResponse.json({error: "Unauthorized."}, {status: 401});
  }
  try {
    const {issueId} = await params;
    return NextResponse.json(await issueSummary(issueId));
  } catch (error) {
    return jsonError(error);
  }
}
