# Supply Brain one-line installer for Windows: downloads the project, then installs it.
#
#   powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/shivanginiaggarwal13-bit/Capstone/installer/installer/bootstrap.ps1 | iex"
#
# Optional settings (set before running, e.g. $env:SB_DIR = "D:\SupplyBrain"):
#   SB_DIR      where to put the project   (default: %USERPROFILE%\SupplyBrain)
#   SB_BRANCH   which branch to download    (default: installer)
#   SB_RUN=1    start the front end when done
$ErrorActionPreference = "Stop"
$Repo = "https://github.com/shivanginiaggarwal13-bit/Capstone"
$Branch = if ($env:SB_BRANCH) { $env:SB_BRANCH } else { "installer" }
$Dir = if ($env:SB_DIR) { $env:SB_DIR } else { Join-Path $env:USERPROFILE "SupplyBrain" }
function Step($t) { Write-Host "`n==> $t" -ForegroundColor Cyan }

if (Test-Path (Join-Path $Dir ".git")) {
  Step "Updating the existing copy in $Dir"
  git -C $Dir fetch --depth 1 origin $Branch; git -C $Dir checkout -q -B $Branch FETCH_HEAD
} elseif ((Test-Path $Dir) -and (Get-ChildItem $Dir -Force | Select-Object -First 1)) {
  Write-Host "$Dir already exists and is not a Supply Brain download. Choose another folder with `$env:SB_DIR."; return
} elseif (Get-Command git -ErrorAction SilentlyContinue) {
  Step "Downloading Supply Brain into $Dir (about 500 MB)"
  git clone --depth 1 --branch $Branch "$Repo.git" $Dir
  if ($LASTEXITCODE -ne 0) { throw "git clone failed" }
} else {
  Step "Git not found; downloading a zip of Supply Brain into $Dir (about 500 MB)"
  $tmp = Join-Path $env:TEMP ("sb_" + [guid]::NewGuid())
  New-Item -ItemType Directory -Path $tmp | Out-Null
  $ProgressPreference = "SilentlyContinue"
  Invoke-WebRequest "https://codeload.github.com/shivanginiaggarwal13-bit/Capstone/zip/refs/heads/$Branch" -OutFile "$tmp\sb.zip"
  Expand-Archive "$tmp\sb.zip" -DestinationPath $tmp
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Dir) | Out-Null
  Move-Item (Get-ChildItem $tmp -Directory -Filter "Capstone-*").FullName $Dir
  Remove-Item -Recurse -Force $tmp
}

& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dir "installer\install.ps1")
if ($env:SB_RUN -eq "1") { & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dir "installer\run_app.ps1") }
else { Write-Host "Supply Brain is in $Dir`nStart it any time with: $Dir\installer\run_app.bat" }
