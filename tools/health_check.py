import psutil
import socket
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

def check_port(port, name):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('127.0.0.1', port))
    status = "ОК (Відкритий)" if result == 0 else "ПОМИЛКА (Закритий)"
    logging.info(f"Port {port} ({name}): {status}")
    sock.close()

def run_diagnostics():
    logging.info("=== QUANTUM EDGE: SYSTEM HEALTH CHECK ===")
    
    # 1. CPU & RAM
    cpu_usage = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory()
    logging.info(f"CPU Usage: {cpu_usage}%")
    logging.info(f"RAM Usage: {ram.percent}% ({ram.used / (1024**3):.2f}GB / {ram.total / (1024**3):.2f}GB)")
    
    # 2. Disk Space
    disk = psutil.disk_usage('/')
    logging.info(f"Disk Usage: {disk.percent}% ({disk.free / (1024**3):.2f}GB free)")
    
    # 3. Critical Ports
    logging.info("--- Checking Critical Infrastructure ---")
    check_port(5555, "ZMQ Market Data Pub")
    check_port(5557, "ZMQ Supervisor Sub")
    check_port(9000, "QuestDB REST API")
    check_port(9009, "QuestDB ILP Ingest")
    
    logging.info("=========================================")

if __name__ == "__main__":
    run_diagnostics()
