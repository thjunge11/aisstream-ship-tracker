"""
Ship Static Data Processor
===========================
Reads ShipStaticData messages from the Kafka topic `ShipStaticData`,
validates them, and writes the transformed, human-readable data to the
Kafka topic `ship_static_data`.

Input message format (topic: ShipStaticData):
    See ShipStaticData.json for the full structure.

Output message format (topic: ship_static_data):
    {
        "ship_id":      int,    -- MMSI (9 digits)
        "ship_name":    str,    -- trimmed
        "call_sign":    str,    -- trimmed
        "imo_number":   int,
        "ship_type":    str,    -- human-readable AIS ship type
        "destination":  str,    -- trimmed
        "eta":          str,    -- "MM-DD HH:MM UTC" (no year in AIS spec)
        "length_m":     int,    -- bow-to-stern  (Dimension.A + Dimension.B)
        "width_m":      int,    -- port-to-starboard (Dimension.C + Dimension.D)
        "draught_m":    float,  -- MaximumStaticDraught
        "updated_at":   str     -- ISO-8601 UTC timestamp
    }

Configuration (environment variables):
    KAFKA_BOOTSTRAP_SERVERS             default: host.docker.internal:9093
    SHIP_STATIC_DATA_INPUT_TOPIC        default: ShipStaticData
    SHIP_STATIC_DATA_OUTPUT_TOPIC       default: ship_static_data
    SHIP_STATIC_DATA_CONSUMER_GROUP_ID  default: ship-static-data-processor
"""

import json
import logging
import os
import signal
from datetime import datetime, timezone
from kafka import KafkaConsumer, KafkaProducer

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "host.docker.internal:9093")
INPUT_TOPIC = os.getenv("SHIP_STATIC_DATA_INPUT_TOPIC", "ShipStaticData")
OUTPUT_TOPIC = os.getenv("SHIP_STATIC_DATA_OUTPUT_TOPIC", "ship_static_data")
CONSUMER_GROUP_ID = os.getenv("SHIP_STATIC_DATA_CONSUMER_GROUP_ID", "ship-static-data-processor")

# ---------------------------------------------------------------------------
# AIS Ship Type code → human-readable string (ITU-R M.1371-5, Table 51)
# ---------------------------------------------------------------------------
SHIP_TYPE: dict[int, str] = {
    0: "Not available",
    20: "Wing in ground",
    21: "Wing in ground – hazardous A",
    22: "Wing in ground – hazardous B",
    23: "Wing in ground – hazardous C",
    24: "Wing in ground – hazardous D",
    30: "Fishing",
    31: "Towing",
    32: "Towing (length >200 m or breadth >25 m)",
    33: "Dredging / underwater ops",
    34: "Diving ops",
    35: "Military ops",
    36: "Sailing",
    37: "Pleasure craft",
    40: "High speed craft",
    41: "High speed craft – hazardous A",
    42: "High speed craft – hazardous B",
    43: "High speed craft – hazardous C",
    44: "High speed craft – hazardous D",
    49: "High speed craft",
    50: "Pilot vessel",
    51: "Search and rescue vessel",
    52: "Tug",
    53: "Port tender",
    54: "Anti-pollution equipment",
    55: "Law enforcement",
    58: "Medical transport",
    59: "Noncombatant ship",
    60: "Passenger",
    61: "Passenger – hazardous A",
    62: "Passenger – hazardous B",
    63: "Passenger – hazardous C",
    64: "Passenger – hazardous D",
    69: "Passenger",
    70: "Cargo",
    71: "Cargo – hazardous A",
    72: "Cargo – hazardous B",
    73: "Cargo – hazardous C",
    74: "Cargo – hazardous D",
    79: "Cargo",
    80: "Tanker",
    81: "Tanker – hazardous A",
    82: "Tanker – hazardous B",
    83: "Tanker – hazardous C",
    84: "Tanker – hazardous D",
    89: "Tanker",
    90: "Other",
    91: "Other – hazardous A",
    92: "Other – hazardous B",
    93: "Other – hazardous C",
    94: "Other – hazardous D",
    99: "Other",
}

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_ship_static_data(msg: dict) -> tuple[bool, str]:
    """Return (True, "") on success, or (False, <reason>) on failure."""
    if msg.get("MessageType") != "ShipStaticData":
        return False, f"unexpected MessageType: {msg.get('MessageType')!r}"

    try:
        ssd = msg["Message"]["ShipStaticData"]
        meta = msg["MetaData"]
    except (KeyError, TypeError):
        return False, "missing Message.ShipStaticData or MetaData"

    if not ssd.get("Valid", False):
        return False, "Valid flag is False"

    mmsi = meta.get("MMSI")
    if not isinstance(mmsi, int) or not (100_000_000 <= mmsi <= 999_999_999):
        return False, f"invalid MMSI: {mmsi!r}"

    name = ssd.get("Name", "").strip()
    if not name:
        return False, "empty or whitespace-only Name"
    if name.lower() == "unknown":
        return False, "Name is 'Unknown'"
    if not any(c.isalnum() for c in name):
        return False, "Name has no alphanumeric characters"

    return True, ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decode_ship_type(type_code: int) -> str:
    """Resolve an AIS ship-type integer to a human-readable label."""
    if type_code in SHIP_TYPE:
        return SHIP_TYPE[type_code]
    # Ranges not individually listed
    if 1 <= type_code <= 19:
        return "Reserved"
    if 25 <= type_code <= 29:
        return "Wing in ground – reserved"
    if 38 <= type_code <= 39:
        return "Reserved"
    if 45 <= type_code <= 48:
        return "High speed craft – reserved"
    if 56 <= type_code <= 57:
        return "Local vessel"
    if 65 <= type_code <= 68:
        return "Passenger – reserved"
    if 75 <= type_code <= 78:
        return "Cargo – reserved"
    if 85 <= type_code <= 88:
        return "Tanker – reserved"
    if 95 <= type_code <= 98:
        return "Other – reserved"
    return "Not available"


