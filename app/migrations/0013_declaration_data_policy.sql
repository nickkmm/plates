-- Personal-data authorization (Ley 1581 de 2012) collected on the public
-- declaration form: when the declarant accepted the data-processing text, and
-- whether they agreed to receive commercial information (TRUE = SÍ, FALSE =
-- NO). Both NULL on declarations made before the form asked for them (those
-- people never signed this authorization). Safe to re-run; same end schema as
-- init.sql.

ALTER TABLE declarations ADD COLUMN IF NOT EXISTS accepted_data_policy_at TIMESTAMP;
ALTER TABLE declarations ADD COLUMN IF NOT EXISTS accepts_marketing BOOLEAN;
