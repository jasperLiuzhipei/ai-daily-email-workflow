# AI Daily Email Workflow

**English** | [简体中文](README.zh-CN.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

An AI newsletter workflow that selects sources, writes evidence-grounded briefs, reviews the final copy and research visuals, and delivers HTML email with inline images and optional reader feedback.

The repository includes the editorial pipeline, deployment templates, a Python/SQLite feedback service, and a Next.js/Supabase feedback app. Documentation entry points are available in English and Chinese. The bundled editorial prompts, newsletter templates, and feedback interfaces currently target Chinese readers; translating the documentation does not change the generated newsletter's language.

## How it works

```text
Source discovery → Event assessment → Evidence collection → Editorial JSON
                                                           ↓
                       Research visuals → HTML email → Final review
                                                           ↓
                                    CID package → SMTP delivery → Feedback
```

- **Source selection:** configurable feeds, primary-source checks, event grouping, publication-history deduplication, and a news volume budget.
- **Editorial generation:** detailed research readings and shorter news briefs, with evidence attribution and explicit boundaries.
- **Review:** final-copy and visual checks, bounded recovery, saved inputs, immutable review history, and artifact hashes.
- **Email:** table-based HTML for mail clients, CID image attachments, frozen recipient snapshots, and delivery records that prevent accidental resends.
- **Feedback:** optional ratings, comments, estimated opens, filtered source clicks, and an authenticated dashboard.
- **Operations:** workday calendars, per-issue locks, deployment templates, and traceable workflow state.

The main workflow is the production path. `scripts/daily_v2/` is a separate shadow implementation; see the [documentation index](docs/README.md) for its status and historical design notes.

## Quick start

Use Python 3.11 or later and `curl`. The optional web feedback app also needs Node.js and npm. Configure model names and request parameters for your provider; the supplied names are configuration examples, not a guarantee of provider availability.

```bash
git clone https://github.com/jasperLiuzhipei/ai-daily-email-workflow.git ai_daily_workflow
cd ai_daily_workflow

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp config/email_settings.example.json config/email_settings.json
cp config/recipients.example.json config/recipients.json
cp config/editor_settings.example.json config/editor_settings.json
cp config/ai_runtime.example.json config/ai_runtime.json
cp .env.example .env
```

Edit the local files with your SMTP settings, test recipients, editor information, and API configuration. Store credentials in environment variables or the ignored `.env` file. The API adapter supports separate text and image endpoints using an OpenAI-compatible request format.

```bash
# Validate local configuration without calling the provider.
python3 scripts/run_ai_daily.py check-api

# Enter a provider key through a hidden terminal prompt.
python3 scripts/configure_ai_runtime.py --base-url https://your-gateway.example.com/v1

# Or configure text and image providers separately.
python3 scripts/configure_ai_runtime.py --scope text --base-url https://text.example.com/v1
python3 scripts/configure_ai_runtime.py --scope image --base-url https://images.example.com/v1
```

API examples use `gpt-6-sol` and `gpt-image-2`; adjust model IDs, image sizes, and request defaults to match your provider. Source policies, audience profiles, content sections, editorial rules, and calendars are versioned in `config/`. Detailed field references are linked from the [documentation index](docs/README.md).

### Prepare and preview

Replace `YYYY-MM-DD` with the issue date. Generation commands call configured external providers and can incur charges.

```bash
# Inspect the issue and recipient-group metadata.
python3 scripts/run_ai_daily.py status --date YYYY-MM-DD
python3 scripts/run_ai_daily.py recipients

# Select sources, fetch evidence, draft content, and generate a research image.
python3 scripts/run_ai_daily.py prepare --date YYYY-MM-DD

# Render a comparison preview without recording a production delivery.
python3 scripts/run_ai_daily.py preview --date YYYY-MM-DD --theme digest_navy --news-layout paper

# Inspect the per-issue workflow state.
python3 scripts/run_ai_daily.py state --date YYYY-MM-DD
```

Research images use a configured canvas, with `1920x1080` in the current example. A one-run override is available through `api-image --image-size ... --refresh-api-image`. Provider output dimensions are recorded separately from the requested size.

### Review and send

Public defaults keep `automatic_send_enabled` **false** in `config/email_quality_policy.json`, with an empty authorization record. Automatic delivery requires a deployment-specific opt-in after configuring and verifying test recipients. Installing the source code does not enable a system scheduler.

```bash
# Build after the content and required research-image checks are complete.
python3 scripts/run_ai_daily.py build --date YYYY-MM-DD --theme digest_navy --news-layout paper

# Send only to the configured test group.
python3 scripts/run_ai_daily.py test --date YYYY-MM-DD --skip-build --ask-password --send

# Explicitly send to the configured production group.
python3 scripts/run_ai_daily.py prod --date YYYY-MM-DD --skip-build --ask-password --send --confirm-prod
```

Final-copy and visual gates apply to delivery. An already published issue is protected against another production send; `--allow-resend-prod` is reserved for an intentional resend. SMTP acceptance is recorded separately from any claim that a message reached the recipient's inbox.

Each issue has a `daily_runs/YYYY-MM-DD/workflow_run.json` containing its stable run ID, stages, artifact hashes, recipient-snapshot metadata, and delivery status. Actual addresses and personalized messages remain in private runtime files.

## Recipients and publication history

`config/recipients.json` stores versioned, enabled recipient groups. Packaging creates an immutable `recipient_snapshot.json`; delivery uses that snapshot rather than a list that might change mid-run. Increment a group's version when changing its membership.

Legacy recipient files can be migrated with a local backup:

```bash
python3 scripts/migrate_recipient_registry.py --write
```

Only successfully published issues advance publication history. Previewing an issue does not advance the deduplication cutoff.

## Editorial diagnostics

```bash
# Compare selected sources with the final issue and create a review worksheet.
python3 scripts/run_ai_daily.py diagnose --date YYYY-MM-DD

# Aggregate worksheets whose review_status is completed.
python3 scripts/run_ai_daily.py replay
```

Diagnostics distinguish missing material, selection-to-publication differences, and editorial deferrals. Replay data supports policy evaluation without automatically changing selection rules.

Single-source arXiv abstract assessments share the unchanged rubric in batches of up to four. The reader keeps the complete abstract and removes only a recognized metadata/navigation tail. Each paper retains its own score, exact source/quote validation, exceptional-score review and individual fallback. Batch traces live under `event_editorial/paper_batches/`; count usage once per request ID, even when several event records reference it. Set `paper_batch_size` to `1` in the editorial policy to use individual requests.

Translated arXiv leads are recognized by matching paper IDs, explicit versions, page declarations and the abstract body. X/Twitter account links and `/i/web/status/` redirects are checked against the same post ID. Feed links with an explicit full-width prose delimiter are cleaned while encoded query values remain intact. Source recovery prioritizes observed alternatives, reuses already-read responses with fresh relevance checks, and skips same-run repeats of 401/403/404/410 errors or blocked/short/unmatched pages. Transient failures remain retryable within the existing time and event limits. These checks establish readable evidence, not publication approval; recovery traces record skipped routes, reused responses and request counts.

An offline replay of 119 papers estimated a **66.1% reduction in input message tokens** (119 initial requests → 30 batches, using `o200k_base`). Recorded evidence and decisions survived replay; this is not a fresh model-quality evaluation or measured billing savings. Output tokens, retries and provider cache pricing are not included. Feed errors report the URL, HTTP status, attempt and elapsed time, with credential-bearing URL fields redacted.

A four-paper check with the original model reported **25,961 → 10,764 total tokens (58.5% less)** and **20,352 → 6,358 input tokens (68.8% less)** against the earlier first attempts. All source/quote checks passed, including a previously invalid source-ID case. Scores and deferral reasons varied; this small sample does not establish unchanged judgments or guaranteed savings for every issue.

On the 2026-10-19 validation issue, compacting duplicate review metadata preserved the evidence text and reduced measured input tokens from **91,584 to 65,702 (28.26%)**. A single follow-up with the original reviewer passed all six checks. This is a measured result for one issue, not an estimate of universal savings or a benchmark of long-term review quality. See the [trace optimization note (Chinese)](docs/TRACE_OPTIMIZATION_2026-10-19.md).

## Optional feedback services

| Implementation | Storage | Deployment | Guide |
| --- | --- | --- | --- |
| Python service | SQLite | Local development or a server/container with a persistent volume | [English](feedback_service/README.md) · [中文](feedback_service/README.zh-CN.md) |
| Next.js app | Supabase Postgres | Vercel or a compatible Node.js deployment | [English](feedback_web/README.md) · [中文](feedback_web/README.zh-CN.md) |

Both implementations use signed links and issue-scoped pseudonymous recipient IDs. Enabling feedback creates one personalized EML per recipient; the top-level EML and `body.html` remain previews without tracking. Estimated opens and filtered clicks are imperfect engagement measurements, not proof of reading.

The mail packager and feedback service must share the same signing secret:

```bash
export AI_DAILY_FEEDBACK_BASE_URL='https://feedback.example.com'
export AI_DAILY_FEEDBACK_SECRET='<secret-store-value>'
export AI_DAILY_FEEDBACK_ADMIN_KEY='<separate-admin-key>'
python3 scripts/run_ai_daily.py package-test --date YYYY-MM-DD --require-feedback
```

Use an HTTPS address reachable by your recipients. Follow the relevant guide for storage, administrator authentication, delivery-count synchronization, and privacy considerations.

## Deployment

The files in `deploy/systemd/` are templates. They assume a dedicated `ai-daily` account and this layout:

```text
/opt/ai-daily/
├── .venv/
├── ai_daily_workflow/     # this repository
├── daily_runs/
├── output/
└── email_packages/
```

Adapt the account, paths, environment file, work calendar, and schedule before enabling timers. The reviewed publishing service can send mail only after the automatic-delivery policy has been explicitly enabled. Keep deployed secrets and runtime data outside Git.

The macOS preview helper accepts your SSH destination through `AI_DAILY_PREVIEW_SERVER` and an optional `AI_DAILY_REMOTE_ROOT` (default `/opt/ai-daily`). It copies an existing preview and opens it locally.

```bash
AI_DAILY_PREVIEW_SERVER='ai-daily@your-server.example.com' \
  zsh deploy/mac/ai-daily-preview YYYY-MM-DD
```

## Development

Run from the repository root:

```bash
python3 -m unittest discover -s tests

cd feedback_web
npm ci
npm run typecheck
```

The Python suite uses temporary runtime fixtures and mocked providers; it includes a local HTTP server for feed transport tests. Do not use production recipients or real provider calls in regression tests. See [Contributing](CONTRIBUTING.md) for scope and submission guidance.

## Repository layout

```text
config/            policies, provider definitions, and example settings
deploy/            systemd templates and macOS preview helper
docs/              documentation index, architecture, and historical notes
feedback_service/  Python/SQLite feedback implementation
feedback_web/      Next.js/Supabase feedback implementation
prompts/           editorial and research prompts
scripts/           selection, generation, review, packaging, and delivery
templates/         source manifests and email layouts
tests/             regression tests and fixtures
```

Credentials, real recipient registries, databases, runtime reports, images, mail packages, and logs are excluded from source control. See [Security](SECURITY.md) for reporting a vulnerability.

## License

Project code and original documentation are available under the [MIT License](LICENSE). Third-party dependencies and quoted source material retain their respective licenses and rights.
