import Link from "next/link";
import {cookies} from "next/headers";
import {redirect} from "next/navigation";
import {ADMIN_COOKIE, validAdminSession} from "@/lib/admin";
import {listIssueSummaries} from "@/lib/feedback";
import {formatShanghai, percent} from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function AdminPage() {
  const cookieStore = await cookies();
  if (!validAdminSession(cookieStore.get(ADMIN_COOKIE)?.value ?? "")) {
    redirect("/admin/login");
  }
  const issues = await listIssueSummaries();
  return (
    <main className="admin-shell">
      <header className="admin-header">
        <div>
          <div className="eyebrow">AI Signal Daily</div>
          <h1>反馈与阅读统计</h1>
          <p>打开率为图片加载估算值；有效点击会过滤明显预取与安全扫描请求。</p>
        </div>
        <form action="/api/admin/logout" method="post">
          <button className="primary" type="submit">
            退出
          </button>
        </form>
      </header>
      <section className="admin-card">
        <table className="admin-table">
          <thead>
            <tr>
              <th>期次</th>
              <th>发送时间</th>
              <th>人数</th>
              <th>估算打开</th>
              <th>有效点击</th>
              <th>评分</th>
              <th>均分</th>
              <th>建议</th>
            </tr>
          </thead>
          <tbody>
            {issues.length ? (
              issues.map((issue) => (
                <tr key={issue.issue_id}>
                  <td>
                    <Link href={`/admin/issue/${encodeURIComponent(issue.issue_id)}`}>
                      {issue.issue_id}
                    </Link>
                  </td>
                  <td>{formatShanghai(issue.sent_at)}</td>
                  <td>{issue.recipient_count || "-"}</td>
                  <td>
                    {issue.estimated_unique_opens} / {percent(issue.estimated_open_rate)}
                  </td>
                  <td>
                    {issue.effective_unique_clicks} / {percent(issue.effective_click_rate)}
                  </td>
                  <td>
                    {issue.rating_count} / {percent(issue.rating_rate)}
                  </td>
                  <td>{issue.average_score ?? "-"}</td>
                  <td>{issue.comment_count}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={8}>还没有统计数据。</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
    </main>
  );
}
