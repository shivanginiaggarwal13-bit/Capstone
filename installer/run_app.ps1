# Start the Supply Brain front end. Reads settings from .env; the port can also be passed: .\installer\run_app.ps1 8600
param([string]$Port = "")
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$vpy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $vpy)) { Write-Host "Not installed yet. Run .\installer\install.ps1 first."; exit 1 }
if (Test-Path ".env") {
  Get-Content ".env" | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$' -and -not $_.TrimStart().StartsWith("#")) {
      [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process")
    }
  }
}
if (-not $Port) { $Port = if ($env:SUPPLY_BRAIN_PORT) { $env:SUPPLY_BRAIN_PORT } else { "8501" } }
$url = "http://localhost:$Port"
Write-Host "Supply Brain front end: $url  (Ctrl+C to stop)"
# Headless skips Streamlit's first-run e-mail prompt; the browser is opened here instead.
if (-not $env:SUPPLY_BRAIN_NO_BROWSER) {
  Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoProfile", "-Command", "Start-Sleep 4; Start-Process '$url'"
}
& $vpy -m streamlit run App\streamlit_app.py --server.port $Port --server.headless true --browser.gatherUsageStats false
