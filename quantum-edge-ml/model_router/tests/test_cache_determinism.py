from __future__ import annotations

from model_router.router import cache as cache_module
from model_router.router.cache import RouterCache


def test_cache_determinism(monkeypatch, tmp_path):
    cache_obj = RouterCache(tmp_path / "cache.sqlite", ttl_s=10)
    cache_obj.set("k", "{\"v\":1}", "student")

    entry = cache_obj.get("k")
    assert entry is not None

    original_time = cache_module.time.time
    monkeypatch.setattr(cache_module.time, "time", lambda: original_time() + 20)
    assert cache_obj.get("k") is None
