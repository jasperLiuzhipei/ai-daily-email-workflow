import {NextResponse} from "next/server";

export function GET() {
  return NextResponse.json({
    service: "ai-daily-feedback",
    version: 2,
    storage: "supabase-postgres",
    routes: {
      health: "/healthz",
      rating_page: "/f/<signed-token>#score=<1-5>",
      comment_page: "/comment/<signed-token>",
      open_pixel: "/open/<signed-token>.gif",
      click_redirect: "/click/<signed-token>/<link-id>?u=<destination>&t=<title>&s=<signature>",
      admin: "/admin",
      rating_api: "/api/rating",
      comment_api: "/api/comment",
      delivery_api: "/api/delivery",
      issues_api: "/api/issues",
      summary_api: "/api/summary/<issue-id>",
      comments_api: "/api/comments/<issue-id>",
    },
  });
}
