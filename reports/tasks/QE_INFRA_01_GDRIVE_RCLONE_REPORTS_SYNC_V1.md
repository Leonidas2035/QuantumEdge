# QE_INFRA_01_GDRIVE_RCLONE_REPORTS_SYNC_V1

## 1. Мета та контекст
Підготовка інфраструктури двошарової звітності QuantumEdge на хості `ARTLINE-PC`:
- Коротке машинне зведення (`KEY=VALUE` + 3–5 речень) повертається у чат.
- Повні інженерні звіти зберігаються у `/home/layman/QuantumEdge/reports/tasks/<TASK_ID>.md` та синхронізуються у Google Drive папку `QuantumEdge-Reports`.
- Наявний у `reports/tasks/` звіт [`QE_HERMES_15MIN_LIVE_VST_TEST_V1.md`](file:///home/layman/QuantumEdge/reports/tasks/QE_HERMES_15MIN_LIVE_VST_TEST_V1.md) збережено недоторканим.

## 2. Діагностика середовища Ubuntu (ARTLINE-PC)
- `fusermount3` (`/usr/bin/fusermount3`), `fusermount` (`/usr/bin/fusermount`) та `gio` (`/usr/bin/gio`) встановлені (`FUSE3_INSTALLED=true`).
- `rclone` на хості не встановлено (`RCLONE_INSTALLED=false`), а `sudo -n` вимагає пароль оператора — безінтерактивне встановлення пропущено без зависання.
- Точка монтування GNOME GVFS (`/run/user/1000/gvfs/`) порожня (`GNOME_GVFS_GDRIVE_FOUND=false`).
- Локальна точка монтування `/home/layman/mnt/gdrive-reports` створена (`mkdir -p`).

## 3. Реалізовані зміни
1. [`tools/sync_report_to_gdrive.sh`](file:///home/layman/QuantumEdge/tools/sync_report_to_gdrive.sh):
   - Приймає шлях до файлу звіту та прапорці `--dry-run`, `--remote` (за замовчуванням `gdrive:QuantumEdge-Reports`), `--mount-dir` (за замовчуванням `/home/layman/mnt/gdrive-reports`).
   - Блокує передачу файлів із секретами (`.env`, `.env.*`, `*.key`, `*.pem`, `rclone.conf`, `*secrets*`) із кодом виходу `3`.
   - Якщо `/home/layman/mnt/gdrive-reports` підмонтований (`mountpoint -q`), копіює файл напряму в точку монтування; інакше перевіряє наявність `rclone` і remote `gdrive:` та викликає `rclone copy`; за відсутності конфігурації повертає `GDRIVE_NOT_CONFIGURED` (код `4`) із підказкою команд.
2. [`tests/test_sync_report_to_gdrive.py`](file:///home/layman/QuantumEdge/tests/test_sync_report_to_gdrive.py):
   - Офлайн unit-тести з підставним `RCLONE_BIN` у `tmp_path` для перевірки блокування секретів, відсутнього файлу, `--dry-run`, `rclone copy` та стану `GDRIVE_NOT_CONFIGURED`.

## 4. Результати офлайн-тестування
Прогін `.venv/bin/pytest tests/test_sync_report_to_gdrive.py -v`:
- `8 passed, 0 failed` (включаючи 5 параметризованих кейсів блокування секретів, перевірку відсутнього файлу, перевірку `--dry-run` / `rclone copy` через мок та перевірку неналаштованого remote).

## 5. Безпека та обмеження
- `git push` не виконувався; зовнішні репозиторії (`/home/layman/.hermes`, `/home/layman/Hermes-GPT`, `/home/layman/TerraTrader-V1`, `/home/layman/.gemini/antigravity-cli`, `hermes_agent/`) не зачеплені.
- Секрети `.env`, `config/firecrawl.key` та `rclone.conf` не читалися й не виводилися.
- Живі біржові запити не виконувалися.

## 6. Команди для оператора (завершення OAuth та синхронізація)
```bash
sudo apt-get update && sudo apt-get install -y rclone
rclone config   # New remote: name=gdrive, storage=drive, scope=drive (OAuth in browser)
rclone mkdir gdrive:QuantumEdge-Reports
rclone mount gdrive:QuantumEdge-Reports /home/layman/mnt/gdrive-reports --vfs-cache-mode writes --daemon
/home/layman/QuantumEdge/tools/sync_report_to_gdrive.sh /home/layman/QuantumEdge/reports/tasks/QE_HERMES_15MIN_LIVE_VST_TEST_V1.md
/home/layman/QuantumEdge/tools/sync_report_to_gdrive.sh /home/layman/QuantumEdge/reports/tasks/QE_INFRA_01_GDRIVE_RCLONE_REPORTS_SYNC_V1.md
```
