# AI Daily Feedback Web

**English** | [简体中文](README.zh-CN.md) | [Project home](../README.md)

A Next.js + Supabase Postgres implementation of the newsletter feedback protocol, suitable for Vercel or a compatible Node.js deployment.

```text
Email links → Next.js pages and API routes → Supabase Postgres
                         ↓
              Authenticated dashboard
```

Use either this implementation or the [Python/SQLite service](../feedback_service/README.md) for a given feedback origin.

## Features

- `/f/<signed-token>#score=<1-5>` submits a star rating and supports changing it later.
- `/comment/<signed-token>` accepts an optional comment of up to 300 characters.
- `/open/<signed-token>.gif` records an estimated open.
- `/click/<signed-token>/<link-id>` records a source click and redirects to the signed destination.
- `/admin` shows delivery counts, estimated open rates, filtered click rates, ratings, and comments.
- `/api/delivery` receives production delivery counts used as rate denominators.

The mail packager derives issue-scoped pseudonymous recipient IDs with HMAC. Raw recipient email addresses are not sent to the feedback database. This is pseudonymization, not a guarantee of anonymity; protect the signing key and service logs.

## 1. Configure Supabase

Create a project and apply every file in [supabase/migrations/](supabase/migrations/) in filename order. Existing deployments should apply only migrations they have not already run.

| Table | Purpose |
| --- | --- |
| `deliveries` | Issue delivery time and recipient count |
| `open_events` | First/last open and count per issue and pseudonymous recipient |
| `click_events` | Candidate and filtered clicks per source |
| `ratings` | Latest rating per issue and pseudonymous recipient |
| `comments` | Latest comment per issue and pseudonymous recipient |
| `feedback_events` | Idempotency records |

Set `SUPABASE_URL` to your project URL and `SUPABASE_SECRET_KEY` to a server-side secret key. Database access happens on the server; do not put a secret key in a browser-exposed environment variable.

## 2. Configure the app

For Vercel, import the repository, set the Root Directory to `feedback_web`, and use the Next.js framework preset. Configure:

```text
SUPABASE_URL
SUPABASE_SECRET_KEY
AI_DAILY_FEEDBACK_SECRET
AI_DAILY_FEEDBACK_ADMIN_KEY
```

The signing secret must match the newsletter packager. Use a separate administrator key. Keep preview and production storage separate when possible, and clearly identify any test issues.

For local development:

```bash
cd feedback_web
cp .env.example .env.local
# Fill in your local environment values.
npm ci
npm run typecheck
npm run build
npm run dev
```

The configuration helper is an alternative to editing `.env.local`:

```bash
python3 scripts/configure_feedback_env.py \
  --project-url https://your-project.supabase.co
```

Never commit `.env.local`, `.vercel/`, or credentials.

## 3. Verify a deployment

From the repository root, configure the deployed origin and matching secrets:

```bash
export AI_DAILY_FEEDBACK_BASE_URL='https://feedback.example.com'
export AI_DAILY_FEEDBACK_SECRET='<matching-signing-secret>'
export AI_DAILY_FEEDBACK_ADMIN_KEY='<matching-admin-key>'

python3 -m feedback_service.check_deployment
python3 -m feedback_service.create_token \
  --issue 2026-07-23 \
  --recipient reader@example.com
```

Open the generated links and verify:

1. Submit four stars, then five: the rating changes while the participant count stays the same.
2. Submit a comment and confirm it appears in the dashboard.
3. Request the open pixel and inspect the estimate.
4. Follow a source link and confirm its redirect and click event.
5. Confirm `/admin` requires authentication.

## 4. Connect the mail workflow

Set the same base URL and secrets in the newsletter's ignored `.env` file. You can use the helper to synchronize the workflow configuration:

```bash
python3 feedback_web/scripts/configure_feedback_env.py \
  --sync-workflow \
  --feedback-base-url https://feedback.example.com
```

Build a package and send to the configured test group first:

```bash
python3 scripts/run_ai_daily.py package-test --date YYYY-MM-DD --require-feedback
python3 scripts/run_ai_daily.py test --date YYYY-MM-DD --skip-build --require-feedback --ask-password --send
```

After verifying opens, ratings, rating updates, comments, and the dashboard, use the explicit production command:

```bash
python3 scripts/run_ai_daily.py prod --date YYYY-MM-DD --skip-build --require-feedback --ask-password --send --confirm-prod
```

The workflow then synchronizes the production delivery count through `/api/delivery`. Without that denominator, event counts remain available but participation rates cannot be calculated reliably.

## Privacy and measurement

- Opens are estimates affected by image proxies, caching, and image blocking.
- Click filtering removes recognized prefetch and security-scanning traffic, but a click is not proof of reading.
- Feedback database records use pseudonymous IDs. Hosting and database logs may still retain IP addresses, user agents, and timestamps.
- Choose hosting, access controls, notices, and retention appropriate to your organization. The same protocol can be hosted on internal infrastructure when external hosting is unsuitable.

## License

Part of AI Daily Email Workflow, released under the [MIT License](../LICENSE).
