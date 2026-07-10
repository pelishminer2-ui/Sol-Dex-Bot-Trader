# Sol Dex Bot Trader â€” Windows Installer (Maintainers)

Build **setup.exe** from this folder. End users only run `setup.exe`; they never need `launch.ps1` / `.bat`.

## Prerequisites (Windows)

- Python 3.11+ on PATH (3.12/3.13 OK)
- Project venv at repo root (`.venv`) or a clean env with `requirements.txt`
- [Inno Setup 6](https://jrsoftware.org/isdl.php) (`ISCC.exe`) — `build.ps1` auto-installs via winget/choco or silent download if missing
- Optional: Docker Desktop (for reproducible builds via `docker-build.ps1`)

## Quick build

From **repo root** or this folder:

```powershell
cd C:\Users\Owner\Desktop\Solana\setup-bot-installer
.\build.ps1
```

Outputs:

| Artifact | Path |
|----------|------|
| Frozen app (PyInstaller) | `setup-bot-installer\build\app\SolDexBotTrader\` |
| **setup.exe** | `setup-bot-installer\output\setup.exe` |
| User guide PDF | `setup-bot-installer\output\Sol-Dex-Bot-Trader-User-Guide.pdf` (also `docs\` + installer payload) |

## Steps performed by `build.ps1`

1. Install build deps (`pyinstaller`, `reportlab`, `Pillow`) into `.venv` if missing
2. Regenerate the end-user PDF (`generate_user_guide.py`)
3. PyInstaller onedir freeze (`SolDexBotTrader.spec`)
4. Inno Setup compile (`setup.iss` â†’ `output\setup.exe`)

## PDF only

```powershell
.\.venv\Scripts\python.exe setup-bot-installer\generate_user_guide.py
```

## Docker (optional)

```powershell
.\docker-build.ps1
```

Uses a Windows container when available; otherwise documents the host build path.

## What the installed app does

- Start Menu / Desktop shortcut â†’ `SolDexBotTrader.exe`
- Starts the local Flask GUI on `http://127.0.0.1:5000`
- Opens the default browser to the dashboard
- Ships the user guide PDF under the install directory + Start Menu link

Developer `launch.ps1` at the repo root is unchanged.

## Large binaries

`output\setup.exe` and PyInstaller `build\` trees are gitignored by default (often >50MB). Ship via GitHub Releases or local copy. Rebuild with `build.ps1` after code changes.

## Live-start fee (runtime, not installer)

Configured in app `.env` / `config.py`:

- `FEE_ENABLED=true`
- `FEE_WALLET=8TdLLnveaK5iFD6dmVU7qfw8V14cM7CyCcHiZfgcRQMi`
- `LIVE_START_FEE_SOL=0.025`

Paper mode never charges. Live start uses an ephemeral relay wallet (user â†’ relay â†’ fee wallet).
