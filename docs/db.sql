-- Latest position (fast lookup)
CREATE TABLE ships_live_data (
    ship_id INTEGER PRIMARY KEY,
    ship_name VARCHAR(50) NOT NULL,
    course_over_ground FLOAT,
    speed_over_ground FLOAT,
    navigational_status VARCHAR(50),
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Config: which ships get full history
CREATE TABLE tracking_config (
    ship_id INTEGER NOT NULL REFERENCES ships_live_data (ship_id),
    enabled_from TIMESTAMPTZ NOT NULL DEFAULT now(),
    enabled_to TIMESTAMPTZ, -- NULL means still active
    PRIMARY KEY (ship_id, enabled_from)
);

-- All history in one place
CREATE TABLE position_history (
    id BIGSERIAL PRIMARY KEY,
    ship_id INTEGER NOT NULL REFERENCES ships_live_data (ship_id),
    course_over_ground FLOAT,
    speed_over_ground FLOAT,
    navigational_status VARCHAR(50),
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL
)

-- Critical for performance
CREATE INDEX ON position_history (ship_id, recorded_at DESC);
