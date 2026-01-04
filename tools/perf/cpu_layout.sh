#!/usr/bin/env bash
set -euo pipefail

join_by() {
  local sep="$1"
  shift
  local first=1
  local result=""
  for value in "$@"; do
    if [ "$first" -eq 1 ]; then
      result="$value"
      first=0
    else
      result="${result}${sep}${value}"
    fi
  done
  printf '%s' "$result"
}

contains() {
  local needle="$1"
  shift
  for hay in "$@"; do
    if [ "$hay" = "$needle" ]; then
      return 0
    fi
  done
  return 1
}

if ! command -v lscpu >/dev/null 2>&1; then
  echo "lscpu is required but missing."
  exit 1
fi

echo "=== CPU topology snapshot ==="
lscpu -e=CPU,CORE,SOCKET,ONLINE,MODEL | column -t

P_CPUS=()
E_CPUS=()
ALL_CPUS=()

for cpu_path in /sys/devices/system/cpu/cpu[0-9]*; do
  cpu_name=$(basename "$cpu_path")
  cpu_id=${cpu_name#cpu}
  if [ ! -f "$cpu_path/online" ]; then
    continue
  fi
  online=$(<"$cpu_path/online")
  if [ "$online" != "1" ]; then
    continue
  fi
  ALL_CPUS+=("$cpu_id")
  core_type=""
  if [ -f "$cpu_path/topology/core_type" ]; then
    core_type=$(<"$cpu_path/topology/core_type")
  elif [ -f "$cpu_path/topology/efficiency_class" ]; then
    eff_class=$(<"$cpu_path/topology/efficiency_class")
    if [ "$eff_class" -le 3 ]; then
      core_type="P"
    else
      core_type="E"
    fi
  fi
  case "${core_type^^}" in
    *P*)
      P_CPUS+=("$cpu_id")
      ;;
    *E*)
      E_CPUS+=("$cpu_id")
      ;;
  esac
done

if [ "${#P_CPUS[@]}" -lt 2 ] || [ "${#E_CPUS[@]}" -lt 2 ]; then
  echo "Warning: P/E detection incomplete; using manual fallback."
  P_CPUS=("${ALL_CPUS[@]:0:2}")
  E_CPUS=("${ALL_CPUS[@]:2}")
fi

hub_candidates=("${P_CPUS[@]:0:2}")
if [ "${#hub_candidates[@]}" -lt 2 ]; then
  hub_candidates=("${ALL_CPUS[@]:0:2}")
fi

bot_candidates=()
for candidate in "${E_CPUS[@]}"; do
  if contains "$candidate" "${hub_candidates[@]}"; then
    continue
  fi
  bot_candidates+=("$candidate")
  if [ "${#bot_candidates[@]}" -ge 8 ]; then
    break
  fi
done

if [ "${#bot_candidates[@]}" -lt 2 ]; then
  for candidate in "${ALL_CPUS[@]}"; do
    if contains "$candidate" "${hub_candidates[@]}" "${bot_candidates[@]}"; then
      continue
    fi
    bot_candidates+=("$candidate")
    if [ "${#bot_candidates[@]}" -ge 4 ]; then
      break
    fi
  done
fi

quest_candidates=()
for candidate in "${E_CPUS[@]}"; do
  if contains "$candidate" "${hub_candidates[@]}" "${bot_candidates[@]}"; then
    continue
  fi
  quest_candidates+=("$candidate")
done

if [ "${#quest_candidates[@]}" -eq 0 ]; then
  for candidate in "${ALL_CPUS[@]}"; do
    if contains "$candidate" "${hub_candidates[@]}" "${bot_candidates[@]}" "${quest_candidates[@]}"; then
      continue
    fi
    quest_candidates+=("$candidate")
    if [ "${#quest_candidates[@]}" -ge 2 ]; then
      break
    fi
  done
fi

hub_list=$(join_by , "${hub_candidates[@]}")
bot_list=$(join_by , "${bot_candidates[@]}")
quest_list=$(join_by , "${quest_candidates[@]}")

echo ""
echo "=== Suggested CPU groups ==="
echo "Hub candidates (P-cores): ${hub_list:-n/a}"
echo "Bot candidates (E-cores): ${bot_list:-n/a}"
echo "QuestDB pool: ${quest_list:-n/a}"

if [ -n "$hub_list" ]; then
  echo ""
  echo "Sample Hub command:"
  echo "  taskset -c ${hub_list} nice -n -5 ./scripts/linux/run.sh meta --config config/meta_agent.yaml"
fi

if [ -n "$bot_list" ]; then
  echo ""
  echo "Sample bot command:"
  echo "  taskset -c ${bot_list} nice -n 5 ./scripts/linux/run_bot.sh --config config/bot.yaml"
fi

if [ -n "$quest_list" ]; then
  echo ""
  echo "Sample QuestDB docker:"
  echo "  docker run --rm --cpuset-cpus=\"${quest_list}\" --memory=16g --name questdb -p 9000:9000 -p 9003:9003 questdb/questdb:latest"
else
  echo ""
  echo "QuestDB pinning: pick cores outside the Hub/bot sets and use --memory=16g."
fi

echo ""
echo "See docs/perf_tsdb.md for full resource profiles and startup guidance."
