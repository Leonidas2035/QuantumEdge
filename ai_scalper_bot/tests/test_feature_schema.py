from bot.ml.features.builder import schema_hash, feature_names
from bot.ml.signal_model.registry import feature_schema_hash


def test_feature_schema_hash_matches():
    expected = schema_hash()
    assert feature_schema_hash() == expected
    assert feature_names()
