# Security Policy

ip·Solis manages access to IT assets, so we take security reports seriously.

## Reporting a vulnerability

**Please do not open a public issue, pull request, or discussion for security
problems.**

Report privately through either channel:

1. **GitHub private vulnerability reporting** —
   [open a draft advisory](https://github.com/XenPool/ipsolis/security/advisories/new)
   (preferred; keeps the report linked to the repo).
2. **Email** — [security@xenpool.de](mailto:security@xenpool.de). Encrypt with
   our PGP key on request.

Please include:

- A description of the issue and its impact.
- Steps to reproduce or a proof of concept.
- Affected version (see the `VERSION` file).
- Any relevant configuration — **with secrets, tokens, and personal data redacted**.

## What to expect

- **Acknowledgement within one business day.**
- An initial assessment and severity rating shortly after.
- Coordinated disclosure: we agree on a timeline with you, ship a fix, and
  credit you in the changelog and advisory if you wish.

Please give us reasonable time to remediate before any public disclosure.

## Supported versions

ip·Solis is pre-1.0 and ships from a single active line. Security fixes land on
the latest released version. Always run the most recent release and apply
database migrations (`alembic upgrade head`) after upgrading.

## Hardening guidance

Operator-side hardening (TLS, secret management, RBAC, audit retention, network
isolation) is covered in [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md). ip·Solis is
designed for on-premises deployment with zero telemetry — no data leaves your
network.
