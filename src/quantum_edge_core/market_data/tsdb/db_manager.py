#!/usr/bin/env python3
"""
QuestDB Schema and Retention (Data Lifecycle) Manager.
Initializes unified tables and purges old partitions using the HTTP REST API.
"""

import argparse
import logging
import os
import requests
from datetime import datetime, timedelta, timezone

# Default QuestDB REST Endpoint
QUESTDB_HOST = os.getenv("MARKET_DATA_QUEST_HOST", "127.0.0.1")
QUESTDB_PORT = os.getenv("MARKET_DATA_QUEST_REST_PORT", "9000") # REST API is on 9000
QUESTDB_URL = f"http://{QUESTDB_HOST}:{QUESTDB_PORT}/exec"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("QuestDBManager")

# Retention periods in days
RETENTION_POLICY = {
    "market_features": 30,
    "llm_decisions": 30,
    "bot_telemetry": 14,
    "trades": 7,
    "orderbook_snapshots": 3
}

# Mapping tables to their designated timestamp columns
TABLE_TIMESTAMP_COLUMNS = {
    "market_features": "ts",
    "llm_decisions": "ts",
    "bot_telemetry": "ts",
    "trades": "timestamp",
    "orderbook_snapshots": "timestamp"
}

DDL_STATEMENTS = [
    # Table 1: market_features
    """
    CREATE TABLE IF NOT EXISTS market_features (
        ts TIMESTAMP,
        symbol SYMBOL,
        mid_price DOUBLE,
        spread DOUBLE,
        rsi_14 DOUBLE,
        macd_line DOUBLE,
        macd_signal DOUBLE,
        atr_14 DOUBLE,
        ofi_raw DOUBLE,
        volume_delta DOUBLE
    ) TIMESTAMP(ts) PARTITION BY DAY;
    """,
    "ALTER TABLE market_features ALTER COLUMN symbol ADD INDEX;",

    # Table 2: bot_telemetry
    """
    CREATE TABLE IF NOT EXISTS bot_telemetry (
        ts TIMESTAMP,
        bot_id SYMBOL,
        status SYMBOL,
        pnl_session DOUBLE,
        active_margin DOUBLE,
        drawdown_pct DOUBLE,
        latency_ms INT
    ) TIMESTAMP(ts) PARTITION BY DAY;
    """,
    "ALTER TABLE bot_telemetry ALTER COLUMN bot_id ADD INDEX;",

    # Table 3: llm_decisions
    """
    CREATE TABLE IF NOT EXISTS llm_decisions (
        ts TIMESTAMP,
        bot_id SYMBOL,
        verdict SYMBOL,
        reason STRING,
        raw_prompt STRING,
        raw_response STRING
    ) TIMESTAMP(ts) PARTITION BY DAY;
    """,
    "ALTER TABLE llm_decisions ALTER COLUMN bot_id ADD INDEX;",
    "ALTER TABLE llm_decisions ALTER COLUMN verdict ADD INDEX;"
]

def execute_sql(query: str) -> bool:
    """Executes a DDL/SQL query on QuestDB via HTTP GET."""
    clean_query = " ".join(query.split())
    try:
        response = requests.get(QUESTDB_URL, params={"query": clean_query}, timeout=5.0)
        if response.status_code == 200:
            logger.info(f"Successfully executed: {clean_query[:70]}...")
            return True
        else:
            # Table already altered index or similar non-critical issues can be quiet
            if "column value index already exists" in response.text.lower():
                logger.debug(f"Non-critical issue: {response.text.strip()}")
                return True
            logger.error(f"Failed to execute query. Status: {response.status_code}, Response: {response.text.strip()}")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Connection error to QuestDB ({QUESTDB_URL}): {e}")
        return False

def init_db():
    """Initializes the unified tables and indexes."""
    logger.info("Initializing QuestDB Unified Schemas...")
    success_count = 0
    for stmt in DDL_STATEMENTS:
        if execute_sql(stmt):
            success_count += 1
    logger.info(f"Database initialization completed. {success_count}/{len(DDL_STATEMENTS)} operations succeeded.")

def purge_old_data():
    """Purges old partitions based on the RETENTION_POLICY."""
    logger.info("Starting QuestDB Data Retention Purge...")
    utc_now = datetime.now(timezone.utc)
    
    for table, days in RETENTION_POLICY.items():
        # Calculate cut-off date
        cutoff_date = (utc_now - timedelta(days=days)).strftime("%Y-%m-%d")
        logger.info(f"Applying retention for table '{table}' (> {days} days, older than {cutoff_date})")
        
        # Get designated timestamp column name
        ts_col = TABLE_TIMESTAMP_COLUMNS.get(table, "ts")
        
        # QuestDB Alter Table Drop Partition syntax
        # Using specific date string to ensure maximum compatibility across versions
        query = f"ALTER TABLE {table} DROP PARTITION WHERE {ts_col} < '{cutoff_date}';"
        
        try:
            response = requests.get(QUESTDB_URL, params={"query": query}, timeout=5.0)
            if response.status_code == 200:
                logger.info(f"Successfully purged partitions for '{table}' before {cutoff_date}")
            else:
                resp_text = response.text.strip()
                if "table does not exist" in resp_text.lower() or "table not found" in resp_text.lower():
                    logger.warning(f"Table '{table}' does not exist yet. Skipping purge.")
                elif "no partitions met the criteria" in resp_text.lower() or "no partitions dropped" in resp_text.lower():
                    logger.info(f"No partitions met the criteria for '{table}' before {cutoff_date}")
                else:
                    logger.error(f"Failed to purge table '{table}': {resp_text}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Connection error while purging '{table}': {e}")

def main():
    parser = argparse.ArgumentParser(description="QuestDB Schema & Retention Manager")
    parser.add_argument(
        "action",
        choices=["init", "purge", "run-all"],
        help="Action to perform: 'init' creates tables, 'purge' drops old partitions, 'run-all' does both."
    )
    args = parser.parse_args()

    if args.action == "init":
        init_db()
    elif args.action == "purge":
        purge_old_data()
    elif args.action == "run-all":
        init_db()
        purge_old_data()

if __name__ == "__main__":
    main()
