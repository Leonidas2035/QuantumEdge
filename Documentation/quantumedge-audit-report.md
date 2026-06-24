# QuantumEdge — Audit & Unified Startup Refactoring Proposal

**Дата:** 2026-06-24  
**Автор:** Supervisor agent (Hermes)  
**Мета:** Скоротити запуск будь-якого модуля QuantumEdge з 6+ кроків/скриптів до 1 команди, усунути конфлікти портів/ PYTHONPATH/робочих каталогів.

---

## 1. Аудит поточного стану

### 1.1 Компоненти та порти
| Компонент | Основна роль | Порт(и) | Примітки |
|---|---|---|---|
| MarketDataHub | ZMQ PUB ринкових даних | 5555 | Залежить від QuestDB (9000/9009/8812) |
| SupervisorAgent | Керування бotoми, LLM-модерація | 5558 (policy PUB), 5559 (command PUB), 8765 (HTTP) | Вимагає `PYTHONPATH` з `~/.hermes` |
| DynDCA Bot | DCA стратегія | 5567 (телеметрія) | `QE_BOT_TELEMETRY_PORT=5567` |
| AI Scalper Bot | Скальп/BingX | 5557 (телеметрія), 5559 (команди) | Конфліктує з Supervisor за 5559 через `bind()` |
| LockBotBTC | Лок-бот | 5577, 5578, 5561 | Власна ізольована логіка |
| QuestDB | TSDB | 9000, 9009, 8812 | Docker, healthcheck у скрипті зависає |

### 1.2 Скрипти та їхні проблеми
| Скрипт | Призначення | Проблеми |
|---|---|---|
| `scripts/cold_start_debug.sh` | Основной скрипт холодного старту | 6 фаз, залежить від `cwd`, зависає на QuestDB healthcheck, Supervisor не запускається з потрібним `PYTHONPATH`, бот біндить 5559 замість connect |
| `scripts/manage.sh` | start/stop/restart | Використовує інші порти (5556 замість 5558/5559), kill -9 по портах, не уніфікований з `cold_start_debug.sh` |
| `start.sh` | Docker + orchestrator | Запускає `QuantumEdge.py`, який нестабільний для термінала Hermes |
| `QuantumEdge.py` | Оркестратор | Верифікація статусу ненадійна, залежить від терміналу |
| `SupervisorAgent` (`~/.hermes/hermes/supervisor.py`) | Agent supervisor | Не запускається без `PYTHONPATH=/home/korben/.hermes:/home/korben/QuantumEdge-main/src`, не читає `config/logging.yaml` з іншого cwd |

### 1.3 Конфігураційні конфлікти
- **Порт 5559:** Supervisor публікує команди на 5559, а AI Scalper біндіть (`bind`) цей самий порт → `Address already in use`.
- **PYTHONPATH:** 
  - Модули QuantumEdge: `PYTHONPATH=/home/korben/QuantumEdge-main/src`
  - Supervisor Agent: `PYTHONPATH=/home/korben/.hermes:/home/korben/QuantumEdge-main/src`
- **Робочі каталоги:** Деякі скрипти чекають запуску з `/home/korben/QuantumEdge-main`, інші з `/src/...`, інші з `/home/korben/.hermes`.
- **healthcheck:** TCP healthcheck на ZMQ PUB сокеті (5557) завжди провалюється → Supervisor рестартує бота кожні ~60с.

---

## 2. Big Rocks (болеві точки)

1. **Немає єдиної точки входу.** Користувач мусить пам'ятати, який скрипт для чого, і вказувати `cwd`.
2. **Порти та конфлікти.** Supervisor та бот конфліктують за 5559; `manage.sh` і `cold_start_debug.sh` мають різні портові карти.
3. **PYTHONPATH-ОР.** Два різних venv/корені (`/home/korben/QuantumEdge-main/.venv` та `/home/korben/QuantumEdge-main/venv`), і додатковий `~/.hermes` для Supervisor.
4. **QuestDB healthcheck.** Фаза 2 у `cold_start_debug.sh` зависає назавжди, блокує весь старт.
5. **Зомбі-процеси.** Без "Phase 1" старт нестабільний; навіть з нею скрипт не завжди чистить стан.
6. **Розпорошені логи.** `quantum_edge.log`, `hub.log`, `bot.log`, `dyndca.log`, `supervisor.log`, `runtime/logs/supervisor.log` — 6 різних файлів.
7. **Ліміти ризику вимкнені.** `risk_management_enabled: false` в `risk.yaml`.

