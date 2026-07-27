#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "💎 Aphrodite Agent Installer"
echo "   Platform: $(uname -s) ($(uname -m))"
echo ""

# Check Python version
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" &> /dev/null; then
    echo "❌ Python 3 not found. Please install Python 3.11+ first."
    exit 1
fi

PY_VER=$($PYTHON --version 2>&1 | awk '{print $2}')
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]); then
    echo "❌ Python $PY_VER found, but 3.11+ is required."
    exit 1
fi

echo "✅ Python $PY_VER"

# Create virtual environment
VENV_DIR="$PROJECT_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "🔨 Creating virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

echo "🔨 Upgrading pip..."
pip install --quiet --upgrade pip

echo "🔨 Installing Aphrodite Agent..."
pip install --quiet -e "$PROJECT_DIR"

echo "🔨 Installing dev dependencies..."
pip install --quiet -e "$PROJECT_DIR[dev]"

# Create config directory
CONFIG_DIR="${HOME}/.config/aphrodite-agent"
mkdir -p "$CONFIG_DIR"

if [ ! -f "$CONFIG_DIR/aphrodite.toml" ]; then
    echo "📝 Copying sample config..."
    cp "$PROJECT_DIR/aphrodite.toml" "$CONFIG_DIR/aphrodite.toml"
fi

# Create data directory
DATA_DIR="${HOME}/.local/share/aphrodite-agent"
mkdir -p "$DATA_DIR"

echo ""
echo "✅ Installation complete!"
echo ""
echo "Next steps:"
echo "   1. Edit config: $CONFIG_DIR/aphrodite.toml"
echo "   2. Set your API key: export OPENROUTER_API_KEY=sk-..."
echo "   3. Create a character: aphrodite create --character mira"
echo "   4. Start chatting: aphrodite chat"
echo "   5. Or launch web UI: aphrodite api"
echo ""
echo "Run 'aphrodite --help' for all commands."
