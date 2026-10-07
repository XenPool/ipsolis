-- Removes everything seed.sql added (matched by the justification marker).
DELETE FROM order_steps WHERE order_id IN (SELECT id FROM orders WHERE justification = 'docs-screenshot-demo');
DELETE FROM orders WHERE justification = 'docs-screenshot-demo';
DELETE FROM api_tokens WHERE created_by = 'docs-screenshot-demo';