---

## 3. Пропозиція: Єдиний запускач `qe` + Makefile

### 3.1 Концепція
Один виконуваний файл `qe` (або `make <target>`) у корені проєкту, який:
- Приймає `up [COMPONENT]`, `down [COMPONENT]`, `restart [COMPONENT]`, `status`, `logs [COMPONENT]`.
- Автоматично вирішує `PYTHONPATH`, `cwd`, порти, venv.
- Використовує **єдиний `config/ports.yaml`** як source of truth.
- Замінює `cold_start_debug.sh`, `manage.sh`, `start.sh`, ручні `kill`/`nohup`.

### 3.2 Єдина конфігурація портів
Створити `config/ports.yaml`:

```yaml
ports:
  hub: 5555
  supervisor_telemetry: 5557
  supervisor_policy: 5558
  supervisor_command: 5559
  dyndca_telemetry: 5567
  lockbot_pub: 5577
  lockbot_cmd_sub: 5578
  lockbot_policy_sub: 5561
  dashboard: 8765

questdb:
  http: 9000
  ilp: 9009
  pg: 8812

components:
  hub:
    module: quantum_edge_core.market_data.hub
    ports: [hub]
    telemetry: false
  supervisor:
    module: quantum_edge_core.supervisor.supervisor
    entrypoint: /home/korben/.hermes/hermes/supervisor.py run-foreground
    ports: [supervisor_policy, supervisor_command, dashboard]
    extra_pythonpath: /home/korben/.hermes
    needs_docker: false
  dyndca:
    module: quantum_edge_core.dyn_dca_bot.main
    ports: [dyndca_telemetry]
    telemetry: true
    env:
      QE_BOT_TELEMETRY_PORT: "5567"
      MARKET_DATA_ZMQ_PORT: "5555"
  ai_scalper:
    module: quantum_edge_core.ai_scalper_bot.run_bot
    ports: [supervisor_telemetry]
    telemetry: true
    env:
      QE_BOT_TELEMETRY_PORT: "5557"
      QE_BOT_POLICY_PORT: "5559"
      MARKET_DATA_ZMQ_PORT: "5555"
    needs_supervisor: true
  lockbotbtc:
    module: quantum_edge_core.lock_bot.main
    args: ["--config", "config/lockbot.yaml"]
    ports: [lockbot_pub, lockbot_cmd_sub, lockbot_policy_sub]
```

### 3.3 Основна логіка `qe` (Python, ~300 рядків)

```python
#!/usr/bin/env python3
"""
QuantumEdge Unified Launcher (qe)
Usage:
  qe up [hub|supervisor|dyndca|ai_scalper|lockbotbtc|all]
  qe down [hub|supervisor|dyndca|ai_scalper|lockbotbtc|all]
  qe restart [component]
  qe status
  qe logs [component]
  qe ports
"""
```

**Ключові функції:**
1. **PortGuard** — один раз перевіряє/звільняє порти з `ports.yaml`, без дублювання логіки.
2. **VenvSelector** — автоматично вибирає правильний venv (`.venv` для QuantumEdge, віртуальне середовище для Supervisor за потреби).
3. **ProcessLauncher** — створює `subprocess.Popen` з правильними `cwd`, `env`, `PYTHONPATH`, перенаправляє логи.
4. **PidManager** — пише/читає `runtime/*.pid` у єдиному форматі.
5. **HealthProbe** — перевіряє готовність: ZMQ port bound, HTTP `/`, або кидає `Timeout`.
6. **LogAggregator** — `qe logs tail` зрозуміло показує останні 50 рядків потрібного файлу.

