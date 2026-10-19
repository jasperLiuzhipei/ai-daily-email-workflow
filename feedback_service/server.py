from __future__ import annotations

import argparse
import base64
import hmac
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from feedback_service.admin_views import dashboard_page, issue_page
from feedback_service.core import (
    FeedbackError,
    FeedbackStore,
    verify_click_signature,
    verify_feedback_token,
)
from feedback_service.routes import (
    ADMIN_ISSUE_PREFIX,
    ADMIN_PATH,
    API_COMMENTS_PREFIX,
    API_COMMENT_PATH,
    API_DELIVERY_PATH,
    API_ISSUES_PATH,
    API_RATING_PATH,
    API_SUMMARY_PREFIX,
    COMMENT_PAGE_PREFIX,
    CLICK_PREFIX,
    HEALTH_PATH,
    METADATA_PATH,
    OPEN_PIXEL_PREFIX,
    RATING_PAGE_PREFIX,
)


TRANSPARENT_GIF = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEND_LEDGER = PROJECT_ROOT / "daily_runs" / "send_ledger.json"

AUTOMATION_USER_AGENT_TOKENS = (
    "bot",
    "crawler",
    "spider",
    "preview",
    "scanner",
    "safelinks",
    "proofpoint",
    "mimecast",
    "barracuda",
    "symantec",
    "trend micro",
    "fortinet",
    "urlscan",
    "security",
    "googleimageproxy",
    "curl",
    "wget",
    "python-requests",
    "headless",
)


