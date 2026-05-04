# quality
# ship_id from MMSI unique and not null
# ship_name not null
# course_over_ground 0-360 or null
# speed_over_ground 0-102.2 or null
# navigational_status one of: "Under way using engine", "At anchor", "Not under command", "Restricted manoeuverability", "Constrained by her draught", "Moored", "Aground", "Engaged in fishing", "Under way sailing", "Unknown"
# rate_of_turn -127 to 127 or null
# latitude -90 to 90 not null
# longitude -180 to 180 not null

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, validator

class ShipPosition(BaseModel):
    ship_id: int
    ship_name: str
    course_over_ground: Optional[int] = Field(None, ge=0, le=360)
    speed_over_ground: Optional[float] = Field(None, ge=0, le=102.2)
    navigational_status: Optional[str] = Field(
        None,
        regex=r"^(Under way using engine|At anchor|Not under command|Restricted manoeuverability|Constrained by her draught|Moored|Aground|Engaged in fishing|Under way sailing|Unknown)$"
    )
    rate_of_turn: Optional[int] = Field(None, ge=-127, le=127)
    latitude: float
    longitude: float
    recorded_at: datetime

    @validator('ship_name')
    def name_must_not_be_empty(cls, v):
        if not v.strip():
            raise ValueError('ship_name must not be empty')
        return v
    
    @validator('latitude')
    def latitude_must_be_valid(cls, v): 
        if v < -90 or v > 90:
            raise ValueError('latitude must be between -90 and 90')
        return v
    
    @validator('longitude')
    def longitude_must_be_valid(cls, v):
        if v < -180 or v > 180:
            raise ValueError('longitude must be between -180 and 180')
        return v

import json
import os
import logging
from datetime import datetime, timezone

import psycopg2
from dotenv import load_dotenv
from kafka import KafkaConsumer
from pydantic import ValidationError

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# AIS navigational status integer → human-readable string
NAV_STATUS_MAP = {
    0: "Under way using engine",
    1: "At anchor",
    2: "Not under command",
    3: "Restricted manoeuverability",
    4: "Constrained by her draught",
    5: "Moored",
    6: "Aground",
    7: "Engaged in fishing",
    8: "Under way sailing",
    15: "Unknown",
}

UPSERT_SQL = """
    INSERT INTO ships_live_data (
        ship_id, ship_name, course_over_ground, speed_over_ground,
        navigational_status, rate_of_turn, latitude, longitude, updated_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (ship_id) DO UPDATE SET
        ship_name           = EXCLUDED.ship_name,
        course_over_ground  = EXCLUDED.course_over_ground,
        speed_over_ground   = EXCLUDED.speed_over_ground,
        navigational_status = EXCLUDED.navigational_status,
        rate_of_turn        = EXCLUDED.rate_of_turn,
        latitude            = EXCLUDED.latitude,
        longitude           = EXCLUDED.longitude,
        updated_at          = EXCLUDED.updated_at
    WHERE ships_live_data.updated_at < EXCLUDED.updated_at;
"""

def parse_message(raw: dict) -> ShipPosition | None:
    """Map raw AIS Kafka message to ShipPosition model."""
    try:
        meta = raw["MetaData"]
        report = raw["Message"]["PositionReport"]

        nav_int = report.get("NavigationalStatus")
        nav_str = NAV_STATUS_MAP.get(nav_int)  # None if unknown code

        recorded_at = datetime.strptime(
            meta["time_utc"].split(" +")[0], "%Y-%m-%d %H:%M:%S.%f"
        ).replace(tzinfo=timezone.utc)

        return ShipPosition(
            ship_id=meta["MMSI"],
            ship_name=meta["ShipName"].strip(),
            course_over_ground=int(report["Cog"]) if report.get("Cog") is not None else None,
            speed_over_ground=report.get("Sog"),
            navigational_status=nav_str,
            rate_of_turn=report.get("RateOfTurn"),
            latitude=meta["latitude"],
            longitude=meta["longitude"],
            recorded_at=recorded_at,
        )
    except (KeyError, ValueError) as e:
        log.warning("Failed to parse message: %s — %s", e, raw)
        return None
    except ValidationError as e:
        log.warning("Validation failed: %s", e)
        return None

def main():
    consumer = KafkaConsumer(
        "PositionReport",
        bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9093"),
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        auto_offset_reset="latest",
        enable_auto_commit=True,
        group_id="ships-live-data-writer",
    )

    conn = psycopg2.connect(
        host=os.getenv("PG_HOST", "localhost"),
        port=os.getenv("PG_PORT", "5432"),
        dbname=os.getenv("PG_DB"),
        user=os.getenv("PG_USER"),
        password=os.getenv("PG_PASSWORD"),
    )
    conn.autocommit = False

    log.info("Consumer started, waiting for messages...")

    try:
        for msg in consumer:
            ship = parse_message(msg.value)
            if ship is None:
                continue

            try:
                with conn.cursor() as cur:
                    cur.execute(UPSERT_SQL, (
                        ship.ship_id,
                        ship.ship_name,
                        ship.course_over_ground,
                        ship.speed_over_ground,
                        ship.navigational_status,
                        ship.rate_of_turn,
                        ship.latitude,
                        ship.longitude,
                        ship.recorded_at,
                    ))
                conn.commit()
            except psycopg2.Error as e:
                log.error("DB error for ship %s: %s", ship.ship_id, e)
                conn.rollback()
    finally:
        consumer.close()
        conn.close()

if __name__ == "__main__":
    main()



