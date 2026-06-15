-- Let users mark which item of a type is their default/active one (e.g. the mask
-- currently in use), independent of start-date inference. When a default is set,
-- "equipment this night" inference prefers it; otherwise it falls back to the
-- most recent item by start_date (unchanged behavior).
BEGIN;

ALTER TABLE user_equipment
    ADD COLUMN is_default BOOLEAN NOT NULL DEFAULT false;

-- At most one default per equipment type per user.
CREATE UNIQUE INDEX uq_user_equipment_default
    ON user_equipment (user_id, equipment_type)
    WHERE is_default;

COMMIT;
