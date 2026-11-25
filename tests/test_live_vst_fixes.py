"""Offline regression tests for the 4 defects discovered in QE_HERMES_15MIN_LIVE_VST_TEST_V1."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import ccxt
import httpx

from quantum_edge_core.contracts import TradeDecision
from quantum_edge_core.execution import (
    LeanExecutor,
    LeanRiskGuard,
    OrderLedger,
    PaperExchangeClient,
    RiskConfig,
    fetch_vst_status,
)
import tools.agy_openai_proxy as agy_proxy
import tools.qe_harness_cli as qe_harness_cli


class TestBingxSlTpParsingAndFormatting:
    def test_fetch_vst_status_parses_trigger_price_and_raw_type_from_ccxt_bingx_orders(self):
        mock_client = MagicMock()
        mock_client.fetch_balance.return_value = {
            "VST": {"free": 260000.0, "total": 261000.0, "used": 1000.0}
        }
        mock_client.fetch_positions.return_value = []
        # Simulate CCXT BingX parse_order() output where triggerPrice is None and
        # trigger price resides in stopLossPrice / takeProfitPrice / info.stopPrice
        mock_client.fetch_open_orders.return_value = [
            {
                "id": "sl-ord-1",
                "clientOrderId": "qe-sl-1",
                "symbol": "BTC/USDT:USDT",
                "type": "market",
                "side": "sell",
                "price": 58317.6,
                "triggerPrice": None,
                "stopPrice": None,
                "stopLossPrice": 57700.0,
                "amount": 0.01,
                "filled": 0.0,
                "status": "open",
                "reduceOnly": True,
                "info": {
                    "type": "STOP_MARKET",
                    "workingType": "MARK_PRICE",
                    "positionSide": "LONG",
                    "stopPrice": "57700.0",
                },
            },
            {
                "id": "tp-ord-2",
                "clientOrderId": "qe-tp-2",
                "symbol": "BTC/USDT:USDT",
                "type": "market",
                "side": "sell",
                "price": 58317.6,
                "triggerPrice": None,
                "stopPrice": None,
                "takeProfitPrice": None,
                "amount": 0.01,
                "filled": 0.0,
                "status": "open",
                "reduceOnly": True,
                "info": {
                    "type": "TAKE_PROFIT_MARKET",
                    "workingType": "MARK_PRICE",
                    "positionSide": "LONG",
                    "stopPrice": "59100.0",
                },
            },
        ]

        status = fetch_vst_status(mock_client)
        orders = status["open_orders"]
        assert len(orders) == 2

        assert orders[0]["triggerPrice"] == 57700.0
        assert orders[0]["stopPrice"] == 57700.0
        assert orders[0]["rawType"] == "STOP_MARKET"
        assert orders[0]["workingType"] == "MARK_PRICE"
        assert orders[0]["reduceOnly"] is True
        assert orders[0]["positionSide"] == "LONG"

        assert orders[1]["triggerPrice"] == 59100.0
        assert orders[1]["stopPrice"] == 59100.0
        assert orders[1]["rawType"] == "TAKE_PROFIT_MARKET"
        assert orders[1]["workingType"] == "MARK_PRICE"
        assert orders[1]["reduceOnly"] is True

    def test_lean_executor_sl_tp_format_is_compatible_with_ccxt_bingx_and_stripped_on_close(self, tmp_path: Path):
        paper = PaperExchangeClient()
        executor = LeanExecutor(paper, OrderLedger(tmp_path / "ledger.sqlite"), LeanRiskGuard())

        open_dec = TradeDecision(
            decision_id="dec-bingx-fmt-open",
            symbol="BTCUSDT",
            action="OPEN_LONG",
            order_type="MARKET",
            sl_price=57700.0,
            tp_price=59100.0,
            margin_vst=100.0,
            leverage=5,
            reason="SL/TP format check",
        )
        res_open = executor.execute(open_dec, current_price=58317.6)
        assert res_open.status == "SUBMITTED"

        submitted_open = paper.submitted_orders[0]
        assert submitted_open["params"]["stopLoss"] == {"triggerPrice": 57700.0}
        assert submitted_open["params"]["takeProfit"] == {"triggerPrice": 59100.0}

        # Verify offline against real ccxt.bingx.create_order_request() with loaded stub market
        ex = ccxt.bingx({"options": {"defaultType": "swap"}})
        ex.markets = {
            "BTC/USDT:USDT": {
                "id": "BTC-USDT",
                "symbol": "BTC/USDT:USDT",
                "base": "BTC",
                "quote": "USDT",
                "settle": "USDT",
                "spot": False,
                "swap": True,
                "type": "swap",
                "future": False,
                "contract": True,
                "linear": True,
                "inverse": False,
                "precision": {"amount": 0.001, "price": 0.1},
                "limits": {"amount": {"min": 0.001}, "price": {"min": 0.1}},
            }
        }
        ex.markets_by_id = {"BTC-USDT": [ex.markets["BTC/USDT:USDT"]]}
        req = ex.create_order_request(
            "BTC/USDT:USDT",
            "market",
            "buy",
            submitted_open["amount"],
            None,
            submitted_open["params"],
        )
        assert json.loads(req["stopLoss"])["stopPrice"] == 57700.0
        assert json.loads(req["stopLoss"])["type"] == "STOP_MARKET"
        assert json.loads(req["takeProfit"])["stopPrice"] == 59100.0
        assert json.loads(req["takeProfit"])["type"] == "TAKE_PROFIT_MARKET"

        # Close order must NOT attach stopLoss/takeProfit and MUST keep reduceOnly=True
        close_dec = TradeDecision(
            decision_id="dec-bingx-fmt-close",
            symbol="BTCUSDT",
            action="CLOSE_LONG",
            order_type="MARKET",
            sl_price=57700.0,
            tp_price=59100.0,
            qty=0.01,
            reason="close without SL/TP",
        )
        res_close = executor.execute(close_dec, current_price=58500.0)
        assert res_close.status == "SUBMITTED"
        submitted_close = paper.submitted_orders[1]
        assert submitted_close["params"]["reduceOnly"] is True
        assert "stopLoss" not in submitted_close["params"]
        assert "takeProfit" not in submitted_close["params"]


class TestProxyBrokenPipeAndSseKeepalive:
    def test_proxy_handles_broken_pipe_cleanly_without_crashing(self, tmp_path: Path):
        started = threading.Event()
        release = threading.Event()

        def slow_runner(prompt: str, **kwargs: Any) -> dict[str, Any]:
            started.set()
            release.wait(timeout=3.0)
            return {
                "status": "SUCCESS",
                "response": "Delayed response after client disconnect.",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }

        log_path = tmp_path / "proxy_bp.log"
        engine = agy_proxy.AgyProxyEngine(
            default_model="gemini-4-carbon-medium",
            timeout_sec=180.0,
            log_path=log_path,
            runner=slow_runner,
        )
        server = agy_proxy.create_server(host="127.0.0.1", port=0, engine=engine)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            # Send a raw HTTP POST and close the socket abruptly before the server writes its response
            body = json.dumps(
                {
                    "model": "gemini-4-carbon-medium",
                    "messages": [{"role": "user", "content": "Slow query"}],
                }
            ).encode("utf-8")
            with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock:
                raw_req = (
                    f"POST /v1/chat/completions HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{port}\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    f"Connection: close\r\n\r\n"
                ).encode("ascii") + body
                sock.sendall(raw_req)
                assert started.wait(timeout=2.0)

            # Now client socket is closed -> release worker so server hits BrokenPipeError on write
            release.set()
            time.sleep(0.2)

            # Server must still be healthy and serve subsequent requests
            health = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
            assert health.status_code == 200
            log_text = log_path.read_text(encoding="utf-8")
            assert "client_disconnected" in log_text
        finally:
            release.set()
            server.shutdown()
            server.server_close()

    def test_proxy_emits_sse_keepalive_comments_while_waiting(self, tmp_path: Path):
        def delayed_runner(prompt: str, **kwargs: Any) -> dict[str, Any]:
            time.sleep(0.25)
            return {
                "status": "SUCCESS",
                "response": "SSE after keepalive.",
                "usage": {"input_tokens": 15, "output_tokens": 5},
            }

        engine = agy_proxy.AgyProxyEngine(
            default_model="gemini-4-carbon-medium",
            timeout_sec=180.0,
            keepalive_interval_sec=0.08,
            log_path=tmp_path / "proxy_sse.log",
            runner=delayed_runner,
        )
        server = agy_proxy.create_server(host="127.0.0.1", port=0, engine=engine)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            resp = httpx.post(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                json={
                    "model": "gemini-4-carbon-medium",
                    "stream": True,
                    "messages": [{"role": "user", "content": "Wait for SSE"}],
                },
                timeout=5.0,
            )
            assert resp.status_code == 200
            assert ": keepalive" in resp.text
            assert "SSE after keepalive." in resp.text
            assert "data: [DONE]" in resp.text
        finally:
            server.shutdown()
            server.server_close()


class TestXrpDefaultAndMinimalCloseSchema:
    def test_xrpusdt_allowed_by_default_and_via_cli_override(self, tmp_path: Path, capsys):
        guard = LeanRiskGuard(RiskConfig())
        xrp_open = TradeDecision(
            decision_id="dec-xrp-default-1",
            symbol="XRPUSDT",
            action="OPEN_LONG",
            order_type="MARKET",
            sl_price=0.58,
            margin_vst=100.0,
            leverage=5,
            reason="XRP default allow test",
        )
        ok, msg = guard.validate(xrp_open, open_positions_count=0, current_price=0.62)
        assert ok is True
        assert msg == "OK"

        # CLI --allowed-symbols override test
        rc = qe_harness_cli.main(
            [
                "execute",
                "--dry-run",
                "--allowed-symbols",
                "BTCUSDT,XRPUSDT",
                "--decision-json",
                xrp_open.model_dump_json(),
                "--current-price",
                "0.62",
                "--ledger-db",
                str(tmp_path / "cli_xrp.sqlite3"),
                "--journal-path",
                str(tmp_path / "cli_xrp.jsonl"),
            ]
        )
        assert rc == 0
        out = json.loads(capsys.readouterr().out.strip())
        assert out["status"] == "SUBMITTED"
        assert out["symbol"] == "XRPUSDT"

    def test_minimal_close_and_hold_decisions_without_margin_or_leverage(self, tmp_path: Path):
        for action in ("CLOSE_LONG", "CLOSE_SHORT", "HOLD"):
            raw = {
                "decision_id": f"min-{action.lower()}-1",
                "symbol": "XRPUSDT",
                "action": action,
                "reason": "minimal payload check",
            }
            dec = TradeDecision.model_validate(raw)
            assert dec.margin_vst == 0.0
            assert dec.leverage == 1

            ok, msg = LeanRiskGuard().validate(dec, open_positions_count=3, current_price=0.62)
            assert ok is True
            assert msg == "OK"

        # Execute minimal CLOSE_LONG via LeanExecutor
        paper = PaperExchangeClient()
        executor = LeanExecutor(paper, OrderLedger(tmp_path / "min_close.sqlite"), LeanRiskGuard())
        min_close = TradeDecision.model_validate(
            {
                "decision_id": "min-exec-close-1",
                "symbol": "BTCUSDT",
                "action": "CLOSE_LONG",
                "qty": 0.01,
                "reason": "minimal close execute",
            }
        )
        res = executor.execute(min_close, current_price=58500.0, open_positions_count=3)
        assert res.status == "SUBMITTED"
        assert paper.submitted_orders[0]["params"]["reduceOnly"] is True
