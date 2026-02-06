#!/bin/bash
# Зберігаємо як: tools/setup_hft_env.sh

echo "[QuantumEdge] Initializing HFT Environment..."

# 1. Створюємо RAM-диски для IPC (ZeroMQ) та логів
# /dev/shm - це Shared Memory (RAM), швидкість як у оперативки
mkdir -p /dev/shm/quantum_ipc
mkdir -p /dev/shm/quantum_logs

# 2. Даємо права на запис
chmod 777 /dev/shm/quantum_ipc
chmod 777 /dev/shm/quantum_logs

# 3. Експорт змінних оточення (щоб Python знав, куди писати)
export QE_IPC_DIR="/dev/shm/quantum_ipc"
export QE_LOG_DIR="/dev/shm/quantum_logs"

echo "[OK] Environment ready."
echo "IPC Path: $QE_IPC_DIR"
echo "Log Path: $QE_LOG_DIR"