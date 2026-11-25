"""
AI Bridge: Validation layer between GeminiClient and Supervisor.
Ensures responses strictly adhere to the defined Schema.
"""

from __future__ import annotations

import logging
from typing import Dict, Any

from quantum_edge_core.supervisor.supervisor.prompts import JSON_SCHEMA

logger = logging.getLogger(__name__)


class MalformedResponseError(Exception):
    """Raised when AI response does not match the schema."""

    pass


class AiBridge:
    @staticmethod
    def validate_response(response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validates the AI response against the internal schema requirements.
        We perform manual validation (lightweight) instead of pulling in jsonschema lib runtime if possible,
        or use simple checks to ensure Fail-Safe.
        """
        # Basic Type Checks
        if not isinstance(response, dict):
            raise MalformedResponseError("Response must be a JSON object")

        # Required Top Level Keys
        required_keys = ["regime", "action", "params_override", "reasoning"]
        for k in required_keys:
            if k not in response:
                raise MalformedResponseError(f"Missing required key: {k}")

        # Enum Validation
        allowed_regimes = JSON_SCHEMA["properties"]["regime"]["enum"]
        if response["regime"] not in allowed_regimes:
            raise MalformedResponseError(f"Invalid regime: {response['regime']}")

        allowed_actions = JSON_SCHEMA["properties"]["action"]["enum"]
        if response["action"] not in allowed_actions:
            raise MalformedResponseError(f"Invalid action: {response['action']}")

        # Nested Object Keys
        params = response["params_override"]
        if not isinstance(params, dict):
            raise MalformedResponseError("params_override must be an object")

        for pk in ["leverage_cap", "min_order_size"]:
            if pk not in params:
                raise MalformedResponseError(f"Missing params_override key: {pk}")
            try:
                float(params[pk])
            except (ValueError, TypeError):
                raise MalformedResponseError(f"Invalid number for {pk}: {params[pk]}")

        return response
