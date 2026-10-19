# AI Daily Feedback Service

**English** | [简体中文](README.zh-CN.md) | [Project home](../README.md)

A Python/SQLite feedback service with no third-party Python dependencies. It provides star ratings, optional comments, estimated opens, source-click tracking, delivery-count synchronization, and an authenticated dashboard.

Use this implementation on a server or container with persistent storage. For the Next.js/Supabase implementation, see [Feedback Web](../feedback_web/README.md).

## Local setup

Run from the repository root. Use distinct generated secrets for link signing and administrator access:

```bash
export AI_DAILY_FEEDBACK_SECRET='<random-signing-secret-at-least-16-characters>'
export AI_DAILY_FEEDBACK_ADMIN_KEY='<separate-admin-secret>'
python3 -m feedback_service.server
```

Open `http://127.0.0.1:8765/admin`. The HTTP Basic authentication username can be any value; the password is `AI_DAILY_FEEDBACK_ADMIN_KEY`. The service can import issue dates and delivery counts from `daily_runs/send_ledger.json`. Set `AI_DAILY_SEND_LEDGER` or `--send-ledger` to use another location.

Generate a test link in a terminal with the same signing secret:

```bash
python3 -m feedback_service.create_token \
  --issue 2026-07-17 \
  --recipient reader@example.com
```

Inspect the summary, comments, or issue list:

```bash
curl http://127.0.0.1:8765/api/summary/2026-07-17 \
  -H "X-Admin-Key: $AI_DAILY_FEEDBACK_ADMIN_KEY"
curl http://127.0.0.1:8765/api/comments/2026-07-17 \
  -H "X-Admin-Key: $AI_DAILY_FEEDBACK_ADMIN_KEY"
curl http://127.0.0.1:8765/api/issues \
  -H "X-Admin-Key: $AI_DAILY_FEEDBACK_ADMIN_KEY"
```

## Container deployment

```bash
docker build -f feedback_service/Dockerfile -t ai-daily-feedback .
docker run --rm -p 8765:8765 \
  -e AI_DAILY_FEEDBACK_SECRET \
  -e AI_DAILY_FEEDBACK_ADMIN_KEY \
  -e AI_DAILY_SEND_LEDGER='/runtime/send_ledger.json' \
  -v ai_daily_feedback:/data \
  -v /path/to/send_ledger.json:/runtime/send_ledger.json:ro \
  ai-daily-feedback
```

Inject secrets through your deployment environment and mount `/data` on persistent storage. Serve the application at the root of an HTTPS origin reachable by email recipients. Configure the variables listed in [platform.env.example](platform.env.example), including:

```bash
AI_DAILY_FEEDBACK_BASE_URL=https://feedback.example.com
```

The service accepts a platform-provided `PORT`, or `AI_DAILY_FEEDBACK_PORT` as an explicit override. Run the deployment check after configuring the public URL and credentials:

```bash
python3 -m feedback_service.check_deployment
```

This checks health and service metadata and prints the resulting mail, dashboard, and API URLs.

## Routes

| Route | Purpose |
| --- | --- |
| `/healthz` | Health check |
| `/.well-known/ai-daily-feedback.json` | Service metadata |
| `/f/<signed-token>#score=<1-5>` | Submit or update a rating |
| `/comment/<signed-token>` | Optional comment, up to 300 characters |
| `/open/<signed-token>.gif` | Estimated open pixel |
| `/click/<signed-token>/<link-id>?u=<destination>&t=<title>&s=<signature>` | Signed source redirect and click tracking |
| `/admin` | Authenticated dashboard |
| `POST /api/delivery` | Synchronize delivery counts |
| `GET /api/issues` | List issues |
| `GET /api/summary/<issue-id>` | Issue metrics |
| `GET /api/comments/<issue-id>` | Issue comments |

Ratings are deduplicated by issue and pseudonymous recipient; another rating updates the existing value. Request `event_id` values support idempotency. The score is in the URL fragment so a simple link fetch does not transmit it to the server. Comment submission is a separate, optional action.

## Connect to the newsletter

The service and mail packager must use the same `AI_DAILY_FEEDBACK_SECRET`. Configure the signing secret, administrator key, and public base URL in the newsletter environment. The example email settings reference `AI_DAILY_FEEDBACK_BASE_URL`; a configured URL enables feedback packaging.

```bash
python3 scripts/run_ai_daily.py package-test --date YYYY-MM-DD --require-feedback
python3 scripts/run_ai_daily.py test --date YYYY-MM-DD --skip-build --require-feedback --ask-password --send
```

The package's `messages/` directory contains the personalized EML files. Its top-level EML and `body.html` are previews without tracking. Use the workflow sender so each recipient receives their own message.

After successful production delivery, the workflow calls `/api/delivery` using the administrator key. A temporary synchronization failure does not undo SMTP delivery or its local ledger entry.

## Measurement and privacy

- The service stores issue-scoped pseudonymous IDs, rather than raw recipient email addresses. Keep the signing secret private.
- Opens are estimates: image blocking, proxies, and caching affect counts.
- Click filtering removes recognizable prefetch and scanning requests; it cannot prove that a human read the source.
- Rating and comment pages try to close after submission, with a visible success page when the browser prevents closing.
- Dashboard timestamps use Asia/Shanghai. Ratings and comments are deduplicated per issue and pseudonymous recipient.
- Server and reverse-proxy logs can still contain IP addresses, user agents, and timestamps. Configure storage, access, and retention for your deployment.

## License

Part of AI Daily Email Workflow, released under the [MIT License](../LICENSE).
