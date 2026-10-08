CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE TABLE IF NOT EXISTS sensor_readings (
 timestamp TIMESTAMPTZ NOT NULL, sensor_id TEXT NOT NULL, event_id TEXT NOT NULL,
 vibration_x DOUBLE PRECISION, vibration_y DOUBLE PRECISION, vibration_z DOUBLE PRECISION,
 temperature DOUBLE PRECISION, fault_level INTEGER, rpm DOUBLE PRECISION, data JSONB NOT NULL,
 PRIMARY KEY(timestamp, sensor_id)
);
SELECT create_hypertable('sensor_readings', 'timestamp', if_not_exists => TRUE);
CREATE TABLE IF NOT EXISTS health_readings (
 event_id TEXT PRIMARY KEY, timestamp TIMESTAMPTZ NOT NULL,
 health_index DOUBLE PRECISION, status TEXT, data JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS quality_inspections (
 id TEXT PRIMARY KEY, event_id TEXT NOT NULL, timestamp TIMESTAMPTZ NOT NULL,
 image_path TEXT NOT NULL, defect_type TEXT NOT NULL, confidence DOUBLE PRECISION,
 bbox JSONB, health_index_at_time DOUBLE PRECISION, gradcam_path TEXT, image_bytes BYTEA, data JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS quality_event_idx ON quality_inspections(event_id);
CREATE TABLE IF NOT EXISTS alarms (
 id BIGSERIAL PRIMARY KEY, timestamp TIMESTAMPTZ NOT NULL, event_id TEXT,
 source TEXT NOT NULL, severity TEXT NOT NULL, message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS line_status (
 id INTEGER PRIMARY KEY DEFAULT 1, timestamp TIMESTAMPTZ NOT NULL, data JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS gradcam_artifacts (
 event_id TEXT PRIMARY KEY, gradcam_path TEXT NOT NULL, metadata JSONB NOT NULL
);
