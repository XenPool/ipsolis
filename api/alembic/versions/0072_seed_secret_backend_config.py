"""Seed external secret-backend config keys.

External secret management lets credentials live in HashiCorp Vault or
CyberArk CCP/AIM instead of plaintext in ``app_config``. The
admin-facing change is small: a secret-typed config row whose ``value``
is a recognised reference scheme (``vault://...`` or ``ccp://...``) is
resolved to its real value at read time. Plain string values continue
to work unchanged (back-compat — a tenant who never enables a backend
behaves exactly like pre-slice tenants).

Slice 1 ships Vault and CCP. Conjur / AWS Secrets Manager / Azure
Key Vault stay queued.

Revision ID: 0072
Revises: 0071
Create Date: 2026-04-26
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0072"
down_revision: Union[str, None] = "0071"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, updated_at)
        VALUES
        -- Backend selector + cache
        ('secret.backend', 'db',
         'External secret backend. One of: db (plaintext in app_config — default), vault (HashiCorp Vault), ccp (CyberArk CCP/AIM).',
         false, NOW()),
        ('secret.cache_ttl_seconds', '60',
         'Resolved-secret cache TTL in seconds. Defaults to 60s — short enough that admins see fresh values quickly after rotating in the backend, long enough to avoid hammering Vault/CCP on every config read.',
         false, NOW()),

        -- HashiCorp Vault
        ('secret.vault.url', '',
         'Vault: base URL, e.g. https://vault.example.com:8200',
         false, NOW()),
        ('secret.vault.token', '',
         'Vault: static token used to authenticate. Slice 1 supports static tokens only; AppRole / k8s-jwt auth queue for slice 2.',
         true, NOW()),
        ('secret.vault.namespace', '',
         'Vault: optional Enterprise namespace (e.g. ipsolis/prod). Sent as X-Vault-Namespace.',
         false, NOW()),
        ('secret.vault.kv_mount', 'secret',
         'Vault: KV v2 mount point used to translate vault://<path> references. Default ''secret''.',
         false, NOW()),

        -- CyberArk CCP / AIM
        ('secret.ccp.url', '',
         'CCP: base URL of the AAM Web Service, e.g. https://ccp.example.com/AIMWebService',
         false, NOW()),
        ('secret.ccp.app_id', '',
         'CCP: AppID configured for ipSolis on the CyberArk side (Application that requested credentials).',
         false, NOW()),
        ('secret.ccp.safe', '',
         'CCP: default Safe used when a reference omits it (e.g. ccp://Object). Optional.',
         false, NOW()),
        ('secret.ccp.client_cert_pem', '',
         'CCP: optional client certificate (PEM, including key) for mTLS auth to the AAM Web Service. Stored as a secret. Slice 1 expects the cert + key concatenated together.',
         true, NOW()),
        ('secret.ccp.verify_tls', 'true',
         'CCP: verify the server TLS certificate. Defaults to true. Set false only for self-signed labs.',
         false, NOW()),

        -- Diagnostic surface — populated by the test endpoint
        ('secret.last_test_at', '',
         'Auto-managed — ISO timestamp of the last successful backend connection test.',
         false, NOW()),
        ('secret.last_test_error', '',
         'Auto-managed — last test-failure message (empty on success).',
         false, NOW())
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM app_config WHERE key IN (
          'secret.backend', 'secret.cache_ttl_seconds',
          'secret.vault.url', 'secret.vault.token',
          'secret.vault.namespace', 'secret.vault.kv_mount',
          'secret.ccp.url', 'secret.ccp.app_id', 'secret.ccp.safe',
          'secret.ccp.client_cert_pem', 'secret.ccp.verify_tls',
          'secret.last_test_at', 'secret.last_test_error'
        )
    """)
