"""Utility to collect and persist LLM interactions in JSONL format for dataset training."""

import json
import logging
import time
from pathlib import Path
from typing import Any, List, Mapping

logger = logging.getLogger("hermes.dataset_collector")


def collect_llm_sample(
    messages: List[Mapping[str, str]],
    response: str,
    model_name: str,
    dataset_file_path: str = "runtime/gemma_dataset.jsonl",
) -> None:
    """Appends an LLM completion interaction to a JSONL dataset file.

    Format matches the standard ChatML structure for model finetuning:
    {
      "messages": [
        {"role": "system", "content": "..." },
        {"role": "user", "content": "..." },
        {"role": "assistant", "content": "..." }
      ],
      "metadata": {
        "timestamp": "2026-06-18T00:15:00Z",
        "model": "..."
      }
    }
    """
    try:
        path = Path(dataset_file_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Build ChatML structure
        formatted_messages = []
        for msg in messages:
            formatted_messages.append(
                {
                    "role": str(msg.get("role", "user")),
                    "content": str(msg.get("content", "")),
                }
            )

        # Append the assistant response
        formatted_messages.append(
            {
                "role": "assistant",
                "content": response,
            }
        )

        sample = {
            "messages": formatted_messages,
            "metadata": {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "model": model_name,
            },
        }

        # Write to JSONL
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(sample, ensure_ascii=False) + "\n")

        logger.info("Successfully saved LLM interaction sample to %s", path)
    except Exception as exc:
        # Non-blocking: log the warning but never fail the main loop/supervisor
        logger.warning("Failed to save LLM interaction sample to dataset: %s", exc)
