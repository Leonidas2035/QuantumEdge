from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from model_router.contracts.decision_v1 import DecisionV1, decode_decision, fallback_decision
from model_router.decoding.enforce import enforce_decision
from model_router.router.budgets import BudgetConfig, TeacherBudgets
from model_router.router.cache import RouterCache
from model_router.router.circuit import CircuitBreaker, CircuitConfig
from model_router.router.distill import DistillConfig, DistillWriter
from model_router.router.policy import RouterPolicy
from model_router.router.redaction import RedactionResult, redact_prompt


@dataclass
class RouterResult:
    decision: DecisionV1
    ok: bool
    backend: str
    latency_ms: float
    cache_hit: bool
    error: Optional[str]
    attempts: int


def _utc_now_str() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _day_bucket(now_utc: str) -> str:
    return now_utc[:10].replace("-", "")


def _approx_tokens(text: str) -> int:
    return max(1, int(len(text) / 4))


def _make_cache_key(schema: str, model_id: str, prompt_hash: str, mode: str) -> str:
    key = f"{schema}:{model_id}:{prompt_hash}:{mode}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _load_bool(env_name: str, default: str) -> bool:
    return os.environ.get(env_name, default) == "1"


class Router:
    def __init__(
        self,
        student_backend,
        teacher_backend,
        runtime_dir: Optional[Path] = None,
    ) -> None:
        self.student_backend = student_backend
        self.teacher_backend = teacher_backend
        self.runtime_dir = runtime_dir or Path(__file__).resolve().parents[1] / "runtime"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

        ttl_s = int(os.environ.get("SUPERVISOR_CACHE_TTL_S", "600"))
        cache_path = self.runtime_dir / "cache" / "router_cache.sqlite"
        self.cache = RouterCache(cache_path, ttl_s)

        budgets = BudgetConfig(
            max_requests_per_day=int(os.environ.get("TEACHER_MAX_REQ_PER_DAY", "200")),
            max_tokens_per_day=int(os.environ.get("TEACHER_MAX_TOKENS_PER_DAY", "200000")),
        )
        self.budgets = TeacherBudgets(self.runtime_dir / "teacher_budgets.json", budgets)

        circuit_conf = CircuitConfig(
            failure_threshold=int(os.environ.get("ROUTER_CIRCUIT_FAILURES", "3")),
            window_s=int(os.environ.get("ROUTER_CIRCUIT_WINDOW_S", "60")),
            cool_down_s=int(os.environ.get("ROUTER_CIRCUIT_COOLDOWN_S", "120")),
        )
        state_path = self.runtime_dir / "circuit_state.json"
        self.student_circuit = CircuitBreaker("student", circuit_conf, state_path=state_path)
        self.teacher_circuit = CircuitBreaker("teacher", circuit_conf, state_path=state_path)

        distill_cfg = DistillConfig(
            enable=_load_bool("DISTILL_ENABLE", "1"),
            store_prompt=_load_bool("DISTILL_STORE_PROMPT", "0"),
        )
        distill_path = self.runtime_dir / "distill" / "teacher_student_pairs.jsonl"
        self.distill = DistillWriter(distill_path, distill_cfg)

        self.events_path = self.runtime_dir / "router_events.jsonl"

    async def route(
        self,
        prompt: str,
        timeout_s: float = 2.0,
        request_id: Optional[str] = None,
        hints: Optional[Dict] = None,
    ) -> RouterResult:
        policy = RouterPolicy.from_env()
        hints = hints or {}
        policy = policy.with_hints(
            mode=hints.get("mode"),
            teacher_ratio=hints.get("teacher_ratio"),
            force_teacher=hints.get("force_teacher"),
        )
        now_utc = _utc_now_str()
        prompt_info = redact_prompt(prompt, store_prompt=self.distill.config.store_prompt)
        cache_key = _make_cache_key("DecisionV1", self._student_model_id(), prompt_info.prompt_hash, policy.mode)

        cache_entry = self.cache.get(cache_key)
        if cache_entry:
            try:
                decision = decode_decision(cache_entry.decision_json)
                result = RouterResult(
                    decision=decision,
                    ok=True,
                    backend=cache_entry.backend,
                    latency_ms=0.0,
                    cache_hit=True,
                    error=None,
                    attempts=0,
                )
                self._log_event(result, request_id, prompt_info.prompt_hash, policy.mode, cache_hit=True)
                return result
            except Exception:
                self.cache.delete(cache_key)

        start = time.perf_counter()
        cache_hit = False
        attempts = 0
        error = None

        student_result = None
        teacher_result = None
        chosen = None

        async def call_student():
            nonlocal attempts
            if self.student_circuit.is_open(time.time()):
                return None
            attempts += 1
            result = await enforce_decision(prompt, self.student_backend, timeout_s=timeout_s)
            if result.ok:
                self.student_circuit.record_success(time.time())
            else:
                self.student_circuit.record_failure(time.time())
            return result

        async def call_teacher():
            nonlocal attempts
            if self.teacher_circuit.is_open(time.time()):
                return None
            tokens_est = _approx_tokens(prompt)
            if not self.budgets.can_use(now_utc, tokens_est):
                return None
            attempts += 1
            result = await enforce_decision(prompt, self.teacher_backend, timeout_s=timeout_s)
            tokens_used = tokens_est + _approx_tokens(result.decision.to_compact_json())
            self.budgets.record(now_utc, tokens_used)
            if result.ok:
                self.teacher_circuit.record_success(time.time())
            else:
                self.teacher_circuit.record_failure(time.time())
            return result

        if policy.force_teacher:
            teacher_result = await call_teacher()
            chosen = teacher_result
        elif policy.mode == "shadow":
            student_result = await call_student()
            teacher_result = await call_teacher()
            chosen = student_result or teacher_result
        elif policy.mode == "fallback":
            student_result = await call_student()
            if student_result and student_result.ok:
                chosen = student_result
            else:
                teacher_result = await call_teacher()
                chosen = teacher_result or student_result
        elif policy.mode == "ab":
            use_teacher = self._choose_teacher(prompt_info.prompt_hash, policy.teacher_ratio, _day_bucket(now_utc))
            if use_teacher:
                teacher_result = await call_teacher()
                if teacher_result:
                    chosen = teacher_result
                else:
                    student_result = await call_student()
                    chosen = student_result
            else:
                student_result = await call_student()
                chosen = student_result
        else:  # local_first
            student_result = await call_student()
            if student_result and student_result.ok:
                chosen = student_result
            else:
                teacher_result = await call_teacher()
                chosen = teacher_result or student_result

        if chosen is None:
            decision = fallback_decision("parse_fail")
            ok = False
            backend_used = "fallback"
            error = "no_backend"
            attempts = max(attempts, 1)
        else:
            decision = chosen.decision
            ok = chosen.ok
            backend_used = "teacher" if chosen is teacher_result else "student"
            error = chosen.error

        latency_ms = (time.perf_counter() - start) * 1000.0
        result = RouterResult(
            decision=decision,
            ok=ok,
            backend=backend_used,
            latency_ms=latency_ms,
            cache_hit=cache_hit,
            error=error,
            attempts=attempts,
        )

        if ok:
            self.cache.set(cache_key, decision.to_compact_json(), backend_used)

        self._log_event(result, request_id, prompt_info.prompt_hash, policy.mode, cache_hit=cache_hit)
        self._maybe_log_distill(prompt_info, student_result, teacher_result)
        return result

    def _choose_teacher(self, prompt_hash: str, ratio: float, day_bucket: str) -> bool:
        ratio = min(max(ratio, 0.0), 1.0)
        key = f"{prompt_hash}:{day_bucket}"
        val = int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16)
        return (val % 10000) < int(ratio * 10000)

    def _student_model_id(self) -> str:
        return os.environ.get("SUPERVISOR_LLM_MODEL", "local")

    def _log_event(self, result: RouterResult, request_id: Optional[str], prompt_hash: str, mode: str, cache_hit: bool) -> None:
        payload = {
            "ts_utc": _utc_now_str(),
            "request_id": request_id,
            "prompt_hash": prompt_hash,
            "mode": mode,
            "chosen_backend": result.backend,
            "attempts": result.attempts,
            "ok": result.ok,
            "latency_ms": round(result.latency_ms, 2),
            "cache_hit": cache_hit,
            "error_code": result.error,
        }
        with open(self.events_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n")

    def _maybe_log_distill(self, prompt_info: RedactionResult, student_result, teacher_result) -> None:
        if not student_result or not teacher_result:
            return
        student_payload = self.distill.make_payload(
            student_result.ok,
            student_result.decision.to_compact_json(),
            getattr(student_result, "raw_text", ""),
        )
        teacher_payload = self.distill.make_payload(
            teacher_result.ok,
            teacher_result.decision.to_compact_json(),
            getattr(teacher_result, "raw_text", ""),
        )
        diff = {
            "same_action": student_result.decision.s == teacher_result.decision.s,
            "confidence_delta": teacher_result.decision.c - student_result.decision.c,
            "risk_delta": f"{student_result.decision.rk}->{teacher_result.decision.rk}",
            "notes": "shadow" if student_result.ok and teacher_result.ok else "partial",
        }
        backend_meta = {
            "student_model": self._student_model_id(),
            "teacher_model": os.environ.get("OPENAI_MODEL", "unknown"),
            "lat_ms_student": round(student_result.latency_ms, 2),
            "lat_ms_teacher": round(teacher_result.latency_ms, 2),
        }
        self.distill.write(prompt_info, student_payload, teacher_payload, diff, backend_meta)
