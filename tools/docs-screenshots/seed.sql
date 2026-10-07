-- Demo data for the docs screenshots (orders for John Doe, API tokens). Every
-- row carries the marker below so cleanup.sql can
-- remove exactly what this file added. Assets are only *referenced* (name
-- shown in My IT) — asset_pool rows are not modified.
-- expiry_reminder_sent_at = now() keeps the expiry job from mailing John.

\set marker '''docs-screenshot-demo'''
\set who '''john@xenpool.de'''
\set name '''John Doe'''

INSERT INTO orders (user_email, user_name, owner_email, owner_name, asset_type_id, assigned_asset_id,
                    requested_from, requested_until, status, config, justification,
                    expiry_reminder_sent_at, requester_department, created_at)
SELECT :who, :name, :who, :name, t.id, a.id,
       now() - (v.age_days || ' days')::interval, now() + (v.left_days || ' days')::interval + interval '1 hour',
       'delivered', '{}', :marker, now(), 'Engineering', now() - (v.age_days || ' days')::interval
FROM (VALUES ('Linux Build Server', 'BLD-LX01', 60, 11),
             ('Developer Workstation', 'WinDEV01', 20, 74),
             ('Executive Laptop', 'LAP-EX01', 30, 299)) AS v(type_name, asset_name, age_days, left_days)
JOIN asset_types t ON t.name = v.type_name
JOIN asset_pool a ON a.name = v.asset_name;

-- Running order in provisioning: step 3 of 5 in progress.
WITH o AS (
  INSERT INTO orders (user_email, user_name, owner_email, owner_name, asset_type_id,
                      requested_from, requested_until, status, config, justification, requester_department, created_at)
  SELECT :who, :name, :who, :name, id, now(), now() + interval '90 days', 'provisioning', '{}', :marker,
         'Engineering', now() - interval '25 minutes'
  FROM asset_types WHERE name = 'Virtual Test Client'
  RETURNING id
)
INSERT INTO order_steps (order_id, step_name, status, started_at, finished_at)
SELECT o.id, s.step_name, s.status::step_status,
       CASE WHEN s.status <> 'pending' THEN now() - interval '20 minutes' END,
       CASE WHEN s.status = 'success' THEN now() - interval '10 minutes' END
FROM o, (VALUES (1, 'Reserve asset', 'success'),
                (2, 'Clone VM from template', 'success'),
                (3, 'Join domain', 'running'),
                (4, 'Install software', 'pending'),
                (5, 'Notify user', 'pending')) AS s(n, step_name, status)
ORDER BY s.n;

-- Running order waiting for approval.
INSERT INTO orders (user_email, user_name, owner_email, owner_name, asset_type_id,
                    requested_from, requested_until, status, config, justification, requester_department, created_at)
SELECT :who, :name, :who, :name, id, now() + interval '2 days', now() + interval '60 days', 'pending_approval', '{}',
       :marker, 'Engineering', now() - interval '1 day'
FROM asset_types WHERE name = 'Design Workstation (GPU)';

SELECT id, status FROM orders WHERE justification = :marker ORDER BY id;

-- Three realistic API tokens for the API-tokens page (newest first, so they top
-- the list). token_hash is random — no plaintext token exists that matches it.
INSERT INTO api_tokens (name, token_hash, token_prefix, scopes, created_by, created_at, last_used_at, expires_at, role)
VALUES
  ('ServiceNow integration', md5(random()::text), 'xpat_sN4q', '["orders:read", "approvals:write"]', :marker,
   now() - interval '40 days', now() - interval '2 hours', now() + interval '325 days', 'approver'),
  ('HR leaver webhook', md5(random()::text), 'xpat_hR7k', '["hr:leaver"]', :marker,
   now() - interval '25 days', now() - interval '1 day', NULL, NULL),
  ('Grafana cost dashboard', md5(random()::text), 'xpat_gF2m', '["orders:read", "audit:read"]', :marker,
   now() - interval '10 days', now() - interval '15 minutes', now() + interval '80 days', 'auditor');
