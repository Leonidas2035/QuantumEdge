from __future__ import annotations

import argparse
import json
import os
import random
from typing import Iterable, List

BASE_TEMPLATES = [
    "Summarize the supervisor action for: {event}.",
    "Classify risk level (low/medium/high) for: {event}.",
    "Rewrite as structured JSON with keys time, symptom, action: {event}.",
    "Explain in one sentence: {event}.",
    "Given policy snippet {snippet}, list missing required keys.",
    "Provide a concise mitigation checklist for: {event}.",
]

EVENTS = [
    "p95 latency above 300ms",
    "orderbook feed stale for 10 seconds",
    "funding rate spike to 0.6%",
    "retry storm in ingestion pipeline",
    "risk check failed due to drawdown limit",
    "unexpected GPU OOM during build",
]

SNIPPETS = [
    '{"risk_limit": 0.02, "max_orders": 5}',
    '{"max_position": 1.0, "slippage_bps": 12}',
    '{"allow": false, "reason": ""}',
]


def load_existing(path: str) -> List[str]:
    if not os.path.exists(path):
        return []
    prompts: List[str] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = data.get("text") or data.get("prompt")
            if text:
                prompts.append(text)
    return prompts


def generate_prompts(count: int, seed: int | None = None) -> List[str]:
    rng = random.Random(seed)
    prompts: List[str] = []
    while len(prompts) < count:
        template = rng.choice(BASE_TEMPLATES)
        event = rng.choice(EVENTS)
        snippet = rng.choice(SNIPPETS)
        prompts.append(template.format(event=event, snippet=snippet))
    return prompts


def write_jsonl(path: str, prompts: Iterable[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for prompt in prompts:
            record = {"text": prompt, "prompt": prompt}
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare AWQ calibration prompts JSONL")
    parser.add_argument("--out", default=os.environ.get("CALIB_JSONL", ""))
    parser.add_argument("--size", type=int, default=int(os.environ.get("CALIB_SIZE", "64")))
    parser.add_argument("--seed", type=int, default=123)
    args = parser.parse_args()

    if not args.out:
        raise SystemExit("CALIB_JSONL not set and --out not provided")

    existing = load_existing(args.out)
    remaining = max(0, args.size - len(existing))
    prompts = existing + generate_prompts(remaining, seed=args.seed)
    write_jsonl(args.out, prompts[: args.size])
    print(f"Wrote {min(args.size, len(prompts))} prompts to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
