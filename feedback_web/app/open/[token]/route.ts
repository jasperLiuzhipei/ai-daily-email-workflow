import {recordOpen} from "@/lib/feedback";
import {verifyFeedbackToken} from "@/lib/token";

export const dynamic = "force-dynamic";

const TRANSPARENT_GIF = Buffer.from(
  "R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==",
  "base64",
);

export async function GET(
  _request: Request,
  {params}: {params: Promise<{token: string}>},
) {
  const {token: rawToken} = await params;
  const token = rawToken.endsWith(".gif") ? rawToken.slice(0, -4) : rawToken;
  try {
    await recordOpen(verifyFeedbackToken(token));
  } catch (error) {
    console.warn("Open event was not recorded.", error);
  }
  return new Response(TRANSPARENT_GIF, {
    headers: {
      "Content-Type": "image/gif",
      "Content-Length": String(TRANSPARENT_GIF.length),
      "Cache-Control": "no-store, max-age=0",
    },
  });
}
