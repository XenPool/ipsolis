"""Seed AWS Secrets Manager AssumeRole config keys.

Slice-1 of the AWS SM adapter shipped static-IAM-key auth only. STS-issued
temporary credentials work too (you paste access_key + secret + session_token
all three), but the operator has to push refreshed values into ip·Solis
manually before the session expires — typically every 1-12 hours. That
breaks down at the boundary between "the credentials we have now" and
"a config edit happened too late".

Native AssumeRole closes the loop: ip·Solis itself calls
``sts:AssumeRole`` with a long-lived bootstrap identity (the same
``access_key_id`` / ``secret_access_key`` rows used in static mode)
plus a target ``role_arn``, caches the returned short-lived
credentials until ~60s before their ``Expiration`` field, and
re-mints automatically. The operator never touches a session token
on this path; the bootstrap IAM user just needs ``sts:AssumeRole``
on the target role.

Five new keys land here. ``auth_method`` defaults to ``static`` so
existing installs upgrade silently; flipping it to ``assume_role``
unlocks the rest.

Revision ID: 0089
Revises: 0088
Create Date: 2026-04-30
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0089"
down_revision: Union[str, None] = "0088"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_KEYS = [
    (
        "secret.awssm.auth_method",
        "static",
        "AWS authentication method. One of: 'static' (long-lived IAM "
        "user keys, optionally with a manually-pasted STS session "
        "token — slice 1 default) or 'assume_role' (ip·Solis calls "
        "sts:AssumeRole using the configured keys as the bootstrap "
        "identity, then uses the derived short-lived credentials for "
        "all Secrets Manager calls; refresh is automatic). The "
        "static path is fine for lab installs; production should "
        "switch to assume_role so credential rotation is a Vault / "
        "IAM-side concern, not an ip·Solis-side one.",
        False,
    ),
    (
        "secret.awssm.role_arn",
        "",
        "AssumeRole target — the IAM role ip·Solis switches into for "
        "Secrets Manager calls. The bootstrap identity (configured via "
        "secret.awssm.access_key_id / secret_access_key) needs "
        "sts:AssumeRole permission on this role; the role itself "
        "carries the secretsmanager:GetSecretValue / ListSecrets "
        "permissions ip·Solis actually needs. Example: "
        "arn:aws:iam::123456789012:role/ipsolis-secrets-reader.",
        False,
    ),
    (
        "secret.awssm.role_session_name",
        "ipsolis",
        "AssumeRole session name — appears in CloudTrail and on the "
        "principal of every signed call, so SOC operators can trace "
        "activity back to ip·Solis. Default 'ipsolis'; set per "
        "environment (e.g. 'ipsolis-prod', 'ipsolis-lab') if multiple "
        "deployments share an AWS account.",
        False,
    ),
    (
        "secret.awssm.role_external_id",
        "",
        "AssumeRole optional external_id — the third-party-IAM trust "
        "convention. Set when the target role's trust policy requires "
        "it (typical when the role lives in another AWS account "
        "managed by a separate team). Empty otherwise.",
        False,
    ),
    (
        "secret.awssm.role_duration_seconds",
        "3600",
        "Requested AssumeRole session length in seconds. Capped on "
        "the AWS side by the role's MaxSessionDuration (default 1h, "
        "max 12h). Default 3600 — a clean multiple of the typical "
        "Vault token TTL, easy to reason about in alerts.",
        False,
    ),
]


def upgrade() -> None:
    for key, value, description, is_secret in _KEYS:
        op.execute(
            f"""
            INSERT INTO app_config (key, value, description, is_secret)
            VALUES ({_lit(key)}, {_lit(value)}, {_lit(description)}, {str(is_secret).lower()})
            ON CONFLICT (key) DO NOTHING
            """
        )

    # Refresh the static-cred description so admins know they're only
    # consulted as the bootstrap identity when auth_method=assume_role.
    op.execute(
        """
        UPDATE app_config
        SET description = 'IAM access key id. In auth_method=static this is the '
                          'principal that signs every Secrets Manager call '
                          'directly. In auth_method=assume_role this is the '
                          'bootstrap identity that calls sts:AssumeRole — '
                          'it only needs sts:AssumeRole on the target role.'
        WHERE key = 'secret.awssm.access_key_id'
        """
    )


def downgrade() -> None:
    for key, _, _, _ in _KEYS:
        op.execute(f"DELETE FROM app_config WHERE key = {_lit(key)}")


def _lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"
