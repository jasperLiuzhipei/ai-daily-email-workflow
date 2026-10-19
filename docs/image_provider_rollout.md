# Research Image Provider Rollout

## Policy

- `codex_imagegen` is the production baseline. Existing daily runs keep their current behavior.
- `gpt_image_2` / `image2` is an API-backed candidate route. It is not production-approved by default.
- A candidate can produce a comparison HTML only when the run explicitly passes `--allow-candidate-image-provider`.
- A candidate may enter a production email only after both gates pass: the provider is marked `production_allowed: true` in `config/image_providers.json`, and the staged image has `--quality-decision approved` plus concise manual comparison notes.
- A failed or undecided candidate must fall back to the native baseline or stop the run. It must never silently replace the research image.
- A scheduled server run must use one approved Image2 route only. Pass `--strict-image-route` (or `--forbid-fallback` to the lower-level generator) so that a network failure stops at preview instead of changing gateways mid-run.

## Native Baseline

Continue to generate through Codex ImageGen, then finalize exactly as before. The registry records this as `codex_imagegen`; no API key or new command is needed.

## Image2 Candidate

The adapter expects an OpenAI Images-compatible endpoint configured by `config/ai_runtime.json`. Keep the secret in `.env` or a company secret store; never add it to repository files. Image API routing does not live in `image_providers.json`; that file only records quality and production policy.

```bash
AI_DAILY_API_KEY=
AI_DAILY_API_BASE_URL=https://<approved-gateway>/v1

python3 ai_daily_workflow/scripts/generate_research_image.py \
  --provider image2 \
  --prompt-file daily_runs/YYYY-MM-DD/research_image_prompt.txt \
  --out daily_runs/YYYY-MM-DD/image2_candidate.png \
  --generation-manifest daily_runs/YYYY-MM-DD/image2_generation.json \
  --forbid-fallback
```

After visually comparing the candidate against a native ImageGen baseline using the same Visual Handoff, build a non-production comparison email with the runner:

```bash
python3 ai_daily_workflow/scripts/run_ai_daily.py preview --date YYYY-MM-DD --theme digest_navy --news-layout paper
```

Use the candidate result only for comparison until the provider configuration is intentionally approved. `pending` is therefore useful for visual testing, but cannot be sent through the production path.

## Stability Test Before A/B

Do not replace the baseline after one successful image. First test the exact same Visual Handoff three times through the company gateway. The test writes raw candidates, per-request manifests, a contact sheet and `stability_report.json`; it does not stage an asset, rebuild HTML or send an email.

Put the secret only in the ignored local `.env` file:

```bash
AI_DAILY_API_KEY=
AI_DAILY_API_BASE_URL=https://<approved-gateway>/v1
```

The script loads `.env` automatically. The optional baseline path makes the contact sheet suitable for an immediate visual A/B.

```bash
python3 ai_daily_workflow/scripts/test_image_provider_stability.py \
  --provider image2 \
  --prompt-file daily_runs/YYYY-MM-DD/research_image_prompt.txt \
  --out-dir daily_runs/YYYY-MM-DD/image2_stability \
  --baseline-image assets/email/YYYY-MM-DD/your-native-baseline.jpg \
  --attempts 3 \
  --size 1024x1024 \
  --quality high
```

Round-one transport gate: all three requests return a valid image and generation manifest, without a timeout, schema mismatch or malformed payload. The report also records request latency, output dimensions, image hashes and returned usage metadata when the gateway provides it.

Before the three requests, the test resolves the image gateway through `ai_runtime.json` and performs a DNS/TCP preflight. If the gateway hostname cannot be reached, it writes a blocked report without issuing image requests. Ask the platform owner for a desktop/VPN-reachable address, or run the same command in the intended internal runtime; do not change the prompt or lower quality to work around a network failure.

Then complete the five pending manual checks in the report: mechanism accuracy, Chinese text readability, evidence integrity, palette independence and email-scale readability. A provider should remain a candidate until it is at least equal to the Codex ImageGen baseline across several different research subjects; the test is evidence for that decision, not an automatic promotion.

## Company Image2 Not OpenAI-Compatible

Do not force it through this adapter. Add a provider entry with a distinct `execution` value, then implement a small adapter in `generate_research_image.py` that records the same prompt hash, output hash, request metadata, and provider id. The staging and quality gate can remain unchanged.
