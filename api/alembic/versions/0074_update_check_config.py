"""Seed config keys for the optional update-check Beat task.

The notifier is **opt-in** so air-gapped tenants don't make outbound
calls without explicit consent. A daily Beat task hits the configured
GitHub releases endpoint, parses the latest tag, and stores the
result in ``app_config``. The base.html banner partial reads those
keys via the existing ``refresh_app_config_if_stale`` machinery.

Keys seeded:

* ``updates.check_enabled`` — master toggle (``"true"`` / ``"false"``).
  Default ``"false"``.
* ``updates.repo_url`` — GitHub API endpoint to query. Defaults to
  the public XenPool/ipSolis repo. Operators can repoint at a
  private fork if they ship internal patches.
* ``updates.latest_version`` / ``updates.latest_url`` /
  ``updates.latest_published_at`` — populated by the Beat task on
  each successful poll. Empty initially so the banner stays hidden
  on a fresh install until the first tick.
* ``updates.checked_at`` / ``updates.check_error`` — observability
  (last success / last error). The Settings UI surfaces both.

Idempotent: re-running the migration on an instance that already has
these rows is a no-op (``ON CONFLICT DO NOTHING``).

Revision ID: 0074
Revises: 0073
Create Date: 2026-04-27
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0074"
down_revision: Union[str, None] = "0073"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO app_config (key, value, is_secret, description)
        VALUES
          ('updates.check_enabled', 'false', false,
           'Update notifier — when ON, a daily Beat task checks the configured GitHub releases endpoint and shows a banner if a newer version is available. OFF by default so air-gapped tenants don''t make outbound calls.'),
          ('updates.repo_url', 'https://api.github.com/repos/XenPool/ipSolis', false,
           'GitHub API URL the update notifier queries (must end in /repos/<owner>/<repo>). Repoint at a private fork if you ship internal patches.'),
          ('updates.latest_version', '', false,
           'Latest release tag observed by the update notifier. Filled in by the daily Beat task; empty when the toggle is off or no successful check has run yet.'),
          ('updates.latest_url', '', false,
           'HTML URL of the latest release (release notes page). Filled in by the daily Beat task.'),
          ('updates.latest_published_at', '', false,
           'ISO-8601 timestamp of when the latest release was published. Filled in by the daily Beat task.'),
          ('updates.checked_at', '', false,
           'ISO-8601 timestamp of the last successful update check. Useful for observability; the Settings UI surfaces it next to the toggle.'),
          ('updates.check_error', '', false,
           'Last error message from the update notifier, or empty when the last check succeeded. Surfaced in the Settings UI so operators can spot DNS / proxy issues.')
        ON CONFLICT (key) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM app_config WHERE key IN (
          'updates.check_enabled',
          'updates.repo_url',
          'updates.latest_version',
          'updates.latest_url',
          'updates.latest_published_at',
          'updates.checked_at',
          'updates.check_error'
        );
        """
    )
