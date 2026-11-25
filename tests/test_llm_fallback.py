import unittest
import json
from unittest.mock import patch, MagicMock, AsyncMock
from urllib.error import HTTPError
import io

from quantum_edge_core.supervisor.supervisor.llm_supervisor import (
    ChatCompletionsClient as SupervisorClient,
)
from quantum_edge_core.supervisor.supervisor.llm.chat_client import (
    ChatCompletionsClient as StandardClient,
)


class TestLlmFallback(unittest.IsolatedAsyncioTestCase):

    @patch("httpx.AsyncClient.post")
    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    async def test_supervisor_client_fallback_on_404_tools(self, mock_post):
        """
        Verify that ChatCompletionsClient in llm_supervisor.py falls back
        and retries without 'response_format' if the endpoint returns a 404
        indicating unsupported tool/format support.
        """
        # First call: returns 404 with unsupported tools msg
        # Second call: returns 200 with normal OpenAI content structure
        mock_404_resp = MagicMock()
        mock_404_resp.status_code = 404
        mock_404_resp.text = '{"message": "No endpoints found that support tool use."}'

        mock_200_resp = MagicMock()
        mock_200_resp.status_code = 200
        mock_200_resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"trading_mode": "scalp", "risk_multiplier": 1.0}'
                    }
                }
            ]
        }

        mock_post.side_effect = [mock_404_resp, mock_200_resp]

        client = SupervisorClient(
            api_url="https://custom.endpoint.ai/api/v1/chat/completions",
            api_key_env="GOOGLE_API_KEY",
        )

        messages = [{"role": "user", "content": "Analyze state"}]
        schema = {"type": "object", "properties": {}}

        result = await client.complete_async(
            model="custom-model",
            messages=messages,
            temperature=0.0,
            timeout_seconds=30.0,
            response_schema=schema,
        )

        self.assertEqual(result, '{"trading_mode": "scalp", "risk_multiplier": 1.0}')
        self.assertEqual(mock_post.call_count, 2)

        # First call should have had response_format
        first_call_json = mock_post.call_args_list[0].kwargs["json"]
        self.assertIn("response_format", first_call_json)

        # Second call (fallback) should NOT have had response_format
        second_call_json = mock_post.call_args_list[1].kwargs["json"]
        self.assertNotIn("response_format", second_call_json)

    @patch("urllib.request.urlopen")
    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    def test_standard_client_fallback_on_404_tools(self, mock_urlopen):
        """
        Verify that ChatCompletionsClient in chat_client.py falls back
        and retries without 'response_format' if the endpoint raises an HTTPError
        indicating unsupported tool/format support.
        """
        # First call: raises HTTPError
        fp = io.BytesIO(b'{"message": "No endpoints found that support tool use."}')
        http_err = HTTPError(
            url="http://test.url",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=fp,
        )

        # Second call: returns mock response context manager
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value.read.return_value = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"trading_mode": "scalp", "risk_multiplier": 1.0}'
                        }
                    }
                ]
            }
        ).encode("utf-8")

        mock_urlopen.side_effect = [http_err, mock_resp]

        client = StandardClient(
            api_url="https://custom.endpoint.ai/api/v1/chat/completions",
            api_key_env="GOOGLE_API_KEY",
        )

        messages = [{"role": "user", "content": "Analyze state"}]
        schema = {"type": "object", "properties": {}}

        result = client.complete(
            model="custom-model",
            messages=messages,
            temperature=0.0,
            timeout_seconds=30.0,
            response_schema=schema,
        )

        self.assertEqual(result, '{"trading_mode": "scalp", "risk_multiplier": 1.0}')
        self.assertEqual(mock_urlopen.call_count, 2)

        # First call payload should have response_format
        first_call_data = json.loads(
            mock_urlopen.call_args_list[0][0][0].data.decode("utf-8")
        )
        self.assertIn("response_format", first_call_data)

        # Second call payload should NOT have response_format
        second_call_data = json.loads(
            mock_urlopen.call_args_list[1][0][0].data.decode("utf-8")
        )
        self.assertNotIn("response_format", second_call_data)


if __name__ == "__main__":
    unittest.main()
