# Сборка one-file EXE (из каталога desktop/). Требуется: Python 3.11+, pip.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
py -3 -m pip install -e ".[build]"
py -3 -m PyInstaller `
    --onefile `
    --name seeding-desktop `
    --collect-all httpx `
    seeding_desktop/cli.py
