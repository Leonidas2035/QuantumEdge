import unittest
from supervisor.llm.google_client import GoogleClient

class TestGoogleClient(unittest.TestCase):
    def test_generate_risk_query(self):
        client = GoogleClient(api_key_env="TEST_KEY")
        context = {
            "mode": "SCALP",
            "pnl_pct": -1.2,
            "drawdown_pct": 0.5,
            "volatility": "HIGH",
            "spread_bps": 25
        }
        prompt = client.generate_risk_query(context)
        expected = "SYS:HFT_SUPERVISOR. MODE:SCALP. PNL:-1.2%. DD:0.5%. VOL:HIGH. SPREAD:25bps. Q: RISK_ASSESSMENT? OUTPUT: JSON {verdict: 'CONTINUE'|'REDUCE'|'HALT', reason: '...'}"

        # Check if the prompt matches expected format (allowing for small differences if needed, but here exact match expected)
        # However, f-string formatting might produce slightly different whitespace or float repr if not careful.
        # But our implementation uses f"{pnl:.1f}%". -1.2 -> "-1.2%".

        self.assertEqual(prompt, expected)
        print(f"Prompt verified: {prompt}")

if __name__ == "__main__":
    unittest.main()
