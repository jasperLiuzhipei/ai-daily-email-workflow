import {NextResponse} from "next/server";
import {
  classifyClickRequest,
  normalizeClickTitle,
  validateClickDestination,
  validateLinkId,
  verifyClickSignature,
} from "@/lib/click";
import {recordClick} from "@/lib/feedback";
import {verifyFeedbackToken} from "@/lib/token";

export const dynamic = "force-dynamic";

export async function GET(
  request: Request,
  {params}: {params: Promise<{token: string; linkId: string}>},
) {
  const {token, linkId: rawLinkId} = await params;
  const url = new URL(request.url);
  try {
    const linkId = validateLinkId(rawLinkId);
    const destinationUrl = validateClickDestination(url.searchParams.get("u"));
    const linkTitle = normalizeClickTitle(url.searchParams.get("t"));
    verifyClickSignature(token, linkId, destinationUrl, linkTitle, url.searchParams.get("s"));
    const identity = verifyFeedbackToken(token);
    const classification = classifyClickRequest(request);
    try {
      await recordClick(identity, {
        linkId,
        linkTitle,
        destinationUrl,
        qualified: classification.qualified,
      });
    } catch (error) {
      // Analytics must never make an editorial source link unavailable.
      console.warn("Click event was not recorded.", error);
    }
    return NextResponse.redirect(destinationUrl, {
      status: 302,
      headers: {
        "Cache-Control": "no-store, max-age=0",
        "Referrer-Policy": "no-referrer",
      },
    });
  } catch (error) {
    console.warn("Click redirect was rejected.", error);
    return new NextResponse("Invalid click link.", {
      status: 400,
      headers: {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"},
    });
  }
}
