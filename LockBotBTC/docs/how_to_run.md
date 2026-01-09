# LockBotBTC Stage 2 Skeleton

## Run

From repo root:

```bash
python -m LockBotBTC.lockbot_btc.main --config LockBotBTC/config/lockbot_btc.yaml
```

## Required endpoints

- MarketDataHub PUB: `hub_sub_endpoint`
- Supervisor cmd PUB: `supervisor_cmd_sub_endpoint`
- LockBotBTC PUB: `bot_pub_endpoint`

## Sample commands (Supervisor → LockBotBTC)

```json
{
  "cmd": "SET_REGIME",
  "regime": "RANGE",
  "reason": "ops_test"
}
```

```json
{
  "cmd": "PAUSE",
  "reason": "maintenance"
}
```

## Notes

- All market data must come from MarketDataHub topics.
- No trading/execution logic is enabled at this stage.
