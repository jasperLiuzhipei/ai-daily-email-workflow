import Link from "next/link";
import {cookies} from "next/headers";
import {redirect} from "next/navigation";
import {ADMIN_COOKIE, validAdminSession} from "@/lib/admin";
import {issueSummary, listClickLinkSummaries, listComments} from "@/lib/feedback";
import {formatShanghai, percent} from "@/lib/format";

export const dynamic = "force-dynamic";

function displaySource(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export default async function AdminIssuePage({
  params,
}: {
  params: Promise<{issueId: string}>;
}) {
  const cookieStore = await cookies();
  if (!validAdminSession(cookieStore.get(ADMIN_COOKIE)?.value ?? "")) {
    redirect("/admin/login");
  }
  const {issueId} = await params;
  const [summary, comments, links] = await Promise.all([
    issueSummary(issueId),
    listComments(issueId),
    listClickLinkSummaries(issueId),
  ]);
  return (
    <main className="admin-shell">
      <header className="admin-header">
        <div>
          <div className="eyebrow">Issue detail</div>
          <h1>{summary.issue_id}</h1>
          <p>
            {formatShanghai(summary.sent_at)} · {summary.recipient_group || "未标注名单组"}
          </p>
        </div>
        <Link href="/admin">返回全部期次</Link>
      </header>
      <section className="metric-grid">
        <div className="metric">
          <strong>{summary.recipient_count || "-"}</strong>
          <span>正式发送人数</span>
        </div>
        <div className="metric">
          <strong>{percent(summary.estimated_open_rate)}</strong>
          <span>估算打开率 · {summary.estimated_unique_opens} 人</span>
        </div>
        <div className="metric">
          <strong>{summary.average_score ?? "-"}</strong>
          <span>平均评分 · {summary.rating_count} 人</span>
        </div>
        <div className="metric">
          <strong>{summary.comment_count}</strong>
          <span>文字建议</span>
        </div>
        <div className="metric">
          <strong>{percent(summary.effective_click_rate)}</strong>
          <span>
            有效点击 · {summary.effective_unique_clicks} 人 / {summary.effective_total_clicks} 次
          </span>
        </div>
      </section>
      <section className="admin-card">
        <div className="admin-card-heading">
          <div>
            <h2>单篇有效点击</h2>
            <p>已过滤 {summary.filtered_click_requests} 次明显预取或安全扫描请求。</p>
          </div>
        </div>
        {links.length ? (
          <table className="admin-table link-table">
            <thead>
              <tr>
                <th>内容</th>
                <th>有效点击人数 / 率</th>
                <th>有效点击次数</th>
                <th>过滤请求</th>
              </tr>
            </thead>
            <tbody>
              {links.map((link) => (
                <tr key={link.link_id}>
                  <td>
                    <a href={link.destination_url} target="_blank" rel="noreferrer">
                      {link.link_title || displaySource(link.destination_url)}
                    </a>
                    <span className="link-host">{displaySource(link.destination_url)}</span>
                  </td>
                  <td>
                    {link.effective_unique_clicks} / {percent(link.effective_click_rate)}
                  </td>
                  <td>{link.effective_total_clicks}</td>
                  <td>{link.filtered_click_requests}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="empty-state">本期还没有文章点击。</div>
        )}
      </section>
      <section className="admin-card">
        <div className="admin-card-heading">
          <div>
            <h2>文字建议</h2>
          </div>
        </div>
        {comments.length ? (
          <ul className="comment-list">
            {comments.map((comment, index) => (
              <li key={`${comment.updated_at}-${index}`}>
                <p>{comment.comment_text}</p>
                <time>{formatShanghai(comment.updated_at)}</time>
              </li>
            ))}
          </ul>
        ) : (
          <ul className="comment-list">
            <li>
              <p>本期还没有文字建议。</p>
            </li>
          </ul>
        )}
      </section>
    </main>
  );
}
