# Research Image Provider Rollout

## Policy

- `codex_imagegen` is the production baseline. Existing daily runs keep their current behavior.
- `gpt_image_2` / `image2` is an API-backed candidate route. It is not production-approved by default.
- A candidate can produce a comparison HTML only when the run explicitly passes `--allow-candidate-image-provider`.
- A candidate may enter a production email only after both gates pass: the provider is marked `production_allowed: true` in `config/image_providers.json`, and the staged image has `--quality-decision approved` plus concise manual comparison notes.
- A failed or undecided candidate must fall back to the native baseline or stop the run. It must never silently replace the research image.

## Native Baseline

Continue to generate through Codex ImageGen, then finalize exactly as before. The registry records this as `codex_imagegen`; no API key or new command is needed.

## Image2 Candidate

The adapter expects an OpenAI Images-compatible endpoint. Use the normal OpenAI endpoint with `OPENAI_API_KEY`, or set `OPENAI_BASE_URL` to a company gateway that exposes the same endpoint. Keep the secret in the shell or a company secret store; never add it to repository files.

```bash
export OPENAI_API_KEY='...'

python3 ai_daily_workflow/scripts/generate_research_image.py \
  --provider image2 \
  --prompt-file daily_runs/YYYY-MM-DD/research_image_prompt.txt \
  --out daily_runs/YYYY-MM-DD/image2_candidate.png \
  --generation-manifest daily_runs/YYYY-MM-DD/image2_generation.json
```

After visually comparing the candidate against a native ImageGen baseline using the same Visual Handoff, build a non-production comparison email:

```bash
python3 ai_daily_workflow/scripts/finalize_ai_daily_workflow.py \
  --date YYYY-MM-DD \
  --content daily_runs/YYYY-MM-DD/ai_daily_email_content.json \
  --brief-json daily_runs/YYYY-MM-DD/research_brief.parsed.json \
  --prompt-file daily_runs/YYYY-MM-DD/research_image_prompt.txt \
  --image-source daily_runs/YYYY-MM-DD/image2_candidate.png \
  --source-kind generated \
  --provider-id image2 \
  --generation-manifest daily_runs/YYYY-MM-DD/image2_generation.json \
  --quality-decision pending \
  --allow-candidate-image-provider \
  --review-status pass \
  --review-notes "candidate checked against Visual Handoff"
```

Use the candidate result only for comparison until the provider configuration is intentionally approved. `pending` is therefore useful for visual testing, but cannot be sent through the production path.

## Company Image2 Not OpenAI-Compatible

Do not force it through this adapter. Add a provider entry with a distinct `execution` value, then implement a small adapter in `generate_research_image.py` that records the same prompt hash, output hash, request metadata, and provider id. The staging and quality gate can remain unchanged.
