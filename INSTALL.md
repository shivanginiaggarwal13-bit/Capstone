# Installing Supply Brain

One command downloads the project from GitHub and sets up everything needed to run it: the Streamlit front end, the
MEIO models, the Digital Twin, segmentation and the scenario agents. Data and model outputs come with the download.

## What you need

- **Python 3.11 or newer** ([python.org/downloads](https://www.python.org/downloads/)). Tested on Python 3.14.
  On Windows, tick "Add python.exe to PATH" during setup.
- About 3 GB of free disk space for the project (about 500 MB) and its Python packages.
- Git is optional: without it, the installer downloads a zip of the project instead.

## Option A: one command (downloads and installs)

**macOS / Linux**, in Terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/shivanginiaggarwal13-bit/Capstone/installer/installer/bootstrap.sh | bash
```

**Windows**, in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/shivanginiaggarwal13-bit/Capstone/installer/installer/bootstrap.ps1 | iex"
```

This downloads the project from GitHub (`shivanginiaggarwal13-bit/Capstone`, branch `installer`) into a `SupplyBrain`
folder in your home directory, then runs the installer below. Running it again updates that copy and re-checks the
install. Settings:

| Setting | Effect | Example (macOS / Linux) |
|---|---|---|
| `SB_DIR` | Install somewhere else | `curl -fsSL ... \| SB_DIR=~/Projects/SupplyBrain bash` |
| `SB_RUN=1` | Start the app when the install finishes | `curl -fsSL ... \| SB_RUN=1 bash` |
| `SB_BRANCH` | Download a different branch | `curl -fsSL ... \| SB_BRANCH=main bash` |
| installer options | Passed on to `install.sh` | `curl -fsSL ... \| bash -s -- --locked` |

On Windows, set these first, for example `$env:SB_DIR = "D:\SupplyBrain"`.

## Option B: you already have the code

If you cloned the repository or downloaded and unzipped it, run the installer from the project folder:

```bash
git clone https://github.com/shivanginiaggarwal13-bit/Capstone.git
cd Capstone
git checkout installer
./installer/install.sh            # Windows: double-click installer\install.bat
```

## What the installer does

1. finds Python 3.11 or newer;
2. creates a private environment in `.venv` (nothing is installed system-wide);
3. installs the packages in `requirements.txt`;
4. creates `.env` from `.env.example` for optional API keys;
5. checks packages, data files and folders (`installer/check_install.py`);
6. runs the agent test suite.

Options:

| macOS / Linux | Windows | Effect |
|---|---|---|
| `--locked` | `-Locked` | Install the exact package versions the project was tested with (`installer/requirements-lock.txt`) |
| `--with-shap` | `-WithShap` | Also install XGBoost and SHAP, needed only for the SHAP explainability script |
| `--skip-tests` | `-SkipTests` | Skip the test suite |
| `PYTHON=/path/to/python` | `-Python C:\path\python.exe` | Use a specific Python |

Running the installer again is safe: it reuses `.venv` and leaves an existing `.env` untouched.

## Run the front end

**macOS / Linux**, from the project folder (`~/SupplyBrain` if you used Option A):

```bash
./installer/run_app.sh          # http://localhost:8501
./installer/run_app.sh 8600     # another port
```

**Windows**: double-click `installer\run_app.bat`, or `.\installer\run_app.ps1`.

## API keys (optional)

The front end and the **Desktop bridge** agent profile work without any key. To use the other agent profiles, add
keys to `.env`:

```
ANTHROPIC_API_KEY=...   # Claude profile
NVIDIA_API_KEY=...      # NVIDIA profile
SUPPLY_BRAIN_PORT=8501  # default port for run_app
```

`.env` is ignored by git, so keys stay on your machine.

## Troubleshooting

| Problem | Fix |
|---|---|
| "Python 3.11 or newer was not found" | Install Python from python.org, then run the installer again (on Windows, tick "Add python.exe to PATH") |
| Windows blocks the script | Use the `.bat` files, which run PowerShell with a one-off execution-policy bypass |
| A package fails to install on an older Python | Upgrade to Python 3.12 or newer, or try `--locked` on Python 3.14 |
| Port already in use | Start on another port: `./installer/run_app.sh 8600` |
| Check the setup at any time | `.venv/bin/python installer/check_install.py` (Windows: `.venv\Scripts\python.exe installer\check_install.py`) |
