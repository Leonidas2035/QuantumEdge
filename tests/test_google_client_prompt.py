import os
import unittest
from unittest.mock import patch

import pytest

pytest.importorskip("google.genai")

from quantum_edge_core.supervisor.supervisor.llm.google_client import GoogleClient


class TestGoogleClient(unittest.TestCase):
    def setUp(self):
        # Set dummy key for validation
        os.environ["GOOGLE_API_KEY"] = "dummy"

    @patch("google.generativeai.GenerativeModel")
    @patch("google.generativeai.configure")
    def test_generate_risk_query(self, mock_configure, mock_model):
        client = GoogleClient(api_key_env="TEST_KEY")
        context = {
            "mode": "SCALP",
            "pnl_pct": -1.2,
            "drawdown_pct": 0.5,
            "volatility": "HIGH",
            "spread_bps": 25,
        }
        prompt = client.generate_risk_query(context)
        expected = (
            "SYS:HFT_SUPERVISOR. MODE:SCALP. PNL:-1.2%. DD:0.5%. VOL:HIGH. "
            "SPREAD:25bps. Q: RISK_ASSESSMENT? OUTPUT: JSON "
            "{verdict: 'CONTINUE'|'REDUCE'|'HALT', reason: '...'}"
        )

        self.assertEqual(prompt, expected)
        print(f"Prompt verified: {prompt}")


if __name__ == "__main__":
    unittest.main()
