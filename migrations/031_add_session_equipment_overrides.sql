-- Per-night equipment override. Lets a user correct what they actually used on a
-- given night — pick a specific registered item, or mark a type as "not used"
-- that night — overriding the start-date/default inference.
--
-- Shape: { "<equipment_type>": "<user_equipment.id>" | "none" }. An absent key
-- means "no override — use the inferred item". Stored as JSONB (not the dead
-- per-type FK columns from migration 009, which can't express "not used" and
-- don't cover headgear).
BEGIN;

ALTER TABLE sessions
    ADD COLUMN equipment_overrides JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMIT;
