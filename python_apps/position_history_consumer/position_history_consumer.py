"""
Position History Writer
=======================
Reads processed ship-position messages from the Kafka topic `shipslivedata`
and inserts them into the PostgreSQL table `position_history` — but only for
ships whose tracking is currently active, i.e. those that have a row in
`tracking_config` with `enabled_to IS NULL`.

The set of actively-tracked ship IDs is loaded from the database at startup
and refreshed every TRACKING_REFRESH_INTERVAL messages so that newly-enabled
or newly-disabled ships are picked up without restarting the process.

Expected message format (as produced by position_report_processor.py):
    {
        "ship_id":              int,
        "ship_name":            str,
        "course_over_ground":   float,
        "speed_over_ground":    float,
        "navigational_status":  str,
        "latitude":             float,
        "longitude":            float,
        "updated_at":           str   -- ISO-8601 UTC timestamp
    }

Configuration (environment variables):
    KAFKA_BOOTSTRAP_SERVERS       default: host.docker.internal:9093
    INPUT_TOPIC                   default: ships_live_data
    CONSUMER_GROUP_ID             default: position-history-writer
    DB_HOST                       default: localhost
    DB_PORT                       default: 5432
    DB_NAME                       default: postgres
    DB_USER                       default: postgres
    DB_PASSWORD                   default: (empty)
    BATCH_SIZE                    default: 500   -- rows per DB transaction
    TRACKING_REFRESH_INTERVAL     default: 10000 -- messages between refreshes
    AUTO_OFFSET_RESET             default: earliest -- 'earliest' or 'latest'
"""

import json
import logging
import os
import signal

import psycopg2
import psycopg2.extras
from kafka import KafkaConsumer

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "host.docker.internal:9093")
INPUT_TOPIC = os.getenv("INPUT_TOPIC", "ships_live_data")
CONSUMER_GROUP_ID = os.getenv("CONSUMER_GROUP_ID", "position-history-writer")

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "dbpass1234")

BATCH_SIZE = int(os.getenv("BATCH_SIZE", "500"))
# How often (in messages consumed) to re-query the tracking_config table
TRACKING_REFRESH_INTERVAL = int(os.getenv("TRACKING_REFRESH_INTERVAL", "10000"))
AUTO_OFFSET_RESET = os.getenv("AUTO_OFFSET_RESET", "earliest")

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------
INSERT_HISTORY_SQL = """
INSERT INTO position_history (
    ship_id,
    course_over_ground,
    speed_over_ground,
    navigational_status,    
    latitude,
    longitude,
    recorded_at
) VALUES (
    %(ship_id)s,
    %(course_over_ground)s,
    %(speed_over_ground)s,
    %(navigational_status)s,
    %(latitude)s,
    %(longitude)s,
    %(updated_at)s
);
"""

# Fetch all ship_ids that are currently being tracked (enabled_to IS NULL)
TRACKED_SHIPS_SQL = """
SELECT ship_id
FROM tracking_config
WHERE enabled_to IS NULL;
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
REQUIRED_FIELDS = {
    "ship_id",
    "course_over_ground",
    "speed_over_ground",
    "navigational_status",
    "latitude",
    "longitude",
    "updated_at",
}


def validate_message(msg: dict) -> tuple[bool, str]:
    """Return (True, "") when the message has all required fields."""
    missing = REQUIRED_FIELDS - msg.keys()
    if missing:
        return False, f"missing fields: {missing}"
    return True, ""


def load_tracked_ships(cursor) -> set[int]:
    """Query tracking_config and return the set of actively-tracked ship IDs."""
    cursor.execute(TRACKED_SHIPS_SQL)
    return {row[0] for row in cursor.fetchall()}


def flush_batch(cursor, batch: list[dict]) -> int:
    """
    Execute a batch insert and return the number of rows written.
    The caller is responsible for committing/rolling back the transaction.
    """
    psycopg2.extras.execute_batch(cursor, INSERT_HISTORY_SQL, batch)
    return len(batch)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log = logging.getLogger(__name__)

    running = True

    def _handle_signal(signum, frame):  # noqa: ANN001
        nonlocal running
        log.info("Received signal %d, shutting down…", signum)
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # -- PostgreSQL connection -----------------------------------------------
    log.info("Connecting to PostgreSQL %s:%d/%s …", DB_HOST, DB_PORT, DB_NAME)
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )
    conn.autocommit = False
    cursor = conn.cursor()
    # Separate cursor used only for tracking_config lookups so that its
    # SELECT queries never interfere with the open batch transaction.
    config_cursor = conn.cursor()
    log.info("PostgreSQL connection established.")

    # Load initial set of tracked ships
    tracked_ships = load_tracked_ships(config_cursor)
    log.info("Loaded %d actively-tracked ships from tracking_config.", len(tracked_ships))

    # -- Kafka consumer -------------------------------------------------------
    consumer = KafkaConsumer(
        INPUT_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        auto_offset_reset=AUTO_OFFSET_RESET,
        enable_auto_commit=True,
        group_id=CONSUMER_GROUP_ID,
    )

    log.info(
        "Position history writer started. Reading from '%s', writing to 'position_history'.",
        INPUT_TOPIC,
    )

    batch: list[dict] = []
    written = 0
    skipped = 0
    messages_since_refresh = 0

    try:
        for kafka_msg in consumer:
            if not running:
                break

            messages_since_refresh += 1

            # Periodically refresh the tracked-ships set
            if messages_since_refresh >= TRACKING_REFRESH_INTERVAL:
                tracked_ships = load_tracked_ships(config_cursor)
                log.info(
                    "Refreshed tracking config: %d actively-tracked ships.",
                    len(tracked_ships),
                )
                messages_since_refresh = 0

            msg = kafka_msg.value

            valid, reason = validate_message(msg)
            if not valid:
                skipped += 1
                log.debug("Skipped message: %s", reason)
                continue

            if msg["ship_id"] not in tracked_ships:
                skipped += 1
                log.debug("Skipped ship_id=%s: not in tracking_config with enabled_to IS NULL", msg["ship_id"])
                continue

            batch.append(msg)

            if len(batch) >= BATCH_SIZE:
                try:
                    written += flush_batch(cursor, batch)
                    conn.commit()
                    log.info("Flushed batch: written=%d  skipped=%d", written, skipped)
                except Exception:
                    conn.rollback()
                    log.exception("Batch write failed, rolled back.")
                finally:
                    batch.clear()

    finally:
        # Flush any remaining messages before exit
        if batch:
            try:
                written += flush_batch(cursor, batch)
                conn.commit()
            except Exception:
                conn.rollback()
                log.exception("Final batch write failed, rolled back.")

        cursor.close()
        config_cursor.close()
        conn.close()
        consumer.close()
        log.info("Shutdown complete. written=%d  skipped=%d", written, skipped)


if __name__ == "__main__":
    main()
