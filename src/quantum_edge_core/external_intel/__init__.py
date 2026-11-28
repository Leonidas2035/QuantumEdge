"""External Intelligence Layer (QE-05): read-only global market intelligence."""

from quantum_edge_core.external_intel.coingecko_client import (
    SYMBOL_TO_COINGECKO_ID,
    CoinGeckoIntelClient,
)

__all__ = [
    "CoinGeckoIntelClient",
    "SYMBOL_TO_COINGECKO_ID",
]
