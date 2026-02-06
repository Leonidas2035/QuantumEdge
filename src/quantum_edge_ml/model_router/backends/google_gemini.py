from __future__ import annotations

import os

class GoogleGeminiBackend:
    def __init__(self, transport=None) -> None:
        self.api_key = os.environ.get("GOOGLE_API_KEY", "")
        self.model = os.environ.get("GOOGLE_MODEL", "gemini-1.5-flash")
        self.max_tokens = int(os.environ.get("GOOGLE_MAX_TOKENS", "128"))
        self.name = "google_gemini"
        self._transport = transport
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/models"

    def _get_client(self, timeout_s: float):
        try:
            import httpx
        except Exception as exc:
            raise RuntimeError("httpx is required for Google Gemini backend") from exc

        if not self.api_key:
            raise RuntimeError("GOOGLE_API_KEY is required")
        
        return httpx.AsyncClient(timeout=timeout_s, transport=self._transport)

    async def generate(self, prompt: str, *, system_prompt: str, timeout_s: float) -> str:
        url = f"{self.base_url}/{self.model}:generateContent?key={self.api_key}"
        
        # mimic system prompt by expecting it in the user prompt or prepend
        # Gemini doesn't always have a strict system role in 'generateContent' the same way as OpenAI 'messages'
        # But we can just use the user prompt if system prompt is simple, or prepend it.
        # "system_instruction" is supported in newer models but simple "parts" text is safest.
        
        full_text = f"System: {system_prompt}\nUser: {prompt}"
        
        payload = {
            "contents": [{
                "parts": [{"text": full_text}]
            }],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": self.max_tokens,
            }
        }
        
        async with self._get_client(timeout_s) as client:
            resp = await client.post(url, json=payload)

        if resp.status_code != 200:
             # Basic error handling
            raise RuntimeError(f"gemini_error:{resp.status_code} {resp.text}")

        data = resp.json()
        
        try:
            # Safely extract text
            candidates = data.get("candidates")
            if not candidates:
                 # Check for block/safety feedback
                if data.get("promptFeedback", {}).get("blockReason"):
                    raise RuntimeError("gemini_blocked_prompt")
                raise RuntimeError("gemini_empty_response")
            
            content_parts = candidates[0].get("content", {}).get("parts", [])
            if not content_parts:
                raise RuntimeError("gemini_empty_parts")
                
            text = content_parts[0].get("text", "")
            if not text:
                raise RuntimeError("gemini_empty_text")
                
            return text.strip()
        except Exception as e:
             if isinstance(e, RuntimeError):
                 raise e
             raise RuntimeError(f"gemini_parse_error:{e}")
