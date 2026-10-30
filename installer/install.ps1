# Supply Brain installer for Windows (PowerShell).
#
#   .\installer\install.ps1               install with the latest compatible packages
#   .\installer\install.ps1 -Locked       install the exact versions the project was tested with
#   .\installer\install.ps1 -WithShap     also install the optional SHAP / XGBoost packages
#   .\installer\install.ps1 -SkipTests    skip the agent test suite at the end
#   .\installer\install.ps1 -Python C:\Path\to\python.exe
param([switch]$Locked, [switch]$WithShap, [switch]$SkipTests, [string]$Python = "")
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($t) { Write-Host "`n==> $t" -ForegroundColor Cyan }
function Run($exe, [string[]]$argv) { & $exe @argv; if ($LASTEXITCODE -ne 0) { throw "Command failed: $exe $argv" } }

Step "Finding Python 3.11 or newer"
$py = $null; $pyArgs = @()
$check = "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($Python) { $py = $Python }
else {
  foreach ($v in "3.14", "3.13", "3.12", "3.11") {
    if (Get-Command py -ErrorAction SilentlyContinue) {
      & py "-$v" -c $check 2>$null
      if ($LASTEXITCODE -eq 0) { $py = "py"; $pyArgs = @("-$v"); break }
    }
  }
  if (-not $py) {
    foreach ($c in "python", "python3") {
      if (Get-Command $c -ErrorAction SilentlyContinue) {
        & $c -c $check 2>$null
        if ($LASTEXITCODE -eq 0) { $py = $c; break }
      }
    }
  }
}
if (-not $py) { Write-Host "Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/ and run this again."; exit 1 }
& $py @pyArgs --version

Step "Creating the virtual environment (.venv)"
$vpy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $vpy)) { Run $py ($pyArgs + @("-m", "venv", ".venv")) } else { Write-Host ".venv already exists, reusing it" }

Step "Installing packages"
Run $vpy @("-m", "pip", "install", "--upgrade", "pip", "--quiet")
if ($Locked) { Run $vpy @("-m", "pip", "install", "-r", "installer\requirements-lock.txt") }
else { Run $vpy @("-m", "pip", "install", "-r", "requirements.txt") }
if ($WithShap) { Run $vpy @("-m", "pip", "install", "-r", "installer\requirements-optional.txt") }

Step "Preparing settings"
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "Created .env from .env.example (add API keys there if you want the Claude or NVIDIA agent profiles)" }
else { Write-Host ".env already exists, left unchanged" }
New-Item -ItemType Directory -Force -Path "App\runs" | Out-Null

Step "Checking the installation"
Run $vpy @("installer\check_install.py")

if (-not $SkipTests) {
  Step "Running the agent test suite"
  Run $vpy @("-m", "pytest", "-q", "Agents\tests")
}

Write-Host "`nSupply Brain is installed.`nStart the front end with:`n`n    .\installer\run_app.ps1   (or double-click installer\run_app.bat)`n"
