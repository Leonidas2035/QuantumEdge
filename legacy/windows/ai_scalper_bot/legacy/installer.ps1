# ============================================================
# installer.ps1 — OFFLINE Python Installer + venv + dependencies
# ============================================================

param(
    [string]$InstallDir = "C:\ai_scalper_bot"
)

Write-Host "`n=== AI SCALPER BOT OFFLINE INSTALLER ===" -ForegroundColor Cyan

# ------------------------------------------------------------
# 0. Перехід у директорію проєкту
# ------------------------------------------------------------
Set-Location $InstallDir

# ------------------------------------------------------------
# 1. Локальний файл Python EXE
# ------------------------------------------------------------
$LocalPythonInstaller = Join-Path $InstallDir "python-3.12.10-amd64.exe"

if (-Not (Test-Path $LocalPythonInstaller)) {
    Write-Host "❌ Не знайдено python-3.12.10-amd64.exe у $InstallDir" -ForegroundColor Red
    exit 1
}

Write-Host "✔ Знайдено локальний Python installer: $LocalPythonInstaller" -ForegroundColor Green


# ------------------------------------------------------------
# 2. Шукаємо Python 3.12 у системі
# ------------------------------------------------------------
$Python312Exe = $null

$searchPaths = @(
    "C:\Program Files\Python312\python.exe",
    "C:\Program Files\Python312\python3.exe",
    "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python312\python3.exe"
)

foreach ($p in $searchPaths) {
    if (Test-Path $p) {
        $ver = & $p -V
        if ($ver -match "3\.12") {
            $Python312Exe = $p
            break
        }
    }
}

# ------------------------------------------------------------
# 3. Якщо Python 3.12 не знайдено → встановлюємо локально
# ------------------------------------------------------------
if (-not $Python312Exe) {

    Write-Host "⬇ Встановлюю Python 3.12.10 OFFLINE..." -ForegroundColor Yellow

    Start-Process `
        -FilePath $LocalPythonInstaller `
        -ArgumentList "/quiet InstallAllUsers=1 PrependPath=0 Include_pip=1" `
        -Wait

    Start-Sleep -Seconds 4

    foreach ($p in $searchPaths) {
        if (Test-Path $p) {
            $Python312Exe = $p
            break
        }
    }
}

if (-not $Python312Exe) {
    Write-Host "❌ ФАТАЛЬНО: Python 3.12 не встановився." -ForegroundColor Red
    exit 1
}

Write-Host "✔ Python 3.12 знайдено: $Python312Exe" -ForegroundColor Green


# ------------------------------------------------------------
# 4. Створення venv
# ------------------------------------------------------------
$VenvDir = Join-Path $InstallDir "venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "⚙ Створюю venv..." -ForegroundColor Yellow
    & $Python312Exe -m venv $VenvDir
} else {
    Write-Host "✔ venv вже існує" -ForegroundColor Green
}


# ------------------------------------------------------------
# 5. Оновлення pip
# ------------------------------------------------------------
Write-Host "⚙ Оновлюю pip..." -ForegroundColor Yellow
& $VenvPython -m pip install --upgrade pip setuptools wheel


# ------------------------------------------------------------
# 6. Встановлення залежностей
# ------------------------------------------------------------
Write-Host "⚙ Встановлюю залежності..." -ForegroundColor Cyan

$packages = @(
    "pyyaml>=6.0",
    "websockets>=11.0",
    "python-dotenv>=1.0",
    "aiohttp>=3.8",
    "numpy>=1.25",
    "pandas>=2.2",
    "python-binance>=1.0",
    "psutil",
    "pydantic",
    "requests",
    "xgboost"
)

foreach ($pkg in $packages) {
    Write-Host "→ $pkg"
    & $VenvPython -m pip install $pkg
}

Write-Host "⬇ Встановлюю Torch CPU..." -ForegroundColor Magenta
& $VenvPython -m pip install torch --index-url https://download.pytorch.org/whl/cpu


# ------------------------------------------------------------
# 7. Створюємо run_bot.cmd
# ------------------------------------------------------------
$RunBot = @"
@echo off
cd /d $InstallDir
"$VenvDir\Scripts\python.exe" -m bot.market_data.ws_manager
"@

Set-Content -Path "$InstallDir\run_bot.cmd" -Value $RunBot -Encoding ASCII

Write-Host "✔ Створено run_bot.cmd" -ForegroundColor Green


# ------------------------------------------------------------
# 8. Створення ярлика run_bot.exe або .lnk
# ------------------------------------------------------------
$Shortcut = "$([Environment]::GetFolderPath('Desktop'))\AI Scalper Bot.lnk"

$WshShell = New-Object -ComObject WScript.Shell
$SC = $WshShell.CreateShortcut($Shortcut)
$SC.TargetPath = "$InstallDir\run_bot.cmd"
$SC.WorkingDirectory = $InstallDir
$SC.IconLocation = "$InstallDir\bot_icon.ico"
$SC.Save()

Write-Host "✔ Ярлик створено на робочому столі" -ForegroundColor Green


Write-Host "`n=== ІНСТАЛЯЦІЯ УСПІШНО ЗАВЕРШЕНА ===" -ForegroundColor Green