def _format_eta(eta: dict) -> str | None:
    """
    Format an AIS ETA dict {Month, Day, Hour, Minute} as 'MM-DD HH:MM UTC'.
    Returns None when the ETA is all-zero (not available).
    """
    month = eta.get("Month", 0)
    day = eta.get("Day", 0)
    hour = eta.get("Hour", 24)   # 24 = not available in AIS
    minute = eta.get("Minute", 60)  # 60 = not available in AIS
    if month == 0 and day == 0:
        return None
    hour_str = "--" if hour == 24 else f"{hour:02d}"
    minute_str = "--" if minute == 60 else f"{minute:02d}"
    return f"{month:02d}-{day:02d} {hour_str}:{minute_str} UTC"


def _parse_time_utc(time_utc_raw: str) -> str:
    """
    Parse MetaData.time_utc ("2026-05-08 09:01:02.989422947 +0000 UTC")
    into an ISO-8601 UTC string, truncating nanoseconds to microseconds.
    """
    try:
        dt_part = time_utc_raw.split("+")[0].strip()
        if "." in dt_part:
            base, frac = dt_part.rsplit(".", 1)
            dt_part = f"{base}.{frac[:6]}"
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


# ---------------------------------------------------------------------------
# Transformation
# ---------------------------------------------------------------------------

def transform_to_ship_static_data(msg: dict) -> dict:
    """Transform a validated ShipStaticData message into the output format."""
    ssd = msg["Message"]["ShipStaticData"]
    meta = msg["MetaData"]

    dim = ssd.get("Dimension", {})
    length_m = (dim.get("A") or 0) + (dim.get("B") or 0)
    width_m = (dim.get("C") or 0) + (dim.get("D") or 0)

    return {
        "ship_id": meta["MMSI"],
        "ship_name": ssd.get("Name", "").strip(),
        "call_sign": ssd.get("CallSign", "").strip(),
        "imo_number": ssd.get("ImoNumber"),
        "ship_type": _decode_ship_type(ssd.get("Type", 0)),
        "destination": ssd.get("Destination", "").strip(),
        "eta": _format_eta(ssd.get("Eta") or {}),
        "length_m": length_m if length_m > 0 else None,
        "width_m": width_m if width_m > 0 else None,
        "draught_m": ssd.get("MaximumStaticDraught"),
        "updated_at": _parse_time_utc(meta.get("time_utc", "")),
    }


# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger(os.path.splitext(os.path.basename(__file__))[0])

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
        "Ship static data processor started. Reading from '%s', writing to '%s'.",
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

            valid, reason = validate_ship_static_data(msg)
            if not valid:
                skipped += 1
                log.info("Skipped message: %s", reason)
                continue

            output = transform_to_ship_static_data(msg)
            producer.send(OUTPUT_TOPIC, output)
            processed += 1

            if processed % 1000 == 0:
                producer.flush()
                log.info("Progress: processed=%d  skipped=%d", processed, skipped)
    finally:
        producer.flush()
        producer.close()
        consumer.close()
        log.info("Shutdown complete. processed=%d  skipped=%d", processed, skipped)


if __name__ == "__main__":
    main()
