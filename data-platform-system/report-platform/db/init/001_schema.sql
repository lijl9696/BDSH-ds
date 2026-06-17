CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS platforms (
    id SERIAL PRIMARY KEY,
    code VARCHAR(64) UNIQUE NOT NULL,
    name VARCHAR(128) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS stores (
    id SERIAL PRIMARY KEY,
    store_code VARCHAR(128) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    province VARCHAR(128),
    city VARCHAR(128),
    region VARCHAR(128),
    owner VARCHAR(128),
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    store_type VARCHAR(32) NOT NULL DEFAULT 'unknown',
    assignment_status VARCHAR(32) NOT NULL DEFAULT 'unconfigured',
    assignment_source VARCHAR(64),
    assignment_confidence INTEGER NOT NULL DEFAULT 0,
    assignment_note TEXT,
    aliases JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS store_assignments (
    id SERIAL PRIMARY KEY,
    platform_code VARCHAR(64) NOT NULL,
    province VARCHAR(128),
    city VARCHAR(128),
    region VARCHAR(128) NOT NULL,
    owner VARCHAR(128) NOT NULL,
    store_name VARCHAR(255) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_store_assignment_platform_name UNIQUE(platform_code, store_name)
);

CREATE TABLE IF NOT EXISTS area_assignments (
    id SERIAL PRIMARY KEY,
    province VARCHAR(128) NOT NULL,
    city VARCHAR(128) NOT NULL,
    store_name VARCHAR(255) NOT NULL DEFAULT '',
    store_type VARCHAR(32) NOT NULL DEFAULT 'all',
    region VARCHAR(128) NOT NULL,
    owner VARCHAR(128) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_area_assignment_province_city_store_type UNIQUE(province, city, store_name, store_type)
);

CREATE TABLE IF NOT EXISTS metrics (
    id SERIAL PRIMARY KEY,
    code VARCHAR(128) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    value_type VARCHAR(32) NOT NULL DEFAULT 'number',
    unit VARCHAR(64),
    aggregation VARCHAR(64) NOT NULL DEFAULT 'sum',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    description TEXT
);

CREATE TABLE IF NOT EXISTS field_mappings (
    id SERIAL PRIMARY KEY,
    platform_code VARCHAR(64) NOT NULL,
    source_field VARCHAR(255) NOT NULL,
    metric_code VARCHAR(128) NOT NULL REFERENCES metrics(code),
    data_type VARCHAR(32) NOT NULL DEFAULT 'number',
    clean_rule JSONB NOT NULL DEFAULT '{}'::jsonb,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_field_mapping_platform_source UNIQUE(platform_code, source_field)
);

CREATE TABLE IF NOT EXISTS import_batches (
    id SERIAL PRIMARY KEY,
    platform_code VARCHAR(64) NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    source_type VARCHAR(32) NOT NULL DEFAULT 'file',
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    duplicate_policy VARCHAR(32) NOT NULL DEFAULT 'skip',
    import_options JSONB NOT NULL DEFAULT '{}'::jsonb,
    row_count INTEGER NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS import_files (
    id SERIAL PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES import_batches(id),
    filename VARCHAR(255) NOT NULL,
    storage_path VARCHAR(500) NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw_import_rows (
    id SERIAL PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES import_batches(id),
    row_number INTEGER NOT NULL,
    raw_data JSONB NOT NULL,
    normalized_keys JSONB NOT NULL DEFAULT '{}'::jsonb,
    warning TEXT
);

CREATE TABLE IF NOT EXISTS metric_values (
    id SERIAL PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES import_batches(id),
    metric_date DATE NOT NULL,
    platform_code VARCHAR(64) NOT NULL,
    store_code VARCHAR(128) NOT NULL,
    metric_code VARCHAR(128) NOT NULL REFERENCES metrics(code),
    value NUMERIC(20, 4) NOT NULL,
    dimensions JSONB NOT NULL DEFAULT '{}'::jsonb,
    dimension_hash VARCHAR(64) NOT NULL DEFAULT 'default',
    version INTEGER NOT NULL DEFAULT 1,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_metric_value_identity UNIQUE(metric_date, platform_code, store_code, metric_code, dimension_hash, version)
);

CREATE TABLE IF NOT EXISTS text_metric_values (
    id SERIAL PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES import_batches(id),
    metric_date DATE NOT NULL,
    platform_code VARCHAR(64) NOT NULL,
    store_code VARCHAR(128) NOT NULL,
    metric_code VARCHAR(128) NOT NULL REFERENCES metrics(code),
    value VARCHAR(255) NOT NULL,
    dimensions JSONB NOT NULL DEFAULT '{}'::jsonb,
    dimension_hash VARCHAR(64) NOT NULL DEFAULT 'default',
    version INTEGER NOT NULL DEFAULT 1,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_text_metric_value_identity UNIQUE(metric_date, platform_code, store_code, metric_code, dimension_hash, version)
);

CREATE TABLE IF NOT EXISTS derived_metric_rules (
    id SERIAL PRIMARY KEY,
    metric_code VARCHAR(128) UNIQUE NOT NULL REFERENCES metrics(code),
    expression TEXT NOT NULL,
    numerator_metric VARCHAR(128),
    denominator_metric VARCHAR(128),
    enabled BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS report_presets (
    id SERIAL PRIMARY KEY,
    code VARCHAR(128) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    config JSONB NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_metric_values_lookup
    ON metric_values(metric_date, platform_code, store_code, metric_code)
    WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_text_metric_values_lookup
    ON text_metric_values(metric_date, platform_code, store_code, metric_code)
    WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_stores_region_owner ON stores(region, owner);
CREATE INDEX IF NOT EXISTS idx_stores_province_city ON stores(province, city);
CREATE INDEX IF NOT EXISTS idx_stores_assignment_status ON stores(assignment_status);
CREATE INDEX IF NOT EXISTS idx_store_assignments_lookup ON store_assignments(platform_code, store_name);
CREATE INDEX IF NOT EXISTS idx_area_assignments_lookup ON area_assignments(province, city, store_name, store_type);
