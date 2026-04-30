-- Latest position (fast lookup)
CREATE TABLE ships_live_data (
    ship_id INTEGER PRIMARY KEY,
    ship_name VARCHAR(50),
    course_over_ground SMALLINT,
    speed_over_ground SMALLINT,
    navigational_status VARCHAR(20),
    rate_of_turn SMALLINT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    updated_at TIMESTAMPTZ
);

-- Config: which ships get full history
CREATE TABLE tracking_config (
    ship_id INTEGER PRIMARY KEY REFERENCES ships_live_data (ship_id),
    enabled BOOLEAN DEFAULT TRUE,
    enabled_from TIMESTAMPTZ,
    enabled_to TIMESTAMPTZ -- NULL means still active
);

-- All history in one place
CREATE TABLE position_history (
    id BIGSERIAL,
    ship_id INTEGER NOT NULL REFERENCES ships_live_data (ship_id),
    course_over_ground SMALLINT,
    speed_over_ground SMALLINT,
    navigational_status VARCHAR(20),
    rate_of_turn SMALLINT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    recorded_at TIMESTAMPTZ NOT NULL
)
PARTITION BY
    RANGE (recorded_at) INTERVAL '1 day';

-- Critical for performance
CREATE INDEX ON position_history (ship_id, recorded_at DESC);

-- If scale becomes massive
-- Use time-based table partitioning (e.g., PostgreSQL PARTITION BY RANGE (recorded_at) monthly).
-- This gives you the storage isolation of per-ship tables without any of the management
-- problems — old partitions can be dropped as a single DDL operation on one object.