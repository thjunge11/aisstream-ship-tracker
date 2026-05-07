"""
Live Ship Data DB Writer
========================
Reads processed ship-position messages from the Kafka topic `ships_live_data`
and upserts them into the PostgreSQL table `ships_live_data`.

A row with the same `ship_id` (MMSI) is updated in place; new ships are
inserted.  Messages are committed to the database in configurable batches
to keep write amplification low while still providing near-real-time updates.

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
    KAFKA_BOOTSTRAP_SERVERS  default: host.docker.internal:9093
    INPUT_TOPIC              default: ships_live_data
    CONSUMER_GROUP_ID        default: live-data-writer
    DB_HOST                  default: localhost
    DB_PORT                  default: 5432
    DB_NAME                  default: postgres
    DB_USER                  default: postgres
    DB_PASSWORD              default: (empty)
    BATCH_SIZE               default: 500   -- rows per DB transaction
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
CONSUMER_GROUP_ID = os.getenv("CONSUMER_GROUP_ID", "live-data-writer")

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "dbpass1234")

BATCH_SIZE = int(os.getenv("BATCH_SIZE", "500"))

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------
UPSERT_SQL = """
INSERT INTO ships_live_data (
    ship_id,
    ship_name,
    course_over_ground,
    speed_over_ground,
    navigational_status,
    latitude,
    longitude,
    updated_at
) VALUES (
    %(ship_id)s,
    %(ship_name)s,
    %(course_over_ground)s,
    %(speed_over_ground)s,
    %(navigational_status)s,
    %(latitude)s,
    %(longitude)s,
    %(updated_at)s
)
ON CONFLICT (ship_id) DO UPDATE SET
    ship_name           = EXCLUDED.ship_name,
    course_over_ground  = EXCLUDED.course_over_ground,
    speed_over_ground   = EXCLUDED.speed_over_ground,
    navigational_status = EXCLUDED.navigational_status,
    latitude            = EXCLUDED.latitude,
    longitude           = EXCLUDED.longitude,
    updated_at          = EXCLUDED.updated_at;
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
REQUIRED_FIELDS = {
    "ship_id",
    "ship_name",
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


def flush_batch(cursor, batch: list[dict], log: logging.Logger) -> int:
    """
    Execute a batch upsert and return the number of rows written.
    The caller is responsible for committing/rolling back the transaction.
    """
    psycopg2.extras.execute_batch(cursor, UPSERT_SQL, batch)
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
    log.info("PostgreSQL connection established.")

    # -- Kafka consumer -------------------------------------------------------
    consumer = KafkaConsumer(
        INPUT_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        group_id=CONSUMER_GROUP_ID,
    )

    log.info(
        "Live data DB writer started. Reading from '%s', writing to 'ships_live_data'.",
        INPUT_TOPIC,
    )

    batch: list[dict] = []
    written = 0
    skipped = 0

    try:
        for kafka_msg in consumer:
            if not running:
                break

            msg = kafka_msg.value

            valid, reason = validate_message(msg)
            if not valid:
                skipped += 1
                log.info("Skipped message: %s", reason)
                continue

            batch.append(msg)

            if len(batch) >= BATCH_SIZE:
                try:
                    written += flush_batch(cursor, batch, log)
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
                written += flush_batch(cursor, batch, log)
                conn.commit()
            except Exception:
                conn.rollback()
                log.exception("Final batch write failed, rolled back.")

        cursor.close()
        conn.close()
        consumer.close()
        log.info("Shutdown complete. written=%d  skipped=%d", written, skipped)


if __name__ == "__main__":
    main()
