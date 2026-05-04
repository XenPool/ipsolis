-- Pre-populate Admin UI → Settings with values pointing at the testlab
-- (Vault, mock-receiver, rsyslog). Idempotent: safe to re-run.
--
-- Run from the host:
--   docker exec -i xp_postgres psql -U xpuser -d ipsolis < testlab/seed-settings.sql
--
-- Notes:
--   • Endpoints use ``host.docker.internal`` so api / worker containers
--     can reach the testlab stack running on the same Docker host.
--   • SIEM is pointed at the mock-receiver ``/splunk`` endpoint by
--     default. To switch to real Splunk, change ``siem.endpoint_url`` to
--     ``http://host.docker.internal:8088/services/collector`` and bring
--     up the splunk profile of docker-compose.testlab.yml.
--   • ``secret.backend`` is left unchanged — this script seeds the Vault
--     connection details so the operator can flip the backend in the
--     UI when ready, but does not switch the resolver itself (which
--     would orphan existing DB-stored secrets).

\set ON_ERROR_STOP on

DO $$
DECLARE
    -- (key, value, is_secret, description) — kept as a CTE-style array so
    -- the upsert below is one statement. Description is non-secret prose
    -- shown in the Settings UI.
    v RECORD;
BEGIN
    FOR v IN
        SELECT * FROM (VALUES
            -- ── SIEM streaming (Splunk HEC mode, pointing at mock) ───
            ('siem.enabled',                  'true',                                              false, 'Testlab: stream every audit_log row to the mock-receiver every minute.'),
            ('siem.format',                   'splunk_hec',                                        false, 'Testlab: Splunk HEC adapter (mock receiver mimics the HEC reply).'),
            ('siem.endpoint_url',             'http://host.docker.internal:9000/splunk',           false, 'Testlab: mock-receiver Splunk endpoint. Switch to :8088 once Splunk profile is up.'),
            ('siem.token',                    'testlab-mock-token',                                true,  'Testlab: any value works against the mock receiver.'),
            ('siem.batch_size',               '200',                                               false, 'Testlab: max audit rows per minute Beat tick.'),
            ('siem.verify_tls',               'false',                                             false, 'Testlab: lab uses http (no TLS) so verification is a no-op.'),
            ('siem.log_type',                 'IpsolisAudit',                                      false, 'Testlab: Sentinel custom-table name (used only when format=sentinel).'),
            ('siem.webhook_url',              'http://host.docker.internal:9000/generic',          false, 'Testlab: generic-HMAC sink on the mock receiver.'),
            ('siem.webhook_secret',           'testlab-webhook-secret',                            true,  'Testlab: HMAC secret for the generic webhook adapter.'),
            ('siem.webhook_signature_header', 'X-Hub-Signature-256',                               false, 'Testlab: signature header name (matches GitHub-style receivers).'),

            -- ── Secrets backend (Vault connection — backend NOT flipped) ─
            ('secret.vault.url',              'http://host.docker.internal:8200',                  false, 'Testlab: HashiCorp Vault dev-mode endpoint.'),
            ('secret.vault.token',            'testlab-root-token',                                true,  'Testlab: Vault dev root token (set by VAULT_DEV_ROOT_TOKEN_ID).'),
            ('secret.vault.kv_mount',         'secret',                                            false, 'Testlab: KV v2 mount auto-created by Vault dev mode.'),
            ('secret.vault.namespace',        '',                                                  false, 'Testlab: dev mode is single-namespace; leave empty.'),

            -- ── Teams notifications (point at mock; mode left disabled) ──
            ('teams.mode',                    'disabled',                                          false, 'Testlab: leave disabled until you want to verify card formatting.'),
            ('teams.webhook_url',             'http://host.docker.internal:9000/teams',            true,  'Testlab: mock-receiver Teams webhook URL.')
        ) AS t(key, value, is_secret, description)
    LOOP
        INSERT INTO app_config (key, value, is_secret, description)
        VALUES (v.key, v.value, v.is_secret, v.description)
        ON CONFLICT (key) DO UPDATE
            SET value       = EXCLUDED.value,
                is_secret   = EXCLUDED.is_secret,
                description = EXCLUDED.description,
                updated_at  = now();
    END LOOP;
END
$$;

-- Show what landed so the operator can eyeball it.
SELECT key,
       CASE WHEN is_secret THEN '***' ELSE value END AS value,
       is_secret
FROM app_config
WHERE key LIKE 'siem.%' OR key LIKE 'secret.vault.%' OR key LIKE 'teams.%'
ORDER BY key;
