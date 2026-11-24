-- Example retention/rollup helpers (adjust as needed)
ALTER TABLE quantumedge.qe_tsdb_points
    MODIFY TTL ts + INTERVAL 14 DAY;
