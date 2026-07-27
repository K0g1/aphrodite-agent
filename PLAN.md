# Aphrodite Agent - Master Plan

## What It Is
A personal continuity operating system with companion interfaces.
Named as homage to Hermes Agent.

## Architecture
- **Rust daemon** (`aphrodited`) as single source of truth
- **Tauri 2** desktop shell (React + TypeScript)
- **Ratatui TUI** (Rust)
- **Clap CLI** (Rust)
- **llama-server** as managed sidecar
- **whisper.cpp** as managed sidecar
- **SQLite/SQLCipher** for all storage
- **WASM plugins** with capability sandboxing

## Cross-Platform
- Windows, Linux, Mac with behavioral parity
- Same daemon, different client surfaces
- Platform-specific engine packs (CUDA/Metal/Vulkan/CPU)

## Key Innovation
Immutable character definitions + mutable versioned state overlays.
The model never rewrites its own base character card.

## Character Card Format: `.aphrocard`
- manifest.json, character.json, lore/, assets/, voice/, greetings/, examples/, evals/
- SillyTavern V2/V3 import/export
- Community card ecosystem

## Build Order
1. Contracts (schemas, protocols, threat model)
2. Vertical text slice (daemon + CLI + one character + llama.cpp)
3. Character + memory foundation
4. Desktop product (Tauri)
5. Proactive continuity
6. Voice (STT + TTS + barge-in)
7. Bounded self-evolution
8. Plugins (WASM + external process)
9. Mobile + sync

## Full project structure, schema, and architecture
## See conversation logs for complete implementation details
