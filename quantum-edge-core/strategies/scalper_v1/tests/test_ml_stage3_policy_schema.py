from bot.ml.eval.tune_policy import validate_policy_schema


def test_policy_schema_required_fields():
    policy = {
        "symbol": "BTCUSDT",
        "horizons": [1, 5, 30],
        "policy_type": "and_gate",
        "thresholds": {"h1": 0.55, "h5": 0.55, "h30": 0.55},
        "schema_hash": "abc",
        "created_at": "2025-01-01T00:00:00Z",
    }
    assert validate_policy_schema(policy) == []
