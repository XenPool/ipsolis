# Contributing to ip·Solis

Thanks for your interest in improving ip·Solis! Contributions — code, docs, bug
reports, and ideas — are welcome.

## Before you start

- **Discuss first.** For anything beyond a trivial fix, open an issue or a
  [Discussion](https://github.com/XenPool/ipsolis-community/discussions) before writing
  code, so we can agree on the approach and avoid wasted effort.
- **Questions** belong in Discussions, **bugs and concrete features** in Issues.
  See [SUPPORT.md](SUPPORT.md).

## Licensing of contributions

ip·Solis Community Edition is licensed under **AGPL-3.0**. By submitting a
contribution you agree it is licensed under the same terms and that you have the
right to contribute it. Keep third-party code compatible with AGPL-3.0 and record
it in [`THIRD-PARTY-LICENSES.md`](THIRD-PARTY-LICENSES.md).

> Note: ip·Solis ships as a single codebase. Pro features live in the same repo
> but are excluded from the Community Docker image at build time. If your change
> touches a Pro-only file, say so in the PR description.

## Development setup

```bash
cp .env.example .env          # set DB credentials, API secret, admin key
docker compose up --build
docker compose exec api alembic upgrade head
```

- API + Admin UI: http://localhost:8000/ui/
- Self-Service Portal: http://localhost:8000/portal
- Swagger: http://localhost:8000/docs

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for the full guide and
[`CLAUDE.md`](CLAUDE.md) for architecture and development notes.

## Branching & pull requests

- Branch from and target **`dev`** — all PRs merge into `dev`, not `main`.
- Use feature branches: `feature/<short-name>` or `fix/<short-name>`.
- Keep PRs focused; one logical change per PR.
- Write a clear description: what changed, why, and how you verified it.
- Reference the issue it closes (`Closes #123`).

## What we look for

- **It works and it's verified.** Include tests where practical, and describe
  how you tested. There is no mock mode — external systems point at real test
  environments.
- **Database changes** come with an Alembic migration. Enum types already exist
  in the DB — use `op.execute(raw SQL)` rather than `sa.Enum` in `create_table`
  (see CLAUDE.md → Database Migrations).
- **PowerShell script modules** return JSON on stdout, use pure ASCII, and never
  rely on interactive prompts.
- **No secrets** in code, fixtures, commits, or logs. Credentials are configured
  at runtime via Admin → Settings (`app_config`), not `.env`.
- Follow the existing code style and keep changes minimal and focused.

## Reporting bugs & requesting features

Use the issue templates — they prompt for the version, edition, and environment
details we need to help you. Always redact secrets and personal data.

## Code of conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md). By participating,
you agree to uphold it.
