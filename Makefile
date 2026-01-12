.PHONY: engine-env-check engine-quantize engine-build engine-smoke engine-bench engine-versions engine-all

engine-env-check:
	bash llm_engine/scripts/env_check.sh

engine-quantize:
	bash llm_engine/scripts/quantize_awq.sh

engine-build:
	bash llm_engine/scripts/build_engine.sh

engine-smoke:
	python llm_engine/scripts/smoke_local.py

engine-bench:
	bash llm_engine/scripts/bench.sh

engine-versions:
	bash llm_engine/scripts/collect_versions.sh

engine-all: engine-env-check engine-quantize engine-build engine-smoke engine-bench engine-versions
