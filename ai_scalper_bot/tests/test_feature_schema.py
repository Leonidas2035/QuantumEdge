import json
import hashlib

from bot.ml.feature_schema import FEATURE_NAMES
from bot.ml.signal_model.registry import feature_schema_hash

def test_feature_schema_hash_matches():
    payload = json.dumps(FEATURE_NAMES, separators=(",", ":"))
    expected = hashlib.sha256(payload.encode()).hexdigest()
    assert feature_schema_hash() == expected
