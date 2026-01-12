# LLM Engine Stage 1 - TensorRT-LLM INT4 AWQ (Gemma 3 4B text-only)

Stage 1 builds a standalone TensorRT-LLM engine for Gemma 3 4B (text-only) using INT4 AWQ, tuned for 8GB VRAM and batch=1 latency. No bot integration in this stage.

## Prerequisites

- NVIDIA GPU with >= 8GB VRAM (tested target: RTX 4060)
- NVIDIA driver + CUDA compatible with TensorRT-LLM container
- Docker with GPU support (preferred)
- Text-only Gemma 3 4B model weights locally available

## Model download (text-only)

Gemma 3 4B releases often include vision-language components. Stage 1 expects a text-only CausalLM model directory.

Example (requires `huggingface-cli` and proper access rights). This is a text-only converted variant; you can point to any text-only Gemma 3 4B model you have access to:

```bash
export HF_MODEL_ID=gghfez/gemma-3-4b-novision
bash llm_engine/scripts/download_model.sh
```

## Quickstart (one-command path)

```bash
source llm_engine/configs/engine_defaults.env
bash llm_engine/scripts/env_check.sh
bash llm_engine/scripts/quantize_awq.sh
bash llm_engine/scripts/build_engine.sh
python llm_engine/scripts/smoke_local.py
bash llm_engine/scripts/bench.sh
bash llm_engine/scripts/collect_versions.sh
```

Makefile alternative:

```bash
make engine-all
```

## Container usage

We recommend running the build inside the official NGC Triton + TRT-LLM python container. `NGC_IMAGE` is pinned in `llm_engine/configs/engine_defaults.env` and should match your driver/CUDA version.

Driver compatibility (from the Triton matrix):

| Triton tag | Min driver |
| --- | --- |
| 25.12-trtllm-python-py3 | 580.95.05 |
| 25.01-trtllm-python-py3 | ~570.86.10 |
| 24.12-trtllm-python-py3 | ~560.35.05 |

How to select a tag without guessing:
- Visit NGC: https://catalog.ngc.nvidia.com/orgs/nvidia/containers/tritonserver
- Filter for tags that include `trtllm-python`.
- Choose a stable monthly tag that matches your driver/CUDA version.

Example docker run (used internally by scripts when `USE_DOCKER=1`):

```bash
docker run --rm --gpus all \
  -v "$(pwd):/workspace" \
  -v "${MODEL_DIR}:/models:ro" \
  -w /workspace \
  -e MODEL_DIR=/models \
  ${NGC_IMAGE} bash
```

## Artifacts

- Quantized checkpoint: `llm_engine/artifacts/ckpt_gemma3_4b_awq_int4/`
- TRT-LLM engine: `llm_engine/artifacts/engine_gemma3_4b_awq_int4/`
- Logs: `llm_engine/artifacts/logs/`
- Reports: `llm_engine/artifacts/reports/`

## Serve

Start a local server when `trtllm-serve` is available:

```bash
bash llm_engine/scripts/serve.sh
```

If `trtllm-serve` is not present in your TRT-LLM version, use `python llm_engine/scripts/smoke_local.py` for local validation.

## Troubleshooting

- OOM during quantization/build: reduce `CALIB_SIZE`, lower `CALIB_SEQ_LEN`, reduce `MAX_INPUT_LEN`, and ensure paged KV cache is enabled.
- Driver/container mismatch: update driver or use a container tag compatible with your CUDA version.
- Paged KV flags changed: `build_engine.sh` auto-detects `--kv_cache_type paged` vs `--paged_kv_cache enable`.
- Quantize flags changed: update `scripts/quantize_awq.sh` to match your TRT-LLM version.

## Notes

- NGC image pinned to `25.12-trtllm-python-py3` with driver compatibility warning in `env_check.sh`.
- Text-only Gemma 3 4B guidance clarified; use a text-only CausalLM model dir.

## Lint (optional)

```bash
pip install ruff
ruff check llm_engine/scripts supervisor_llm
```

## Acceptance criteria

Stage 1 is complete when:
- INT4 AWQ checkpoint and TRT-LLM engine build successfully.
- `smoke_local.py` runs short and near-limit prompts without errors.
- `bench.sh` produces `bench_results.json` and `baseline.md`.
- `collect_versions.sh` captures environment versions.