### 3.4 Команди користувача
```bash
# Повний старт (QuestDB + всі сервіси)
qe up all

# Лише Хаб + бот
qe up hub ai_scalper

# Перевірити стан
qe status

# Переглянути логи скальпера
qe logs ai_scalper

# Зупинити все
qe down all

# Перезапуск одного
qe restart ai_scalper
```

## 4. Конкретні правки, що вирішують конфлікти

### 4.1 Виправити AI Scalper SUB vs BIND на 5559
У `run_bot.py`:
```python
# Було:
cmd_endpoint = f"tcp://0.0.0.0:{self.config.policy_port}"
self.cmd_sub.bind(cmd_endpoint)

# Стало:
cmd_endpoint = f"tcp://127.0.0.1:{self.config.policy_port}"
self.cmd_sub.connect(cmd_endpoint)
```
Це дозволяє боту підписатися на PUB-сокет Supervisor, а не конфліктувати.

### 4.2 Виправити Supervisor healthcheck на боті
У `config/processes.yaml` для `ai_scalper` вже стоїть `type: "none"` — це правильно. Залишити як є.

### 4.3 Увімкнути risk_management
У `config/risk.yaml`:
```yaml
risk_management_enabled: true
```
Перед увімкненням переконатися, що `equity_start` у `runtime/supervisor/risk_state.json` валідний (не `null`), або видалити файл для чистого старту.

### 4.4 Видалити зайві PYTHONPATH з `processes.yaml`
У `processes.yaml` більше не потрібно вказувати `PYTHONPATH: "."` — `qe` сама формує коректне середовище.

### 4.5 Єдиний venv
Перенести/симетризувати залежності так, щоб один venv (`.venv` або `venv`) містив усе: `hermes-agent` + `quantum_edge_core`. Це дозволить запускати Supervisor без складних `PYTHONPATH`.

### 4.6 Відключити зайві скрипти старту
- `start.sh` → замінити на `qe up all`
- `scripts/manage.sh` → залишити тільки як fallback або видалити
- `scripts/cold_start_debug.sh` → видалити після стабілізації `qe`

---

## 5. План реалізації

| Етап | Дія | Результат |
|---|---|---|
| **1** | Створити `config/ports.yaml` як єдиний source of truth | Всі порти в одному місці |
| **2** | Додати `qe` — Python-скрипт запускача ~300 рядків | `qe up all` стартує все |
| **3** | Виправити `run_bot.py`: bind → connect на 5559 | Зникає конфлікт Supervisor–Scalper |
| **4** | Увімкнути `risk_management_enabled: true` та скинути `risk_state.json` | Активуються ліміти ризику |
| **5** | Уніфікувати venv (додати `hermes-agent` у `.venv` або налаштувати console script) | Supervisor запускається без додаткового PYTHONPATH |
| **6** | Замінити `cold_start_debug.sh` та `start.sh` на `qe` | Зникає дублювання логіки |
| **7** | Додати `qe status`, `qe logs [component]` | Швидка діагностика без `ps`/`ss` |

---

## 6. Переваги

- **1 команда** замість 6+ кроків.
- **Немає конфліктів портів** — якщо порт зайнято, `qe` вкаже хто зайняв і пропонує kill.
- **Немає приbijних PYTHONPATH/cwd** — все інкапсульовано в `qe`.
- **Немає frozen healthcheck-ів** — Хаб перевіряється по ZMQ-порту, не по HTTP.
- **Єдиний лог-файл** або зручний `qe logs tail`.
- **Ізоляція модулів зберігається** — кожен модуль запускається з своїми env/портами, але через єдиний інтерфейс.

---

## 7. Резюме

Поточний стан: запуск вимагає 6 фаз, 3 скрипти, ручне управління PYTHONPATH, і все ще має конфлікт Supervisor–Scalper за порт 5559.

Пропозиція: ввести **єдиний CLI `qe`** на основі `config/ports.yaml`, виправити конфлікт SUB/BIND, увімкнути ризик-менеджмент, консолідувати venv і замінити 3 скрипти на один інструмент.

Після рефакторингу: `qe up all` + `qe status` = повний контроль над системою.
