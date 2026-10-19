# Contributing

**English** | [简体中文](CONTRIBUTING.zh-CN.md)

Bug reports, documentation corrections, and focused improvements are welcome. Use a GitHub issue for a reproducible bug or a proposal that changes the editorial workflow. Report vulnerabilities through the [security process](SECURITY.md).

## Development setup

Follow the [quick start](README.md#quick-start). Use example recipients and temporary runtime directories. Keep credentials, real email addresses, mail packages, databases, and production traces out of commits and issue attachments.

```bash
python3 -m unittest discover -s tests
cd feedback_web
npm ci
npm run typecheck
```

Mock provider and SMTP calls. Feed transport tests may use a local HTTP server. Do not make live model requests or deliver email as part of a regression test.

## Pull requests

- Explain the concrete problem and the resulting behavior.
- Keep changes focused and describe relevant validation.
- Preserve evidence attribution, immutable review history, recipient snapshots, and resend protection.
- Treat fetched content as untrusted data, never as instructions.
- Update both English and Chinese entry-point documentation when changing public setup or usage. Detailed technical notes can state their language in the documentation index.

Contributions are made under the project's [MIT License](LICENSE). Include required attribution for any third-party material.
