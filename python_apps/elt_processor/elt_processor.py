"""
Kafka Position Report Processor
================================
Reads PositionReport messages from the Kafka topic `positionreports`,
validates them, and writes the transformed data — ready for the
`ships_live_data` table — to the Kafka topic `ships_live_data`.

Input message format (topic: positionreports):
    See PositionReport.json for the full structure.

Output message format (topic: ships_live_data):
    {
        "ship_id":              int,   -- MMSI (9 digits)
        "ship_name":            str,
        "course_over_ground":   float, -- degrees
        "speed_over_ground":    float, -- knots
        "navigational_status":  str,   -- human-readable AIS status
        "latitude":             float, -- decimal degrees
        "longitude":            float, -- decimal degrees
        "updated_at":           str    -- ISO-8601 timestamp (UTC)
    }
"""

import json
import logging
from datetime import datetime, timezone
from kafka import KafkaConsumer, KafkaProducer
import os
import signal
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration – override via environment variables or edit here directly
# ---------------------------------------------------------------------------
load_dotenv("../.env")
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "host.docker.internal:9093")
INPUT_TOPIC = os.getenv("INPUT_TOPIC", "PositionReport")
OUTPUT_TOPIC = os.getenv("LIVE_DATA_TOPIC", "ships_live_data")
CONSUMER_GROUP_ID = os.getenv("ELT_CONSUMER_GROUP_ID", "elt-processor")

# ---------------------------------------------------------------------------
# AIS NavigationalStatus code → human-readable string (ITU-R M.1371-5)
# ---------------------------------------------------------------------------
NAV_STATUS_NOT_DEFINED = 15  # AIS default / not available value

NAVIGATIONAL_STATUS: dict[int, str] = {
    0: "Under way using engine",
    1: "At anchor",
    2: "Not under command",
    3: "Restricted manoeuvrability",
    4: "Constrained by her draught",
    5: "Moored",
    6: "Aground",
    7: "Engaged in fishing",
    8: "Under way sailing",
    9: "Reserved for future use",
    10: "Reserved for future use",
    11: "Power-driven vessel towing astern",
    12: "Power-driven vessel pushing ahead or towing alongside",
    13: "Reserved for future use",
    14: "AIS-SART is active",
    15: "Not defined",
}

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
# AIS "not available" sentinel values
_LAT_NOT_AVAILABLE = 91.0
_LON_NOT_AVAILABLE = 181.0
_COG_NOT_AVAILABLE = 360.0
# AIS spec: SOG >= 102.3 knots means "not available / not applicable"
_SOG_NOT_AVAILABLE = 102.3


def validate_position_report(msg: dict) -> tuple[bool, str]:
    """
    Validate a raw PositionReport Kafka message.

    Returns (True, "") on success or (False, <reason>) on failure.
    """
    try:
        pr = msg["Message"]["PositionReport"]
        meta = msg["MetaData"]
    except (KeyError, TypeError):
        return False, "missing Message.PositionReport or MetaData"

    # AIS Valid flag
    if not pr.get("Valid", False):
        return False, "Valid flag is False"

    # MMSI must be a valid 9-digit maritime identifier
    mmsi = meta.get("MMSI")
    if not isinstance(mmsi, int) or not (100_000_000 <= mmsi <= 999_999_999):
        return False, f"invalid MMSI: {mmsi!r}"

    # Latitude range check (-90..90); 91.0 = AIS "not available"
    lat = pr.get("Latitude")
    if lat is None or lat == _LAT_NOT_AVAILABLE or not (-90.0 <= lat <= 90.0):
        return False, f"invalid Latitude: {lat!r}"

    # Longitude range check (-180..180); 181.0 = AIS "not available"
    lon = pr.get("Longitude")
    if lon is None or lon == _LON_NOT_AVAILABLE or not (-180.0 <= lon <= 180.0):
        return False, f"invalid Longitude: {lon!r}"

    # COG: 360.0 = AIS "not available"
    cog = pr.get("Cog")
    if cog is not None and cog == _COG_NOT_AVAILABLE:
        return False, "COG not available"

    # SOG: 102.3+ = AIS "not available"
    sog = pr.get("Sog")
    if sog is not None and sog >= _SOG_NOT_AVAILABLE:
        return False, f"SOG not available: {sog!r}"
    
    # Ship name: Filter out empty or whitespace-only names
    ship_name = meta.get("ShipName", "").strip()
    if not ship_name:
        return False, "empty or whitespace-only ShipName"
    
    # Ship name: Filter out "unknown" names (case-insensitive)
    if ship_name.lower() == "unknown":
        return False, "ShipName is 'Unknown'"
    
    # Ship name: Filter out names with only non alphanumerics characters
    if ship_name and not any(c.isalnum() for c in ship_name):
        return False, "ShipName has no alphanumeric characters"    

    return True, ""


