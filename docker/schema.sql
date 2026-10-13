CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE TABLE IF NOT EXISTS sensor_readings (
 timestamp TIMESTAMPTZ NOT NULL, sensor_id TEXT NOT NULL, event_id TEXT NOT NULL,
 vibration_x DOUBLE PRECISION, vibration_y DOUBLE PRECISION, vibration_z DOUBLE PRECISION,
 temperature DOUBLE PRECISION, fault_level DOUBLE PRECISION, rpm DOUBLE PRECISION, data JSONB NOT NULL,
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
CREATE TABLE IF NOT EXISTS prognostics_readings (
 event_id TEXT PRIMARY KEY, timestamp TIMESTAMPTZ NOT NULL, data JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS product_history (
 run_id TEXT NOT NULL, product_id TEXT NOT NULL, inspection_id TEXT NOT NULL,
 stage TEXT NOT NULL DEFAULT 'inspection_pending', outcome TEXT NOT NULL DEFAULT 'unknown',
 entered_at TIMESTAMPTZ, captured_at TIMESTAMPTZ, outfeed_at TIMESTAMPTZ,
 camera_timestamp TIMESTAMPTZ, camera_sim_time DOUBLE PRECISION, sensor_event_id TEXT,
 model_version TEXT, health_index DOUBLE PRECISION, quality JSONB,
 gradcam_path TEXT, gradcam_metadata JSONB, updated_at TIMESTAMPTZ NOT NULL,
 PRIMARY KEY(run_id, product_id, inspection_id),
 CHECK(outcome IN ('unknown','normal','defective'))
);
CREATE INDEX IF NOT EXISTS product_history_updated_idx ON product_history(updated_at DESC);
CREATE INDEX IF NOT EXISTS product_history_sensor_idx ON product_history(sensor_event_id);
CREATE UNIQUE INDEX IF NOT EXISTS product_history_evidence_idx ON product_history(sensor_event_id) WHERE sensor_event_id IS NOT NULL;
ALTER TABLE product_history
 ADD COLUMN IF NOT EXISTS outfeed_position JSONB,
 ADD COLUMN IF NOT EXISTS outfeed_lane TEXT,
 ADD COLUMN IF NOT EXISTS outfeed_classification TEXT;
