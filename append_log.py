log_path = "/home/korben/QuantumEdge-main/ai_scalper_behavior.log"
entry = """
=== AI SCALPER BEHAVIOR TRACKING ===
Timestamp: 2026-06-24 10:48 UTC
- zmq_status: ai_scalper=error(validation/parse: missing source/status/pnl_session/drawdown_pct/equity), dyndca=RUNNING, pnl_session=220.245, active_positions=1, position_size=7.0, unrealized_pnl=220.245
- market_snapshot: price=64400.0, spread=0.2, top_bid=64399.9, top_ask=64400.1
- trend: RSI=47.66, MACD=0.43, ATR=28.51 (latest 10:45 UTC)
- ai_scalper recent decisions: [2026-06-24 13:47:04] Price=62532.00 Signal=HOLD Reason=Strategy returned HOLD (Mode: scalp, OFI: -0.01, Pos: 0.1494); [2026-06-24 13:47:14] Price=62542.05 Signal=HOLD Reason=Strategy returned HOLD (Mode: scalp, OFI: -1.37, Pos: 0.1494); [2026-06-24 13:47:24] Price=62533.05 Signal=HOLD Reason=Strategy returned HOLD (Mode: scalp, OFI: -1.34, Pos: 0.1494); [2026-06-24 13:47:34] Price=62532.90 Signal=HOLD Reason=Strategy returned HOLD (Mode: scalp, OFI: -0.01, Pos: 0.1494); [2026-06-24 13:47:44] Price=62532.90 Signal=HOLD Reason=Strategy returned HOLD (Mode: scalp, OFI: -0.54, Pos: 0.1494)
- dyndca recent TP/close attempts: [2026-06-24 13:18:39] TAKE PROFIT HIT. Position closed successfully. profit_pct=1.2 entry=65944.36 order_id=2069726883861237760 price=62595.7 side=sell; [2026-06-24 13:18:39] Grid position closed entry=66614.09 order_id=2069726887791300610 price=62595.7 side=sell; [2026-06-24 13:20:12] TAKE PROFIT HIT. Position closed successfully. profit_pct=1.2; [2026-06-24 13:20:12] Grid position closed entry=63136.53 order_id=2069726840362110976 price=62505.0 side=sell
- ANALYSIS: ai_scalper zgeneruvav pomyilku validatsii ZMQ-statusu -- vidсутni obovyazkovi polya (source/status/pnl_session/drawdown_pct/equity). dyndca normalno pratsyue: zafiksovano zakryttya pozytsey za TP z pribytkom 1.2%. ai_scalper vydaye lshe HOLD-signaly z OFI v mezhah -1.37..-0.01, pozytsiia 0.1494. RSI=47.66, MACD=0.43 -- neutralna/slabko-bycha zona, ATR=28.51 vkazuye na pomirnu volaty lnist. Rozbizhnist: ai_scalper pratsyue z tsinoju ~62532, a market_snapshot pokazuye 64400 -- mozhlyvo inshyi instrum ent/symvol abo zatry mka danikh. Korektnist rozrakhunkiv TP pidtverdzhena logamy dyndca. Ochikuvannia TP:aktyvna pozytsiia dyndca=1, pry potochnij volaty lnosti TP mozhe spracyuvaty pry rusi vgoru.

=== END TRACKING ===
"""
with open(log_path, "a", encoding="utf-8") as f:
    f.write(entry)
print("LOGGED", flush=True)
