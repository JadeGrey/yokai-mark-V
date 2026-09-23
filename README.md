# Yokai

Yokai is a private, single-server Discord bot (~10 users) hosted locally on Windows. Its persona is a fan nod to Echo's Yokai drone from *Rainbow Six Siege*—confident, sharp, and focused on high-fidelity music streaming and queue management.

> *Unaffiliated fan project inspired by Echo's Yokai drone from Rainbow Six Siege. © Ubisoft.*

## Prerequisites
- **Python 3.12+** (tested with Python 3.14 on Windows)
- **FFmpeg** on `PATH`
- **Deno** on `PATH` (used by yt-dlp for JavaScript extraction)

## Quick Start (PowerShell)

1. **Set up the virtual environment:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -e ".[dev]"
   ```

2. **Configure environment:**
   Copy `.env.example` to `.env` and fill in your Discord credentials:
   ```powershell
   Copy-Item .env.example .env
   ```

3. **Run the bot:**
   ```powershell
   python -m yokai
   ```

4. **Run tests and linting:**
   ```powershell
   python -m pytest -q
   python -m ruff check .
   python -m ruff format --check .
   ```

*Note: Yokai runs on your local machine and will appear offline whenever your PC is asleep or powered off.*
