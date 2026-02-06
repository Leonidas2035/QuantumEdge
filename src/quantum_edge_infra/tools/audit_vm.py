import os
import platform
import psutil
import subprocess

def get_cpu_info():
    print("--- CPU Architecture & Instructions ---")
    print(f"Arch: {platform.machine()}")
    print(f"Physical Cores: {psutil.cpu_count(logical=False)}")
    print(f"Logical Cores: {psutil.cpu_count(logical=True)}")
    
    # Check for AVX/AVX2/AVX512 (Critical for TensorRT-LLM )
    try:
        if platform.system() == "Linux":
            with open('/proc/cpuinfo') as f:
                cpuinfo = f.read()
                flags = [line for line in cpuinfo.split('\n') if line.startswith('flags')]
                if flags:
                    flag_list = flags[0].split()
                    relevant = ['avx', 'avx2', 'avx512f', 'fma']
                    found = [f for f in relevant if f in flag_list]
                    print(f"Vector Instructions Supported: {', '.join(found)}")
                else:
                    print("Could not read flags from /proc/cpuinfo")
    except Exception as e:
        print(f"Error reading CPU flags: {e}")

def get_memory_topology():
    print("\n--- Memory & NUMA (Critical for ZeroMQ Latency) ---")
    print(f"Total RAM: {psutil.virtual_memory().total / (1024**3):.2f} GB")
    print(f"Available RAM: {psutil.virtual_memory().available / (1024**3):.2f} GB")
    
    # Check NUMA nodes using lscpu if available
    try:
        result = subprocess.run(['lscpu'], capture_output=True, text=True)
        for line in result.stdout.split('\n'):
            if "NUMA" in line:
                print(line)
            if "L3 cache" in line:
                print(f"L3 Cache: {line.split(':')[-1].strip()}")
    except FileNotFoundError:
        print("lscpu tool not found. Cannot determine NUMA topology.")

def get_io_stats():
    print("\n--- Disk I/O (Critical for QuestDB ) ---")
    # Basic check - in HFT we prefer NVMe
    # This is a heuristic check for rotational drives
    if platform.system() == "Linux":
        os.system("lsblk -d -o name,rota")

if __name__ == "__main__":
    print(f"QuantumEdge System Audit [Time: {psutil.datetime.datetime.now()}]")
    get_cpu_info()
    get_memory_topology()
    get_io_stats()