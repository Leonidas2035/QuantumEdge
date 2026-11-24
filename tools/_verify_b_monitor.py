import sys, asyncio
sys.path.insert(0, '/home/layman/QuantumEdge')
import psutil

# Replicate the signature logic from QuantumEdge.py to verify detection without
# importing the whole orchestrator (which binds ZMQ port 5560).
signatures = {
    "ai_scalper": ("ai_scalper_bot", "run_bot"),
    "dyndca": ("dyn_dca_bot",),
    "lockbotbtc": ("lock_bot",),
    "supervisor": ("supervisor",),
    "hub": ("market_data", "hub"),
}

def find(name):
    sig = signatures.get(name)
    if not sig:
        return None
    for proc in psutil.process_iter(["pid", "cmdline", "name"]):
        try:
            cl = " ".join(proc.info.get("cmdline") or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if all(t in cl for t in sig) and proc.is_running():
            return proc
    return None

for n in signatures:
    p = find(n)
    print(f"{n:12} -> {'PID '+str(p.pid) if p else 'NOT FOUND'}")