def page_shell(title: str, body: str, script: str = "") -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>{title}</title>
  <style>
    * {{ box-sizing:border-box; }}
    html,body {{ margin:0;min-height:100%;background:#f4f7f9; }}
    body {{ color:#294354;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",Arial,sans-serif;-webkit-font-smoothing:antialiased; }}
    main {{ width:min(520px,calc(100% - 32px));margin:9vh auto 40px;padding:34px 30px;background:#fff;border-radius:8px;box-shadow:0 8px 28px rgba(29,54,69,.08); }}
    .eyebrow {{ color:#2f82a8;font:600 11px/1.4 Arial,sans-serif;letter-spacing:1.6px;text-transform:uppercase; }}
    h1 {{ margin:10px 0 8px;font-size:21px;line-height:1.45;font-weight:600;letter-spacing:0; }}
    p {{ margin:0;color:#718594;font-size:13px;line-height:1.7; }}
    .stars {{ display:flex;gap:7px;margin:24px 0 18px; }}
    .star {{ appearance:none;border:0;background:none;padding:0;color:#93a8b5;font:400 29px/1 Arial,sans-serif;cursor:pointer;text-decoration:none; }}
    .star.active {{ color:#2f82a8; }}
    .status {{ min-height:24px;color:#607788;font-size:13px;line-height:24px; }}
    .status.ok {{ color:#2f82a8; }}
    .status.error {{ color:#a8443a; }}
    textarea {{ width:100%;min-height:128px;margin:22px 0 12px;padding:13px 14px;resize:vertical;border:1px solid #cfdae1;border-radius:6px;color:#294354;background:#fff;font:400 14px/1.7 inherit;outline:none; }}
    textarea:focus {{ border-color:#2f82a8;box-shadow:0 0 0 3px rgba(47,130,168,.09); }}
    .actions {{ display:flex;align-items:center;gap:12px; }}
    button.primary {{ border:0;border-radius:5px;padding:9px 17px;color:#fff;background:#2f82a8;font:500 13px/1.4 inherit;cursor:pointer; }}
    button.primary[disabled] {{ opacity:.55;cursor:default; }}
    .count {{ margin-left:auto;color:#93a3ae;font-size:11px; }}
    @media (max-width:520px) {{ main {{ margin-top:22px;padding:28px 22px; }} }}
  </style>
</head>
<body>
  <main>{body}</main>
  <script>{script}</script>
</body>
</html>"""


def rating_page(token: str) -> str:
    token_json = json.dumps(token)
    rating_api_json = json.dumps(API_RATING_PATH)
    stars = "".join(
        f'<button class="star" type="button" data-score="{score}" aria-label="{score} 星">☆</button>'
        for score in range(1, 6)
    )
    body = f"""
    <div class="eyebrow">AI Signal Daily</div>
    <h1 id="title">正在记录评分</h1>
    <p>点击其他星级可以修改，本期始终只保留一条最终评分。</p>
    <div class="stars" aria-label="修改评分">{stars}</div>
    <div id="status" class="status">请稍候...</div>
    """
    script = f"""
    const token = {token_json};
    const ratingApi = {rating_api_json};
    const statusNode = document.getElementById('status');
    const titleNode = document.getElementById('title');
    const starNodes = Array.from(document.querySelectorAll('.star'));
    let pendingPayload = null;

    function eventId() {{
      if (crypto.randomUUID) return crypto.randomUUID();
      return Date.now().toString(36) + Math.random().toString(36).slice(2);
    }}

    function scoreFromHash() {{
      const value = new URLSearchParams(location.hash.slice(1)).get('score');
      const score = Number(value);
      return Number.isInteger(score) && score >= 1 && score <= 5 ? score : null;
    }}

    function paint(score) {{
      starNodes.forEach((node) => {{
        const active = Number(node.dataset.score) <= score;
        node.classList.toggle('active', active);
        node.textContent = active ? '★' : '☆';
      }});
    }}

    async function submitScore(score) {{
      paint(score);
      titleNode.textContent = `正在记录 ${{score}} 星`;
      statusNode.className = 'status';
      statusNode.textContent = '请稍候...';
      pendingPayload = {{ token, score, event_id: eventId() }};
      try {{
        const response = await fetch(ratingApi, {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(pendingPayload),
          keepalive: true,
          credentials: 'same-origin'
        }});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || '提交失败');
        pendingPayload = null;
        titleNode.textContent = `已记录 ${{result.score}} 星`;
        statusNode.className = 'status ok';
        statusNode.textContent = '已经保存，正在尝试关闭页面...';
        paint(result.score);
        setTimeout(() => {{
          window.close();
          setTimeout(() => {{
            statusNode.textContent = '已经保存，可以关闭页面。';
          }}, 350);
        }}, 900);
      }} catch (error) {{
        titleNode.textContent = '评分暂未保存';
        statusNode.className = 'status error';
        statusNode.textContent = '网络异常，请点击星级重试。';
      }}
    }}

    starNodes.forEach((node) => node.addEventListener('click', () => {{
      const score = Number(node.dataset.score);
      history.replaceState(null, '', `#score=${{score}}`);
      submitScore(score);
    }}));

    addEventListener('pagehide', () => {{
      if (!pendingPayload || !navigator.sendBeacon) return;
      navigator.sendBeacon(ratingApi, new Blob(
        [JSON.stringify(pendingPayload)],
        {{ type: 'application/json' }}
      ));
    }});

    const initialScore = scoreFromHash();
    if (initialScore) {{
      paint(initialScore);
      setTimeout(() => submitScore(initialScore), 250);
    }} else {{
      titleNode.textContent = '选择一个星级';
      statusNode.textContent = '';
    }}
    """
    return page_shell("AI 日报评分", body, script)


def comment_page(token: str) -> str:
    token_json = json.dumps(token)
    comment_api_json = json.dumps(API_COMMENT_PATH)
    body = """
    <div class="eyebrow">AI Signal Daily</div>
    <h1>写一句建议</h1>
    <p>文本反馈完全可选，只用于改进选源、解读和展示方式。</p>
    <textarea id="comment" maxlength="300" placeholder="哪一条最有用，或者哪里可以做得更好？"></textarea>
    <div class="actions">
      <button id="submit" class="primary" type="button">提交建议</button>
      <span id="status" class="status"></span>
      <span id="count" class="count">0 / 300</span>
    </div>
    """
    script = f"""
    const token = {token_json};
    const commentApi = {comment_api_json};
    const input = document.getElementById('comment');
    const button = document.getElementById('submit');
    const statusNode = document.getElementById('status');
    const countNode = document.getElementById('count');

    function eventId() {{
      if (crypto.randomUUID) return crypto.randomUUID();
      return Date.now().toString(36) + Math.random().toString(36).slice(2);
    }}
    input.addEventListener('input', () => countNode.textContent = `${{input.value.length}} / 300`);
    button.addEventListener('click', async () => {{
      const comment = input.value.trim();
      if (!comment) {{
        statusNode.className = 'status error';
        statusNode.textContent = '请先写一点内容。';
        return;
      }}
      button.disabled = true;
      statusNode.className = 'status';
      statusNode.textContent = '提交中...';
      try {{
        const response = await fetch(commentApi, {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ token, comment, event_id: eventId() }}),
          credentials: 'same-origin'
        }});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || '提交失败');
        statusNode.className = 'status ok';
        statusNode.textContent = '建议已收到，正在尝试关闭页面...';
        setTimeout(() => {{
          window.close();
          setTimeout(() => {{
            statusNode.textContent = '建议已收到，可以关闭页面。';
          }}, 350);
        }}, 900);
      }} catch (error) {{
        statusNode.className = 'status error';
        statusNode.textContent = '提交失败，请重试。';
      }} finally {{
        button.disabled = false;
      }}
    }});
    """
    return page_shell("AI 日报建议", body, script)


class FeedbackApplication:
    def __init__(
        self,
        database: Path,
        secret: str,
        admin_key: str | None = None,
        send_ledger: Path | None = None,
    ):
        if len(secret) < 16:
            raise FeedbackError("AI_DAILY_FEEDBACK_SECRET must be at least 16 characters.")
        self.secret = secret
        self.admin_key = admin_key
        self.send_ledger = send_ledger
        self.store = FeedbackStore(database)
        self.refresh_deliveries()

    def identity(self, token: str):
        return verify_feedback_token(token, self.secret)

    def refresh_deliveries(self) -> int:
        if self.send_ledger is None:
            return 0
        return self.store.sync_send_ledger(self.send_ledger)


class FeedbackHandler(BaseHTTPRequestHandler):
    server_version = "AIDailyFeedback/0.1"

    @property
    def app(self) -> FeedbackApplication:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, format: str, *args) -> None:
        print(f"feedback {self.address_string()} {format % args}")

    def common_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'",
        )

    def send_bytes(self, status: int, content_type: str, payload: bytes) -> None:
        self.send_response(status)
        self.common_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_html(self, status: int, html: str) -> None:
        self.send_bytes(status, "text/html; charset=utf-8", html.encode("utf-8"))

    def send_json(self, status: int, value: dict[str, object]) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_bytes(status, "application/json; charset=utf-8", payload)

    def send_redirect(self, destination_url: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.common_headers()
        self.send_header("Location", destination_url)
        self.end_headers()

    def is_qualified_click(self) -> bool:
        purpose = " ".join(
            value
            for value in (
                self.headers.get("Purpose", ""),
                self.headers.get("Sec-Purpose", ""),
                self.headers.get("X-Purpose", ""),
            )
            if value
        ).lower()
        if "prefetch" in purpose or "preview" in purpose:
            return False
        if any(
            self.headers.get(name)
            for name in (
                "X-Proofpoint-Orig-Guid",
                "X-Proofpoint-Vulnerability",
                "X-Safelinks-Url",
                "X-Mimecast-Spam-Score",
            )
        ):
            return False
        user_agent = self.headers.get("User-Agent", "").strip().lower()
        return bool(user_agent) and not any(token in user_agent for token in AUTOMATION_USER_AGENT_TOKENS)

    def admin_key_from_request(self) -> str:
        supplied = self.headers.get("X-Admin-Key", "")
        if supplied:
            return supplied
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Basic "):
            return ""
        try:
            decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return ""
        _, separator, password = decoded.partition(":")
        return password if separator else ""

    def require_admin(self, *, api: bool = False) -> bool:
        if not self.app.admin_key:
            self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Admin dashboard is disabled."})
            return False
        if hmac.compare_digest(self.admin_key_from_request(), self.app.admin_key):
            return True
        if api:
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "Unauthorized."})
            return False
        body = b"Admin authentication required."
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.common_headers()
        self.send_header("WWW-Authenticate", 'Basic realm="AI Daily Admin", charset="UTF-8"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

    def read_json(self) -> dict[str, object]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise FeedbackError("Invalid request length.") from exc
        if length <= 0 or length > 8192:
            raise FeedbackError("Invalid request body.")
        try:
            value = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            raise FeedbackError("Invalid JSON body.") from exc
        if not isinstance(value, dict):
            raise FeedbackError("JSON body must be an object.")
        return value

    def error_page(self, message: str) -> str:
        body = f"""
        <div class="eyebrow">AI Signal Daily</div>
        <h1>链接暂时不可用</h1>
        <p>{message}</p>
        """
        return page_shell("反馈链接不可用", body)

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            if path == HEALTH_PATH:
                self.send_json(HTTPStatus.OK, {"ok": True})
                return
            if path == METADATA_PATH:
                self.send_json(
                    HTTPStatus.OK,
                    {
                        "service": "ai-daily-feedback",
                        "version": 1,
                        "routes": {
                            "health": HEALTH_PATH,
                            "rating_page": f"{RATING_PAGE_PREFIX}/<signed-token>#score=<1-5>",
                            "comment_page": f"{COMMENT_PAGE_PREFIX}/<signed-token>",
                            "open_pixel": f"{OPEN_PIXEL_PREFIX}/<signed-token>.gif",
                            "click_redirect": f"{CLICK_PREFIX}/<signed-token>/<link-id>?u=<destination>&t=<title>&s=<signature>",
                            "admin": ADMIN_PATH,
                            "rating_api": API_RATING_PATH,
                            "comment_api": API_COMMENT_PATH,
                            "delivery_api": API_DELIVERY_PATH,
                            "issues_api": API_ISSUES_PATH,
                            "summary_api": f"{API_SUMMARY_PREFIX}/<issue-id>",
                            "comments_api": f"{API_COMMENTS_PREFIX}/<issue-id>",
                        },
                    },
                )
                return
            if path in {ADMIN_PATH, f"{ADMIN_PATH}/"}:
                if not self.require_admin():
                    return
                self.app.refresh_deliveries()
                self.send_html(HTTPStatus.OK, dashboard_page(self.app.store.list_issue_summaries()))
                return
            if path.startswith(f"{ADMIN_ISSUE_PREFIX}/"):
                if not self.require_admin():
                    return
                self.app.refresh_deliveries()
                issue_id = path.removeprefix(f"{ADMIN_ISSUE_PREFIX}/")
                self.send_html(
                    HTTPStatus.OK,
                    issue_page(
                        self.app.store.summary(issue_id),
                        self.app.store.list_comments(issue_id),
                        self.app.store.list_click_links(issue_id),
                    ),
                )
                return
            if path.startswith(f"{RATING_PAGE_PREFIX}/"):
                token = path.removeprefix(f"{RATING_PAGE_PREFIX}/")
                self.app.identity(token)
                self.send_html(HTTPStatus.OK, rating_page(token))
                return
            if path.startswith(f"{COMMENT_PAGE_PREFIX}/"):
                token = path.removeprefix(f"{COMMENT_PAGE_PREFIX}/")
                self.app.identity(token)
                self.send_html(HTTPStatus.OK, comment_page(token))
                return
            if path.startswith(f"{OPEN_PIXEL_PREFIX}/") and path.endswith(".gif"):
                token = path.removeprefix(f"{OPEN_PIXEL_PREFIX}/")[:-4]
                identity = self.app.identity(token)
                self.app.store.record_open(identity)
                self.send_bytes(HTTPStatus.OK, "image/gif", TRANSPARENT_GIF)
                return
            if path.startswith(f"{CLICK_PREFIX}/"):
                remainder = path.removeprefix(f"{CLICK_PREFIX}/")
                token, separator, link_id = remainder.rpartition("/")
                if not separator or not token or not link_id:
                    raise FeedbackError("Invalid click link.")
                query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
                destination_url = str(query.get("u", [""])[0])
                link_title = str(query.get("t", [""])[0])
                signature = str(query.get("s", [""])[0])
                verify_click_signature(
                    token,
                    link_id,
                    destination_url,
                    link_title,
                    signature,
                    self.app.secret,
                )
                identity = self.app.identity(token)
                try:
                    self.app.store.record_click(
                        identity,
                        link_id,
                        link_title,
                        destination_url,
                        qualified=self.is_qualified_click(),
                    )
                except Exception as exc:  # Analytics must not block source reading.
                    print(f"feedback click event not recorded: {exc}")
                self.send_redirect(destination_url)
                return
            if path.startswith(f"{API_SUMMARY_PREFIX}/"):
                if not self.require_admin(api=True):
                    return
                self.app.refresh_deliveries()
                issue_id = path.removeprefix(f"{API_SUMMARY_PREFIX}/")
                self.send_json(HTTPStatus.OK, self.app.store.summary(issue_id))
                return
            if path.startswith(f"{API_COMMENTS_PREFIX}/"):
                if not self.require_admin(api=True):
                    return
                issue_id = path.removeprefix(f"{API_COMMENTS_PREFIX}/")
                self.send_json(
                    HTTPStatus.OK,
                    {"issue_id": issue_id, "comments": self.app.store.list_comments(issue_id)},
                )
                return
            if path == API_ISSUES_PATH:
                if not self.require_admin(api=True):
                    return
                self.app.refresh_deliveries()
                self.send_json(
                    HTTPStatus.OK,
                    {"issues": self.app.store.list_issue_summaries()},
                )
                return
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
        except FeedbackError as exc:
            self.send_html(HTTPStatus.BAD_REQUEST, self.error_page(str(exc)))

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            data = self.read_json()
            if path == API_DELIVERY_PATH:
                if not self.require_admin(api=True):
                    return
                issue_id = str(data.get("issue_id") or "")
                try:
                    recipient_count = int(data.get("recipient_count") or 0)
                    issue_number_raw = data.get("issue_number")
                    issue_number = int(issue_number_raw) if issue_number_raw is not None else None
                except (TypeError, ValueError) as exc:
                    raise FeedbackError("Invalid delivery count or issue number.") from exc
                self.app.store.upsert_delivery(
                    issue_id,
                    issue_number=issue_number,
                    series=str(data.get("series") or "") or None,
                    sent_at=str(data.get("sent_at") or "") or None,
                    recipient_count=recipient_count,
                    recipient_group=str(data.get("recipient_group") or "") or None,
                    recipient_fingerprint=str(data.get("recipient_fingerprint") or "") or None,
                )
                self.send_json(HTTPStatus.OK, self.app.store.summary(issue_id))
                return
            token = str(data.get("token") or "")
            event_id = str(data.get("event_id") or "")
            identity = self.app.identity(token)
            if path == API_RATING_PATH:
                try:
                    score = int(data.get("score"))
                except (TypeError, ValueError) as exc:
                    raise FeedbackError("Score must be between 1 and 5.") from exc
                self.send_json(
                    HTTPStatus.OK,
                    self.app.store.record_rating(identity, score, event_id),
                )
                return
            if path == API_COMMENT_PATH:
                comment = str(data.get("comment") or "")
                self.send_json(
                    HTTPStatus.OK,
                    self.app.store.record_comment(identity, comment, event_id),
                )
                return
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
        except FeedbackError as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the AI Daily feedback P0 service.")
    parser.add_argument("--host", default=os.environ.get("AI_DAILY_FEEDBACK_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("AI_DAILY_FEEDBACK_PORT") or os.environ.get("PORT") or "8765"),
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(os.environ.get("AI_DAILY_FEEDBACK_DB", "feedback_service/data/feedback.db")),
    )
    parser.add_argument("--secret", default=os.environ.get("AI_DAILY_FEEDBACK_SECRET"))
    parser.add_argument("--admin-key", default=os.environ.get("AI_DAILY_FEEDBACK_ADMIN_KEY"))
    parser.add_argument(
        "--send-ledger",
        type=Path,
        default=Path(os.environ.get("AI_DAILY_SEND_LEDGER", str(DEFAULT_SEND_LEDGER))),
        help="Production send ledger used to calculate delivery denominators.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.secret:
        raise SystemExit("Missing --secret or AI_DAILY_FEEDBACK_SECRET.")
    app = FeedbackApplication(args.database, args.secret, args.admin_key, args.send_ledger)
    server = ThreadingHTTPServer((args.host, args.port), FeedbackHandler)
    server.app = app  # type: ignore[attr-defined]
    print(f"AI Daily feedback: http://{args.host}:{args.port}")
    print(f"Database: {args.database.resolve()}")
    print(f"Send ledger: {args.send_ledger.resolve()}")
    if args.admin_key:
        print(f"Admin dashboard: http://{args.host}:{args.port}/admin")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
