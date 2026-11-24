-- Minimal ClickHouse schema for SupervisorAgent TSDB
CREATE DATABASE IF NOT EXISTS quantumedge;

CREATE TABLE IF NOT EXISTS quantumedge.qe_tsdb_points
(
    ts DateTime64(3),
    measurement String,
    tags String,
    fields String
)
ENGINE = MergeTree
ORDER BY ts
PARTITION BY toYYYYMM(ts);
