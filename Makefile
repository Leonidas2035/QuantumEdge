.PHONY: engine-env-check engine-quantize engine-build engine-smoke engine-bench engine-versions engine-all

ENGINE_SCRIPTS := src/quantum_edge_ml/inference_engine/scripts

engine-env-check:
	bash $(ENGINE_SCRIPTS)/env_check.sh

engine-quantize:
	bash $(ENGINE_SCRIPTS)/quantize_awq.sh

engine-build:
	bash $(ENGINE_SCRIPTS)/build_engine.sh

engine-smoke:
	python $(ENGINE_SCRIPTS)/smoke_local.py

engine-bench:
	bash $(ENGINE_SCRIPTS)/bench.sh

engine-versions:
	bash $(ENGINE_SCRIPTS)/collect_versions.sh

engine-all: engine-env-check engine-quantize engine-build engine-smoke engine-bench engine-versions