# ---------------------------------------------------------------------------
# Transformation
# ---------------------------------------------------------------------------

def _parse_time_utc(time_utc_raw: str) -> str:
    """
    Parse the MetaData.time_utc string to an ISO-8601 UTC timestamp.

    The source format is: "2026-04-24 12:29:45.191749062 +0000 UTC"
    Python's datetime only supports up to microsecond precision, so
    nanoseconds are truncated.
    """
    try:
        # Keep everything up to (but not including) the timezone marker
        dt_part = time_utc_raw.split("+")[0].strip()
        if "." in dt_part:
            base, frac = dt_part.rsplit(".", 1)
            frac = frac[:6]  # truncate nanoseconds → microseconds
            dt_part = f"{base}.{frac}"
        dt = datetime.strptime(dt_part, "%Y-%m-%d %H:%M:%S.%f").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, AttributeError) as exc:
        logging.getLogger(__name__).warning(
            "Could not parse time_utc %r, using current UTC time: %s",
            time_utc_raw,
            exc,
        )
        dt = datetime.now(timezone.utc)
    return dt.isoformat()


def transform_to_ships_live_data(msg: dict) -> dict:
    """
    Transform a validated PositionReport message into the ships_live_data
    column format.
    """
    pr = msg["Message"]["PositionReport"]
    meta = msg["MetaData"]

    nav_status_int = pr.get("NavigationalStatus", NAV_STATUS_NOT_DEFINED)
    nav_status_str = NAVIGATIONAL_STATUS.get(nav_status_int, "Not defined")

    # Prefer the (already-rounded) MetaData coordinates; fall back to the
    # full-precision PositionReport values.  Explicit None checks are required
    # so that the valid coordinate value 0.0 (equator / prime meridian) is not
    # treated as falsy.
    meta_lat = meta.get("latitude")
    latitude = meta_lat if meta_lat is not None else pr.get("Latitude")
    meta_lon = meta.get("longitude")
    longitude = meta_lon if meta_lon is not None else pr.get("Longitude")

    return {
        "ship_id": meta["MMSI"],
        "ship_name": meta.get("ShipName", "").strip(),
        "course_over_ground": pr.get("Cog", 0),
        "speed_over_ground": pr.get("Sog", 0),
        "navigational_status": nav_status_str,
        "latitude": latitude,
        "longitude": longitude,
        "updated_at": _parse_time_utc(meta.get("time_utc", "")),
    }


# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log = logging.getLogger(__name__)

    # Graceful shutdown flag
    running = True

    def _handle_signal(signum, frame):  # noqa: ANN001
        nonlocal running
        log.info("Received signal %d, shutting down…", signum)
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    consumer = KafkaConsumer(
        INPUT_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        group_id=CONSUMER_GROUP_ID,
    )

    producer = KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )

    log.info(
        "Position report processor started. Reading from '%s', writing to '%s'.",
        INPUT_TOPIC,
        OUTPUT_TOPIC,
    )

    processed = 0
    skipped = 0

    try:
        for kafka_msg in consumer:
            if not running:
                break

            msg = kafka_msg.value

            valid, reason = validate_position_report(msg)
            if not valid:
                skipped += 1
                log.info(
                    "Skipped message: %s",
                    reason,
                )
                continue

            ships_live = transform_to_ships_live_data(msg)
            producer.send(OUTPUT_TOPIC, ships_live)
            processed += 1

            if processed % 1000 == 0:
                producer.flush()
                log.info(
                    "Progress: processed=%d  skipped=%d", processed, skipped
                )
    finally:
        producer.flush()
        producer.close()
        consumer.close()
        log.info("Shutdown complete. processed=%d  skipped=%d", processed, skipped)


if __name__ == "__main__":
    main()
