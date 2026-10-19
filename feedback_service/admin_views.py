from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from feedback_service.routes import ADMIN_ISSUE_PREFIX, ADMIN_PATH


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _local_time(value: object, *, with_date: bool = True) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=SHANGHAI)
        parsed = parsed.astimezone(SHANGHAI)
    except ValueError:
        return escape(text)
    return parsed.strftime("%Y.%m.%d %H:%M" if with_date else "%H:%M")


def _number(value: object) -> str:
    try:
        return f"{int(value or 0):,}"
    except (TypeError, ValueError):
        return "0"


def _rate(value: object) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "-"


def _average(value: object) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "-"


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>{escape(title)}</title>
  <style>
    * {{ box-sizing:border-box; }}
    html,body {{ margin:0;min-height:100%;background:#f4f7f9; }}
    body {{ color:#243e50;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",Arial,sans-serif;-webkit-font-smoothing:antialiased; }}
    a {{ color:inherit; }}
    .shell {{ width:min(1120px,calc(100% - 36px));margin:0 auto;padding:38px 0 70px; }}
    .topline {{ display:flex;align-items:flex-end;justify-content:space-between;gap:24px;padding:0 2px 22px;border-bottom:2px solid #2f82a8; }}
    .eyebrow {{ color:#2f82a8;font:600 11px/1.4 Arial,sans-serif;letter-spacing:1.8px;text-transform:uppercase; }}
    h1 {{ margin:7px 0 0;font-size:25px;line-height:1.35;font-weight:600;letter-spacing:0; }}
    .muted {{ color:#7a8d9a;font-size:12px;line-height:1.7; }}
    .back {{ color:#607b8c;font-size:13px;text-decoration:none; }}
    .back:hover {{ color:#2f82a8; }}
    .metrics {{ display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:0;margin:28px 0 32px;background:#fff;border:1px solid #d9e3e9;border-radius:7px;overflow:hidden; }}
    .metric {{ min-height:92px;padding:19px 22px;border-right:1px solid #e5ecef; }}
    .metric:last-child {{ border-right:0; }}
    .metric-label {{ color:#8395a2;font-size:11px;line-height:1.4; }}
    .metric-value {{ margin-top:7px;color:#243e50;font:600 23px/1.2 Arial,"PingFang SC",sans-serif; }}
    .panel {{ background:#fff;border:1px solid #d9e3e9;border-radius:7px;overflow:hidden; }}
    .panel-head {{ display:flex;align-items:center;justify-content:space-between;gap:16px;padding:18px 22px;border-bottom:1px solid #e7edf0; }}
    .panel-title {{ margin:0;font-size:15px;line-height:1.4;font-weight:600; }}
    table {{ width:100%;border-collapse:collapse;table-layout:fixed; }}
    th {{ padding:11px 16px;color:#8294a0;background:#f8fafb;font-size:11px;line-height:1.4;font-weight:500;text-align:left;white-space:nowrap; }}
    td {{ padding:16px;color:#526b7c;font-size:12px;line-height:1.55;border-top:1px solid #edf1f3;vertical-align:middle; }}
    tbody tr:first-child td {{ border-top:0; }}
    tbody tr:hover td {{ background:#fbfdfe; }}
    .issue-link {{ color:#243e50;font-weight:600;text-decoration:none; }}
    .issue-link:hover {{ color:#2f82a8; }}
    .issue-meta {{ display:block;margin-top:3px;color:#91a1ab;font-size:10px;font-weight:400; }}
    .link-title {{ display:block;color:#294f66;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap; }}
    .link-url {{ display:block;margin-top:3px;color:#91a1ab;font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap; }}
    .strong {{ color:#294f66;font-weight:600; }}
    .empty {{ padding:54px 24px;color:#8a9aa5;font-size:13px;text-align:center; }}
    .detail-grid {{ display:grid;grid-template-columns:minmax(0,1.1fr) minmax(320px,.9fr);gap:22px;margin-top:24px; }}
    .section {{ padding:22px; }}
    .distribution {{ margin-top:18px; }}
    .dist-row {{ display:grid;grid-template-columns:42px minmax(0,1fr) 44px;align-items:center;gap:12px;margin-top:11px;color:#657b89;font-size:12px; }}
    .bar {{ height:7px;background:#edf2f5;border-radius:10px;overflow:hidden; }}
    .bar > span {{ display:block;height:100%;background:#2f82a8;border-radius:10px; }}
    .comments {{ padding:0 22px; }}
    .comment {{ padding:17px 0;border-top:1px solid #edf1f3; }}
    .comment:first-child {{ border-top:0; }}
    .comment-text {{ color:#3d596b;font-size:13px;line-height:1.75;white-space:pre-wrap;word-break:break-word; }}
    .comment-meta {{ margin-top:7px;color:#92a1aa;font-size:10px;line-height:1.5; }}
    .note {{ margin-top:22px;color:#8a9aa5;font-size:11px;line-height:1.7; }}
    @media (max-width:760px) {{
      .shell {{ width:min(100% - 24px,1120px);padding-top:24px; }}
      .topline {{ align-items:flex-start;flex-direction:column; }}
      .metrics {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
      .metric:nth-child(2) {{ border-right:0; }}
      .metric:nth-child(-n+2) {{ border-bottom:1px solid #e5ecef; }}
      .detail-grid {{ grid-template-columns:1fr; }}
      .table-wrap {{ overflow-x:auto; }}
      table {{ min-width:790px; }}
    }}
  </style>
</head>
<body><main class="shell">{body}</main></body>
</html>"""


def dashboard_page(items: list[dict[str, Any]]) -> str:
    sent_total = sum(int(item.get("recipient_count") or 0) for item in items)
    open_total = sum(int(item.get("estimated_unique_opens") or 0) for item in items)
    effective_click_total = sum(int(item.get("effective_unique_clicks") or 0) for item in items)
    rating_total = sum(int(item.get("rating_count") or 0) for item in items)
    comment_total = sum(int(item.get("comment_count") or 0) for item in items)
    latest = str(items[0].get("issue_id") or "-") if items else "-"
    rows: list[str] = []
    for item in items:
        issue_id = escape(str(item.get("issue_id") or ""))
        issue_number = item.get("issue_number")
        issue_label = f"第 {int(issue_number)} 期" if isinstance(issue_number, int) else "未编号"
        rows.append(
            f"""<tr>
              <td><a class="issue-link" href="{ADMIN_ISSUE_PREFIX}/{quote(issue_id, safe='')}">{issue_id}<span class="issue-meta">{escape(issue_label)} · {_local_time(item.get('sent_at'), with_date=False)}</span></a></td>
              <td>{_number(item.get('recipient_count'))}</td>
              <td><span class="strong">{_number(item.get('estimated_unique_opens'))}</span> / {_rate(item.get('estimated_open_rate'))}</td>
              <td><span class="strong">{_number(item.get('effective_unique_clicks'))}</span> / {_rate(item.get('effective_click_rate'))}</td>
              <td><span class="strong">{_number(item.get('rating_count'))}</span> / {_rate(item.get('rating_rate'))}</td>
              <td>{_average(item.get('average_score'))}</td>
              <td><span class="strong">{_number(item.get('comment_count'))}</span> / {_rate(item.get('comment_rate'))}</td>
            </tr>"""
        )
    table_body = "".join(rows) if rows else '<tr><td class="empty" colspan="7">暂无期次数据。同步发送台账或收到反馈后会自动出现。</td></tr>'
    body = f"""
    <header class="topline">
      <div><div class="eyebrow">AI Signal Daily</div><h1>日报数据后台</h1></div>
      <div class="muted">最近期次：{escape(latest)}<br>时间统一显示为北京时间</div>
    </header>
    <section class="metrics" aria-label="总体统计">
      <div class="metric"><div class="metric-label">已记录期数</div><div class="metric-value">{_number(len(items))}</div></div>
      <div class="metric"><div class="metric-label">累计发送</div><div class="metric-value">{_number(sent_total)}</div></div>
      <div class="metric"><div class="metric-label">估算独立打开</div><div class="metric-value">{_number(open_total)}</div></div>
      <div class="metric"><div class="metric-label">有效点击</div><div class="metric-value">{_number(effective_click_total)}</div></div>
    </section>
    <section class="panel">
      <div class="panel-head"><h2 class="panel-title">每日表现</h2><span class="muted">按日期倒序</span></div>
      <div class="table-wrap"><table>
        <thead><tr><th style="width:23%">日期</th><th>发送</th><th>打开人数 / 率</th><th>有效点击人数 / 率</th><th>评分人数 / 率</th><th>平均分</th><th>意见人数 / 率</th></tr></thead>
        <tbody>{table_body}</tbody>
      </table></div>
    </section>
    <p class="note">打开率依赖邮件客户端加载透明图片，因此是估算值；有效点击会过滤明显预取和安全扫描请求。评分与意见按“同一期、同一匿名收件人”去重。</p>
    """
    return _page("AI 日报数据后台", body)


def issue_page(
    summary: dict[str, Any],
    comments: list[dict[str, str]],
    click_links: list[dict[str, object]] | None = None,
) -> str:
    issue_id = escape(str(summary.get("issue_id") or ""))
    rating_count = int(summary.get("rating_count") or 0)
    distribution = summary.get("distribution")
    distribution = distribution if isinstance(distribution, dict) else {}
    bars: list[str] = []
    for score in range(5, 0, -1):
        count = int(distribution.get(str(score)) or 0)
        width = round(count * 100 / rating_count, 1) if rating_count else 0
        bars.append(
            f'<div class="dist-row"><span>{score} 星</span><div class="bar"><span style="width:{width}%"></span></div><span>{count}</span></div>'
        )
    comment_rows = "".join(
        f"""<article class="comment">
          <div class="comment-text">{escape(str(item.get('comment') or ''))}</div>
          <div class="comment-meta">更新于 {_local_time(item.get('updated_at'))} · 匿名 ID {escape(str(item.get('recipient_id') or '')[:8])}</div>
        </article>"""
        for item in comments
    ) or '<div class="empty">本期暂无文本意见。</div>'
    issue_number = summary.get("issue_number")
    issue_note = f"第 {int(issue_number)} 期" if isinstance(issue_number, int) else "未编号"
    click_rows = "".join(
        f"""<tr>
          <td><span class="link-title">{escape(str(item.get('link_title') or item.get('link_id') or '未命名链接'))}</span><span class="link-url">{escape(str(item.get('destination_url') or ''))}</span></td>
          <td><span class="strong">{_number(item.get('effective_unique_clicks'))}</span> / {_rate(item.get('effective_click_rate'))}</td>
          <td>{_number(item.get('effective_total_clicks'))}</td>
          <td>{_number(item.get('filtered_click_requests'))}</td>
        </tr>"""
        for item in (click_links or [])
    ) or '<tr><td class="empty" colspan="4">本期还没有来源链接点击记录。</td></tr>'
    body = f"""
    <header class="topline">
      <div><div class="eyebrow">Issue Detail</div><h1>{issue_id} · {escape(issue_note)}</h1></div>
      <a class="back" href="{ADMIN_PATH}">返回全部期次</a>
    </header>
    <section class="metrics" aria-label="本期统计">
      <div class="metric"><div class="metric-label">发送人数</div><div class="metric-value">{_number(summary.get('recipient_count'))}</div></div>
      <div class="metric"><div class="metric-label">估算打开率</div><div class="metric-value">{_rate(summary.get('estimated_open_rate'))}</div></div>
      <div class="metric"><div class="metric-label">评分率 / 平均分</div><div class="metric-value">{_rate(summary.get('rating_rate'))} / {_average(summary.get('average_score'))}</div></div>
      <div class="metric"><div class="metric-label">文本意见</div><div class="metric-value">{_number(summary.get('comment_count'))}</div></div>
      <div class="metric"><div class="metric-label">有效点击率</div><div class="metric-value">{_rate(summary.get('effective_click_rate'))}</div></div>
    </section>
    <div class="detail-grid">
      <section class="panel section">
        <h2 class="panel-title">星级分布</h2>
        <div class="distribution">{''.join(bars)}</div>
        <p class="note">共 {_number(rating_count)} 人评分；重复点击只更新最终星级，不重复计人数。</p>
      </section>
      <section class="panel section">
        <h2 class="panel-title">打开情况</h2>
        <div class="distribution">
          <div class="dist-row"><span>独立</span><div class="bar"><span style="width:{min(float(summary.get('estimated_open_rate') or 0), 100)}%"></span></div><span>{_number(summary.get('estimated_unique_opens'))}</span></div>
          <div class="dist-row"><span>总计</span><div class="bar"><span style="width:{min(float(summary.get('estimated_open_rate') or 0), 100)}%"></span></div><span>{_number(summary.get('estimated_total_opens'))}</span></div>
        </div>
        <p class="note">首次打开：{_local_time(summary.get('first_opened_at'))}<br>最近打开：{_local_time(summary.get('last_opened_at'))}</p>
      </section>
    </div>
    <section class="panel" style="margin-top:22px">
      <div class="panel-head"><h2 class="panel-title">文章链接点击</h2><span class="muted">仅计入通过预取与安全扫描过滤后的真实跳转</span></div>
      <div class="table-wrap"><table>
        <thead><tr><th style="width:52%">文章 / 来源链接</th><th>独立点击 / 率</th><th>有效点击总数</th><th>已过滤请求</th></tr></thead>
        <tbody>{click_rows}</tbody>
      </table></div>
    </section>
    <section class="panel" style="margin-top:22px">
      <div class="panel-head"><h2 class="panel-title">文本意见</h2><span class="muted">按更新时间倒序</span></div>
      <div class="comments">{comment_rows}</div>
    </section>
    <p class="note">收件人只以按期生成的匿名标识保存，后台不会展示邮箱地址。</p>
    """
    return _page(f"{issue_id} · AI 日报数据", body)
