"""TSDB schema migrations."""

from __future__ import annotations

import logging
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from supervisor.config import TsdbConfig, TsdbRetentionConfig
from supervisor.tsdb.query import derive_questdb_query_url, questdb_exec


def apply_clickhouse(sql_path: Path, cfg: TsdbConfig, logger: logging.Logger) -> bool:
    if not sql_path.exists():
        logger.warning("ClickHouse schema file missing: %s", sql_path)
        return False
    sql = sql_path.read_text(encoding="utf-8")
    if not sql.strip():
        return False
    try:
        req = urllib.request.Request(
            f"{cfg.clickhouse_url}/?database={urllib.parse.quote(cfg.clickhouse_database)}",
            data=sql.encode("utf-8"),
            method="POST",
        )
        if cfg.clickhouse_user:
            creds = f"{cfg.clickhouse_user}:{cfg.clickhouse_password or ''}".encode("utf-8")
            import base64

            req.add_header("Authorization", "Basic " + base64.b64encode(creds).decode("utf-8"))
        req.add_header("Content-Type", "text/plain")
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            if resp.status >= 300:
                raise RuntimeError(f"ClickHouse HTTP status {resp.status}")
        logger.info("ClickHouse schema applied.")
        return True
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("ClickHouse migration failed: %s", exc)
        return False


def apply_questdb(sql_path: Path, cfg: TsdbConfig, logger: logging.Logger) -> bool:
    if not sql_path.exists():
        logger.warning("QuestDB schema file missing: %s", sql_path)
        return False
    query_url = cfg.questdb_query_url or derive_questdb_query_url(cfg.questdb_ilp_http_url)
    if not query_url:
        logger.warning("QuestDB query URL missing; cannot apply schema.")
        return False
    sql_text = sql_path.read_text(encoding="utf-8")
    statements = [stmt.strip() for stmt in sql_text.split(";") if stmt.strip()]
    for stmt in statements:
        try:
            questdb_exec(query_url, stmt)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("QuestDB migration failed: %s", exc)
            return False
    logger.info("QuestDB schema applied from %s", sql_path)
    return True


def run_tsdb_migrations(project_root: Path, cfg: TsdbConfig, logger: logging.Logger, retention: Optional[TsdbRetentionConfig] = None) -> bool:
    """Apply TSDB schema best-effort."""

    if not cfg.enabled or cfg.backend == "none":
        logger.info("TSDB disabled; skipping migrations.")
        return True

    ok = False
    if cfg.backend == "clickhouse":
        sql_path = project_root / "sql" / "clickhouse_schema.sql"
        ok = apply_clickhouse(sql_path, cfg, logger)
        if retention and retention.enabled:
            ret_path = project_root / "sql" / "clickhouse_retention.sql"
            if ret_path.exists():
                apply_clickhouse(ret_path, cfg, logger)
    elif cfg.backend == "questdb":
        sql_path = project_root / "supervisor" / "tsdb" / "migrations" / "0001_init.sql"
        if not sql_path.exists():
            sql_path = project_root / "sql" / "questdb_schema.sql"
        ok = apply_questdb(sql_path, cfg, logger)
    else:
        logger.info("No migration handler for backend %s", cfg.backend)
        ok = True
    return ok
