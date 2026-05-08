"""
AIS Ship Tracker – Flask Web Application
=========================================
Serves a MapLibre GL JS map showing live ship positions from `ships_live_data`
and position history for actively-tracked ships from `position_history`.

API endpoints
-------------
GET /                           – HTML map page
GET /api/ships/live             – GeoJSON FeatureCollection of all live ships
GET /api/ships/<ship_id>/history – GeoJSON FeatureCollection (line + points)
GET /api/ships/tracked          – JSON list of actively-tracked ship IDs

Configuration (environment variables)
--------------------------------------
DB_HOST          default: localhost
DB_PORT          default: 5432
DB_NAME          default: postgres
DB_USER          default: postgres
DB_PASSWORD      default: (empty)
HISTORY_LIMIT    default: 200   – max history points returned per ship
"""

import os
import logging

import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, render_template

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DB_CONFIG: dict = {
    "host": os.getenv("DB_HOST", "host.docker.internal"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "postgres"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "dbpass1234"),
}

HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", "200"))

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _get_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(**DB_CONFIG)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/ships/live")
def ships_live():
    """Return all live ship positions as a GeoJSON FeatureCollection."""
    try:
        conn = _get_conn()
    except Exception as exc:
        log.error("DB connection failed: %s", exc)
        return jsonify({"error": "database unavailable"}), 503

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT ship_id, ship_name, course_over_ground, speed_over_ground,
                       navigational_status, latitude, longitude, updated_at
                FROM ships_live_data
                WHERE latitude IS NOT NULL AND longitude IS NOT NULL
                """
            )
            rows = cur.fetchall()

        log.info("Fetched %d live ship records from DB", len(rows))

        with conn.cursor() as cur:
            cur.execute(
                "SELECT ship_id FROM tracking_config WHERE enabled_to IS NULL"
            )
            tracked_ids: set[int] = {row[0] for row in cur.fetchall()}

        log.info("Currently tracking %d ships", len(tracked_ids))

        features = []
        for row in rows:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [
                            float(row["longitude"]),
                            float(row["latitude"]),
                        ],
                    },
                    "properties": {
                        "ship_id": row["ship_id"],
                        "ship_name": (row["ship_name"] or "").strip(),
                        "course_over_ground": row["course_over_ground"] or 0,
                        "speed_over_ground":  row["speed_over_ground"] or 0,
                        "navigational_status": row["navigational_status"] or "",
                        "updated_at": (
                            row["updated_at"].isoformat()
                            if row["updated_at"]
                            else None
                        ),
                        "tracked": row["ship_id"] in tracked_ids,
                    },
                }
            )
        
        log.info("Returning %d features for live ship data", len(features))

        return jsonify({"type": "FeatureCollection", "features": features})
    except Exception as exc:
        log.exception("Error fetching live ship data: %s", exc)
        return jsonify({"error": "internal server error"}), 500
    finally:
        conn.close()


@app.route("/api/ships/<int:ship_id>/history")
def ship_history(ship_id: int):
    """Return the position history for a tracked ship as a GeoJSON FeatureCollection.

    The collection contains:
      - one LineString feature (chronological path)
      - one Point feature per recorded position
    """
    try:
        conn = _get_conn()
    except Exception as exc:
        log.error("DB connection failed: %s", exc)
        return jsonify({"error": "database unavailable"}), 503

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT latitude, longitude, course_over_ground, speed_over_ground,
                       navigational_status, recorded_at
                FROM position_history
                WHERE ship_id = %s
                ORDER BY recorded_at DESC
                LIMIT %s
                """,
                (ship_id, HISTORY_LIMIT),
            )
            rows = cur.fetchall()

        if not rows:
            return jsonify({"type": "FeatureCollection", "features": []})

        point_features = []
        coords_desc = []

        for row in rows:
            lon = float(row["longitude"])
            lat = float(row["latitude"])
            coords_desc.append([lon, lat])
            point_features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "course_over_ground": row["course_over_ground"] or 0,
                        "speed_over_ground": row["speed_over_ground"] or 0,
                        "navigational_status": row["navigational_status"] or "",
                        "recorded_at": (
                            row["recorded_at"].isoformat()
                            if row["recorded_at"]
                            else None
                        ),
                    },
                }
            )

        features = []

        # LineString in chronological order (oldest → newest)
        if len(coords_desc) >= 2:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": list(reversed(coords_desc)),
                    },
                    "properties": {"ship_id": ship_id, "type": "track"},
                }
            )

        features.extend(point_features)

        return jsonify({"type": "FeatureCollection", "features": features})
    except Exception as exc:
        log.exception("Error fetching history for ship %d: %s", ship_id, exc)
        return jsonify({"error": "internal server error"}), 500
    finally:
        conn.close()


@app.route("/api/ships/tracked")
def ships_tracked():
    """Return a JSON array of ship_ids that are actively tracked."""
    try:
        conn = _get_conn()
    except Exception as exc:
        log.error("DB connection failed: %s", exc)
        return jsonify({"error": "database unavailable"}), 503

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ship_id FROM tracking_config WHERE enabled_to IS NULL"
            )
            ids = [row[0] for row in cur.fetchall()]
        return jsonify(ids)
    except Exception as exc:
        log.exception("Error fetching tracked ships: %s", exc)
        return jsonify({"error": "internal server error"}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
