**Microstructure OFI (v1)**
- Top-of-book OFI is computed in `src/quantum_edge_core/market_data/` (MarketDataHub) and published to bots + QuestDB.
- Rolling window statistics provide `ofi_z` and `ofi_ma5`.

**OFI Formula**
- OFIₜ = ΔBidQty@BestBid − ΔAskQty@BestAsk
- Bid side:
  - BestBidₜ > BestBidₜ₋₁ → +BidQtyₜ
  - BestBidₜ < BestBidₜ₋₁ → −BidQtyₜ₋₁
  - BestBidₜ = BestBidₜ₋₁ → +(BidQtyₜ − BidQtyₜ₋₁)
- Ask side (inverted):
  - BestAskₜ < BestAskₜ₋₁ → +AskQtyₜ
  - BestAskₜ > BestAskₜ₋₁ → −AskQtyₜ₋₁
  - BestAskₜ = BestAskₜ₋₁ → −(AskQtyₜ − AskQtyₜ₋₁)

**Payload Contract**
- Topic: `{symbol}:microstructure.v1`
- Schema version: `1`
- Payload fields:
  - `ts_event`, `ts_ingest`, `symbol`
  - `best_bid_px`, `best_bid_qty`, `best_ask_px`, `best_ask_qty`
  - `ofi_raw`, `ofi_z`, `ofi_ma5`
  - `spread_bps`, `top_qty_sum`
  - `trade_rate_1s` (nullable), `volume_1s` (nullable)
  - `is_gap`, `is_resynced`

**Reset Semantics**
- On L2 sequence gaps or book resyncs, rolling windows reset.
- Next publish after reset sets `is_gap` or `is_resynced` and clears the flag.

**Config (Hub)**
```bash
MARKET_DATA_MICRO_ENABLED=1
MARKET_DATA_MICRO_OFI_WINDOW_N=50
MARKET_DATA_MICRO_TRADE_WINDOW_SEC=1.0
MARKET_DATA_MICRO_TOPIC_SUFFIX=microstructure.v1
MARKET_DATA_MICRO_ZSCORE_EPS=1e-9
```
