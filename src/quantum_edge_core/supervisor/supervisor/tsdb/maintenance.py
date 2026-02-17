"""TSDB retention and rollup maintenance helpers."""

from __future__ import annotations

import logging
import urllib.request
from pathlib import Path
from typing import Optional

from supervisor.config import TsdbRetentionConfig, TsdbConfig
from supervisor.tsdb.query import derive_questdb_query_url, questdb_exec


def _post_sql(url: str, sql: str, auth: Optional[tuple[str, str]] = None) -> None:
    req = urllib.request.Request(url, data=sql.encode("utf-8"), method="POST")
    if auth and auth[0]:
        creds = f"{auth[0]}:{auth[1] or ''}".encode("utf-8")
        import base64

        req.add_header(
            "Authorization", "Basic " + base64.b64encode(creds).decode("utf-8")
        )
    req.add_header("Content-Type", "text/plain")
    with urllib.request.urlopen(req, timeout=5) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"HTTP {resp.status}")


def clickhouse_retention(
    project_root: Path,
    cfg: TsdbConfig,
    retention: TsdbRetentionConfig,
    logger: logging.Logger,
) -> bool:
    sql_path = project_root / "sql" / "clickhouse_retention.sql"
    dynamic_sql = ""
    if retention.enabled:
        dynamic_sql = (
            f"ALTER TABLE {cfg.clickhouse_database}.{cfg.table_prefix}tsdb_points "
            f"MODIFY TTL ts + INTERVAL {retention.raw_days} DAY;"
        )
    sql = ""
    if sql_path.exists():
        sql = sql_path.read_text(encoding="utf-8")
    if dynamic_sql:
        sql = f"{dynamic_sql}\n{sql}"
    if not sql.strip():
        logger.warning("No ClickHouse retention SQL to apply.")
        return False
    try:
        _post_sql(
            f"{cfg.clickhouse_url}/?database={cfg.clickhouse_database}",
            sql,
            (cfg.clickhouse_user, cfg.clickhouse_password),
        )
        return True
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("ClickHouse retention failed: %s", exc)
        return False


def questdb_retention(
    cfg: TsdbConfig, retention: TsdbRetentionConfig, logger: logging.Logger
) -> bool:
    if not retention.enabled:
        return True
    query_url = cfg.questdb_query_url or derive_questdb_query_url(
        cfg.questdb_ilp_http_url
    )
    if not query_url:
        logger.warning("QuestDB query URL missing; retention skipped.")
        return False
    tables = ["qe_events", "qe_metrics", "qe_exec"]
    for table in tables:
        sql = f"DELETE FROM {table} WHERE timestamp < dateadd('d', -{retention.raw_days}, now())"
        try:
            questdb_exec(query_url, sql)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("QuestDB retention failed for %s: %s", table, exc)
            return False
    return True


def apply_retention_and_rollups(
    project_root: Path,
    tsdb_cfg: TsdbConfig,
    retention_cfg: TsdbRetentionConfig,
    logger: logging.Logger,
) -> bool:
    if not retention_cfg.enabled or not tsdb_cfg.enabled or tsdb_cfg.backend == "none":
        logger.info("Retention skipped (disabled or TSDB disabled).")
        return True
    if tsdb_cfg.backend == "clickhouse":
        return clickhouse_retention(project_root, tsdb_cfg, retention_cfg, logger)
    if tsdb_cfg.backend == "questdb":
        return questdb_retention(tsdb_cfg, retention_cfg, logger)
    logger.info("No retention handler for backend %s", tsdb_cfg.backend)
    return True
