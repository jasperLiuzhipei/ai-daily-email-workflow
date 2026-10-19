---
name: ai-daily-imagegen
description: Generate, inspect and stage research illustrations in the AI Daily server project, preserving newspaper ink style and source-grounded information density. Use for server previews, repairs and image-workflow maintenance.
---

# AI Daily images

Find the workflow root containing `scripts/research_image_contract.py` and read
`docs/IMAGE_WORKFLOW.md`. It owns routing, review and staging conventions.
Scheduled jobs read the research system prompt and JSON drawing policy directly;
editing this skill alone does not change scheduled output.

Compile the six-field handoff with the project compiler. Do not append generic
image skills' visual templates, palette rules or label budgets. The accepted
style/density reference is `daily_runs/2026-09-28/image_policy_v14_preview/research-preview-v2.png`
relative to the workspace. Inspect it when comparing; its six stages are specific
to that source. Only claim comparisons with images actually viewed.

For requested server/API tests, use the configured `generate_research_image.py`
route, a fresh preview directory and `--forbid-fallback`. Save the handoff,
compiled prompt, receipt, original output and real generation manifest. Keep
credentials in the configured environment. For other routes follow the project
workflow and the applicable tool instructions.

Inspect the actual image against the handoff and shared drawing policy. Record
specific observations in the six-check editorial audit, including visible ink
contours/hatching and actual arrowhead receivers for each directed relation; file decoding or a
model's summary cannot substitute for those checks. Preserve complete mechanism,
evidence and boundaries while removing repetition. A useful dense composition
may have sections; avoid turning this into a universal layout requirement.

For defects, make one targeted retry using the original prompt plus a short
current correction; keep both versions and actual prompts. For aesthetic review,
show the saved preview and await feedback. User acceptance records a preference;
publication requires the existing per-image checks and task authorization.
