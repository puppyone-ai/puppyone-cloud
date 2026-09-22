-- pgTAP adapter for the portable post-deploy schema contracts.
--
-- The assertion body lives below _support so pg_prove mounts it without
-- discovering it as a standalone TAP test. Hosted staging/production execute
-- that same body with plain psql and no pgTAP dependency.

SELECT plan(1);
\ir _support/schema_contracts.inc
SELECT pass('portable schema smoke contracts completed without an exception');
SELECT * FROM finish();
