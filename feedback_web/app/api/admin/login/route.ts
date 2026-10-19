import {NextRequest, NextResponse} from "next/server";
import {
  ADMIN_COOKIE,
  adminSessionCookie,
  validAdminPassword,
} from "@/lib/admin";

export async function POST(request: NextRequest) {
  const data = await request.formData();
  const password = String(data.get("password") ?? "");
  if (!validAdminPassword(password)) {
    return NextResponse.redirect(new URL("/admin/login?error=1", request.url), 303);
  }
  const response = NextResponse.redirect(new URL("/admin", request.url), 303);
  response.cookies.set(ADMIN_COOKIE, adminSessionCookie(), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    path: "/",
    maxAge: 8 * 60 * 60,
  });
  return response;
}
