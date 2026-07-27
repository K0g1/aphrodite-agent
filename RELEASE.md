# Aphrodite Agent v0.1.0-alpha — One-Click Downloads

No Python, no pip, no virtualenv needed. Download the binary for your OS, make it executable (macOS/Linux), and run.

---

## Downloads

| OS | File | Size | Instructions |
|----|------|------|-------------|
| **Linux** | `aphrodite-linux-x64` | ~26 MB | [Download](#) `chmod +x` `./aphrodite-linux-x64 chat` |
| **macOS** | `aphrodite-macos` | ~28 MB | [Download](#) `chmod +x` `./aphrodite-macos chat` |
| **Windows** | `aphrodite-windows.exe` | ~27 MB | [Download](#) Double-click or run `aphrodite-windows.exe chat` |

---

## Quick Start (Any OS)

### Step 1: Download
Grab the binary for your OS from the release assets above.

### Step 2: First Run (no config needed)

**Linux / macOS:**
```bash
chmod +x aphrodite-*
./aphrodite-linux-x64 doctor     # Verify everything works
./aphrodite-linux-x64 chat       # Start chatting
```

**Windows (PowerShell / CMD):**
```powershell
.\aphrodite-windows.exe doctor
.\aphrodite-windows.exe chat
```

### Step 3: Configure your LLM (optional, uses free model by default)

The binary ships with a default config pointing to OpenRouter's free tier. If you want to use your own API key or a local Ollama instance:

**Linux / macOS:**
```bash
# Config auto-created at first run. Edit it:
nano ~/.config/aphrodite-agent/aphrodite.toml
```

**Windows:**
```powershell
notepad $env:USERPROFILE\.config\aphrodite-agent\aphrodite.toml
```

### Step 4: Create a character

```bash
./aphrodite-linux-x64 create --character mira
```

### Step 5: Launch the Web UI (optional)

```bash
./aphrodite-linux-x64 api
# Open http://127.0.0.1:8765 in your browser
```

---

## What Gets Created on First Run

| Path | Purpose |
|------|---------|
| `~/.config/aphrodite-agent/aphrodite.toml` | Main config file |
| `~/.local/share/aphrodite-agent/aphrodite.db` | SQLite database (messages, memories, world state) |
| `~/.local/share/aphrodite-agent/characters/` | Character markdown files |

---

## Troubleshooting

**"command not found"**
- Make sure you ran `chmod +x` on macOS/Linux
- On Windows, make sure Windows Defender hasn't blocked the executable

**"SQLite error"**
- The binary needs write access to `~/.local/share/aphrodite-agent/`
- Make sure your home directory is writable

**"Provider error"**
- Check your API key in the config
- For local Ollama, ensure it's running at `http://localhost:11434`

---

## Building from Source

If you prefer to build yourself:

```bash
git clone https://github.com/K0g1/aphrodite-agent.git
cd aphrodite-agent
pip install pyinstaller
pyinstaller --onefile scripts/aphrodite_launcher.py
```

---

*Built with PyInstaller. All dependencies bundled. No system Python required.*
