from __future__ import annotations

import argparse
import json
import os
import statistics
import threading
import time
from dataclasses import dataclass
from typing import List, Optional


def _try_import_trt() -> bool:
    try:
        import tensorrt_llm  # noqa: F401

        return True
    except Exception:
        return False


@dataclass
class RunResult:
    ttft_ms: float
    latency_ms: float
    tokens_per_s: float
    peak_vram_mb: Optional[float]


def _vram_sampler(stop_event: threading.Event, samples: List[int]) -> None:
    try:
        import pynvml
    except Exception:
        return
    try:
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        while not stop_event.is_set():
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            samples.append(int(info.used))
            time.sleep(0.05)
    except Exception:
        return
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass


def _run_once(
    runner,
    tokenizer,
    prompt: str,
    max_new_tokens: int,
    track_vram: bool,
) -> RunResult:
    stop_event = threading.Event()
    samples: List[int] = []
    sampler = None
    if track_vram:
        sampler = threading.Thread(target=_vram_sampler, args=(stop_event, samples), daemon=True)
        sampler.start()

    input_ids = tokenizer(prompt, return_tensors="pt").input_ids
    start = time.perf_counter()
    ttft = None

    try:
        outputs = runner.generate(
            input_ids=input_ids,
            max_new_tokens=max_new_tokens,
            end_id=getattr(tokenizer, "eos_token_id", None),
            pad_id=getattr(tokenizer, "eos_token_id", None),
            streaming=True,
        )
        generated = []
        for idx, output in enumerate(outputs):
            if idx == 0:
                ttft = (time.perf_counter() - start) * 1000.0
            generated = output
    except TypeError:
        outputs = runner.generate(
            input_ids=input_ids,
            max_new_tokens=max_new_tokens,
            end_id=getattr(tokenizer, "eos_token_id", None),
            pad_id=getattr(tokenizer, "eos_token_id", None),
        )
        generated = outputs[0] if isinstance(outputs, (list, tuple)) else outputs

    total = (time.perf_counter() - start) * 1000.0
    if ttft is None:
        ttft = total

    stop_event.set()
    if sampler is not None:
        sampler.join(timeout=1.0)

    input_len = int(input_ids.shape[-1])
    output_len = int(getattr(generated, "shape", [0])[-1]) if hasattr(generated, "shape") else len(generated)
    gen_tokens = max(0, output_len - input_len)
    tokens_per_s = (gen_tokens / (total / 1000.0)) if total > 0 else 0.0

    peak_vram_mb = None
    if samples:
        peak_vram_mb = max(samples) / (1024 * 1024)

    return RunResult(ttft_ms=ttft, latency_ms=total, tokens_per_s=tokens_per_s, peak_vram_mb=peak_vram_mb)


def build_long_prompt(tokenizer, target_tokens: int) -> str:
    base = "Supervisor note: verify risk limits and latency budget. "
    tokens = tokenizer.encode(base)
    text = base
    while len(tokens) < target_tokens:
        text += "Supervisor check ok. "
        tokens = tokenizer.encode(text)
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test TensorRT-LLM engine")
    parser.add_argument("--engine-dir", default=os.environ.get("ENGINE_DIR", ""))
    parser.add_argument("--model-dir", default=os.environ.get("MODEL_DIR", ""))
    parser.add_argument("--max-input-len", type=int, default=int(os.environ.get("MAX_INPUT_LEN", "2048")))
    parser.add_argument("--max-output-len", type=int, default=int(os.environ.get("MAX_OUTPUT_LEN", "512")))
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--bench-json", default="")
    args = parser.parse_args()

    if not args.engine_dir:
        raise SystemExit("ENGINE_DIR is required")
    if not args.model_dir:
        raise SystemExit("MODEL_DIR is required")
    if not _try_import_trt():
        raise SystemExit("tensorrt_llm not available. Run inside TRT-LLM container.")

    try:
        from tensorrt_llm.runtime import ModelRunner
    except Exception as exc:
        raise SystemExit(f"Failed to import TRT-LLM runtime: {exc}")

    try:
        from transformers import AutoTokenizer
    except Exception as exc:
        raise SystemExit(f"transformers not available: {exc}")

    tokenizer = AutoTokenizer.from_pretrained(args.model_dir, use_fast=True)
    runner = ModelRunner.from_dir(engine_dir=args.engine_dir, rank=0)

    prompt = "Summarize the risk check for a 5% drawdown in two sentences."
    near_limit = build_long_prompt(tokenizer, max(16, args.max_input_len - 32))

    results: List[RunResult] = []
    for i in range(args.iterations):
        label = "near-limit" if i % 2 == 1 else "short"
        use_prompt = near_limit if label == "near-limit" else prompt
        result = _run_once(runner, tokenizer, use_prompt, args.max_output_len, track_vram=True)
        results.append(result)
        print(
            f"[{label}] ttft_ms={result.ttft_ms:.2f} "
            f"latency_ms={result.latency_ms:.2f} tokens_per_s={result.tokens_per_s:.2f} "
            f"peak_vram_mb={result.peak_vram_mb if result.peak_vram_mb is not None else 'n/a'}"
        )

    if args.bench_json:
        ttft = [r.ttft_ms for r in results]
        lat = [r.latency_ms for r in results]
        tps = [r.tokens_per_s for r in results]
        peak_vram = max([r.peak_vram_mb or 0 for r in results]) if results else 0.0

        summary = {
            "ttft_ms": ttft,
            "latency_ms": lat,
            "tokens_per_s": tps,
            "peak_vram_mb": peak_vram,
            "p50": {
                "ttft_ms": statistics.median(ttft),
                "latency_ms": statistics.median(lat),
                "tokens_per_s": statistics.median(tps),
            },
            "p95": {
                "ttft_ms": statistics.quantiles(ttft, n=20)[-1] if len(ttft) >= 20 else max(ttft),
                "latency_ms": statistics.quantiles(lat, n=20)[-1] if len(lat) >= 20 else max(lat),
                "tokens_per_s": statistics.quantiles(tps, n=20)[-1] if len(tps) >= 20 else max(tps),
            },
        }
        with open(args.bench_json, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
