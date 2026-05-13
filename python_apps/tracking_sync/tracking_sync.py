"""
Tracking Sync
=============
Reads tracked_ship_ids.json and reconciles it against the tracking_config
table in PostgreSQL:

  - Ships in the JSON that are NOT actively tracked  → open a new tracking row
                                                        (INSERT INTO tracking_config)
  - Ships actively tracked that are NOT in the JSON  → close the tracking row
                                                        (SET enabled_to = NOW())
  - Ships present in both                            → already active, no-op

A ship must exist in ships_live_data before it can be tracked (FK constraint).
Ships from the JSON that are not yet in ships_live_data are skipped with a
warning; they will be picked up on the next run once they appear in the stream.

Configuration (environment variables):
    TRACKED_SHIP_IDS_PATH   path to the JSON config file
                            default: /config/tracked_ship_ids.json
    DB_HOST                 default: localhost
    DB_PORT                 default: 5432
    DB_NAME                 default: postgres
    DB_USER                 default: postgres
    DB_PASSWORD             default: (empty)
"""

import json
import logging
import os
import sys

import psycopg2

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(os.path.splitext(os.path.basename(__file__))[0])

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TRACKED_SHIP_IDS_PATH = os.getenv("TRACKED_SHIP_IDS_PATH", "/config/tracked_ship_ids.json")

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------
ACTIVE_TRACKED_SQL = """
SELECT DISTINCT ship_id
FROM tracking_config
WHERE enabled_to IS NULL;
"""

EXISTING_IN_LIVE_DATA_SQL = """
SELECT ship_id
FROM ships_live_data
WHERE ship_id = ANY(%s);
"""

ENABLE_TRACKING_SQL = """
INSERT INTO tracking_config (ship_id, enabled_from)
VALUES (%s, NOW())
ON CONFLICT DO NOTHING;
"""

DISABLE_TRACKING_SQL = """
UPDATE tracking_config
SET enabled_to = NOW()
WHERE ship_id = %s
  AND enabled_to IS NULL;
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_desired_ids(path: str) -> set:
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        log.error("Config file not found: %s", path)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        log.error("Invalid JSON in %s: %s", path, exc)
        sys.exit(1)
    return {int(sid) for sid in data["tracked_ship_ids"]}


def get_active_ids(conn) -> set:
    with conn.cursor() as cur:
        cur.execute(ACTIVE_TRACKED_SQL)
        return {row[0] for row in cur.fetchall()}


def filter_existing_in_live_data(conn, ids: set) -> set:
    """Return the subset of ids that have a row in ships_live_data (FK check)."""
    if not ids:
        return set()
    with conn.cursor() as cur:
        cur.execute(EXISTING_IN_LIVE_DATA_SQL, (list(ids),))
        return {row[0] for row in cur.fetchall()}


def enable_tracking(conn, ship_ids: set) -> None:
    with conn.cursor() as cur:
        for ship_id in sorted(ship_ids):
            cur.execute(ENABLE_TRACKING_SQL, (ship_id,))
            log.info("  ✓ Enabled  tracking for ship_id=%s", ship_id)
    conn.commit()


def disable_tracking(conn, ship_ids: set) -> None:
    with conn.cursor() as cur:
        for ship_id in sorted(ship_ids):
            cur.execute(DISABLE_TRACKING_SQL, (ship_id,))
            log.info("  ✗ Disabled tracking for ship_id=%s", ship_id)
    conn.commit()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("=== Tracking Sync started ===")
    log.info("Config file: %s", TRACKED_SHIP_IDS_PATH)

    desired_ids = load_desired_ids(TRACKED_SHIP_IDS_PATH)
    log.info("Desired ship IDs (%d): %s", len(desired_ids), sorted(desired_ids))

    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )
    try:
        active_ids = get_active_ids(conn)
        log.info("Currently active in DB (%d): %s", len(active_ids), sorted(active_ids))

        to_enable = desired_ids - active_ids
        to_disable = active_ids - desired_ids

        # ── Enable ──────────────────────────────────────────────────────────
        if to_enable:
            # Only ships already seen in the live stream can be tracked (FK)
            known = filter_existing_in_live_data(conn, to_enable)
            unknown = to_enable - known
            if unknown:
                log.warning(
                    "Skipping %s — not yet in ships_live_data; "
                    "will be enabled on the next run once data arrives.",
                    sorted(unknown),
                )
            if known:
                log.info("Enabling tracking for %d ship(s):", len(known))
                enable_tracking(conn, known)
        else:
            log.info("No new ships to enable.")

        # ── Disable ─────────────────────────────────────────────────────────
        if to_disable:
            log.info("Disabling tracking for %d ship(s):", len(to_disable))
            disable_tracking(conn, to_disable)
        else:
            log.info("No ships to disable.")

        log.info("=== Sync complete ===")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
