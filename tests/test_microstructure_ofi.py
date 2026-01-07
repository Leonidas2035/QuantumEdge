from MarketDataHub.microstructure.ofi import MicrostructureAnalyzer


def test_ofi_qty_change_same_price():
    analyzer = MicrostructureAnalyzer(window_n=5)
    analyzer.update_book(symbol="BTCUSDT", bid_px=100.0, bid_qty=1.0, ask_px=101.0, ask_qty=2.0, ts_event=1)
    snap = analyzer.update_book(symbol="BTCUSDT", bid_px=100.0, bid_qty=2.0, ask_px=101.0, ask_qty=2.0, ts_event=2)
    assert snap is not None
    assert snap.ofi_raw == 1.0


def test_ofi_bid_moves_up_down():
    analyzer = MicrostructureAnalyzer(window_n=5)
    analyzer.update_book(symbol="BTCUSDT", bid_px=100.0, bid_qty=5.0, ask_px=102.0, ask_qty=2.0, ts_event=1)
    snap_up = analyzer.update_book(symbol="BTCUSDT", bid_px=101.0, bid_qty=3.0, ask_px=102.0, ask_qty=2.0, ts_event=2)
    assert snap_up is not None
    assert snap_up.ofi_raw == 3.0
    snap_down = analyzer.update_book(symbol="BTCUSDT", bid_px=100.0, bid_qty=4.0, ask_px=102.0, ask_qty=2.0, ts_event=3)
    assert snap_down is not None
    assert snap_down.ofi_raw == -3.0


def test_ofi_ask_moves_up_down():
    analyzer = MicrostructureAnalyzer(window_n=5)
    analyzer.update_book(symbol="BTCUSDT", bid_px=100.0, bid_qty=5.0, ask_px=102.0, ask_qty=2.0, ts_event=1)
    snap_down = analyzer.update_book(symbol="BTCUSDT", bid_px=100.0, bid_qty=5.0, ask_px=101.0, ask_qty=5.0, ts_event=2)
    assert snap_down is not None
    assert snap_down.ofi_raw == 5.0
    snap_up = analyzer.update_book(symbol="BTCUSDT", bid_px=100.0, bid_qty=5.0, ask_px=102.0, ask_qty=4.0, ts_event=3)
    assert snap_up is not None
    assert snap_up.ofi_raw == -5.0


def test_zscore_eps_clamp():
    analyzer = MicrostructureAnalyzer(window_n=5, eps=1e-6)
    analyzer.update_book(symbol="BTCUSDT", bid_px=100.0, bid_qty=1.0, ask_px=101.0, ask_qty=1.0, ts_event=1)
    snap = analyzer.update_book(symbol="BTCUSDT", bid_px=100.0, bid_qty=1.0, ask_px=101.0, ask_qty=1.0, ts_event=2)
    assert snap is not None
    assert abs(snap.ofi_z) <= 1e-6


def test_reset_flags_on_gap_and_resync():
    analyzer = MicrostructureAnalyzer(window_n=5)
    analyzer.update_book(symbol="BTCUSDT", bid_px=100.0, bid_qty=1.0, ask_px=101.0, ask_qty=1.0, ts_event=1)
    analyzer.mark_gap()
    snap_gap = analyzer.update_book(symbol="BTCUSDT", bid_px=100.0, bid_qty=1.0, ask_px=101.0, ask_qty=1.0, ts_event=2)
    assert snap_gap is not None
    assert snap_gap.is_gap is True
    analyzer.mark_resync()
    snap_resync = analyzer.update_book(symbol="BTCUSDT", bid_px=100.0, bid_qty=1.0, ask_px=101.0, ask_qty=1.0, ts_event=3)
    assert snap_resync is not None
    assert snap_resync.is_resynced is True
