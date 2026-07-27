# Aphrodite Agent - Complete Architecture & Design Spec

## Overview
A personal continuity operating system with companion interfaces.
Named as homage to Hermes Agent. Cross-platform (Windows/Linux/Mac).
Rust daemon + Tauri desktop + Ratatui TUI + Clap CLI.

## Architecture: One Daemon, Many Clients

```
Desktop (Tauri/React) ─┐
TUI (Ratatui) ─────────┤── Local IPC ── aphrodited (Rust daemon) ── SQLite
CLI (Clap) ────────────┤                   │
Mobile (future) ───────┘              llama-server
                                    whisper.cpp
                                    TTS provider
```

## Character Card Format: .aphrocard

### Fields That Make Characters Feel Real
- Identity (name, pronouns, occupation, knowledge boundaries)
- Personal history (formative events, relationships, losses, secrets)
- Motivations (goals, fears, internal contradictions, boundaries)
- Speech model (sentence length, vocabulary, humor, verbal tics, emoji policy)
- Emotional model (baseline temperament, triggers, expression style, decay rate)
- Relationship behavior (trust progression, affection, conflict, repair style)
- Agency (personal projects, ability to disagree, initiate plans)
- Temporal behavior (sleep schedule, routines, timezone)

### Character ≠ Persona ≠ State ≠ Memory
- Character card = authored identity (immutable base)
- User persona = how user presents themselves
- Character state = current mood, adaptations (mutable overlay)
- Relationship state = evolving bond between user and character
- Memory = evidence about prior events

## Self-Evolution System

### Architecture: Immutable Base + Versioned Overlays
```
Base Character v1.2.0 (creator-locked invariants)
  → User-approved customization overlay
  → Learned adaptation overlay
  → Temporary session state
```

### Evolution Pipeline
```
Conversation evidence → Observation extractor → Candidate adaptation
→ Evidence aggregation → Conflict/safety checks → Shadow evaluation
→ Reject / Hold / Auto-apply low-risk / Request approval
→ Versioned overlay commit (with rollback)
```

### Auto-apply Policy
- Safe: message length preference, vocabulary, notification timing
- Require approval: personality changes, romantic boundaries, identity
- Never: manipulative tactics, prejudice, claims of human identity

## Voice System

### Voice State Machine
```
DISCONNECTED → PREPARING → READY → LISTENING → USER_SPEAKING
→ ENDPOINTING → FINALIZING_TRANSCRIPT → THINKING → SPEAKING
→ (barge-in) → BARGE_IN_CLASSIFY → TAKEOVER/BACKCHANNEL/NOISE
```

### Latency Targets
- P50 first audible response: <1.0s
- P95 first audible response: <2.0s
- Barge-in suppression P95: <180ms

### Turn Detection: Multi-Signal
1. Acoustic VAD (probability thresholds)
2. Partial transcription (streaming STT)
3. Syntactic completion (sentence structure)
4. Semantic completion (topic closure)
5. Speaker-specific pause profile
6. Manual override

### Barge-in Classification
- Takeover ("Wait, that's not what I meant") → stop, commit new turn
- Backchannel ("Mhm", "Yeah") → duck, continue after
- Noise → restore playback, no user message

### Voice Onboarding (7 screens)
1. Privacy choice (local vs cloud)
2. Device selection + level check
3. Environmental calibration (noise floor, echo detection)
4. Turn-taking calibration (pause profile)
5. Language setup + code-switching
6. Character voice audition + style controls
7. Background permissions

### Voice Cloning Flow (11 steps)
1. Choose source (design/clone/provider)
2. Rights and consent verification
3. Local or cloud processing choice
4. Recording environment check
5. Capture samples (neutral/warm/energetic/names/languages)
6. Quality gate (noise/clipping/reverb)
7. Live consent verification (voice captcha)
8. Provider submission with disclosure
9. Audition (5 test clips)
10. Bind to character
11. Revocation and deletion

## TUI Design

### Three-Pane Layout (120+ cols)
```
┌ Characters │ Conversation │ State (mood/threads/memory) ┐
├────────────┴──────────────┴─────────────────────────────┤
│ Message composer...                                      │
├──────────────────────────────────────────────────────────┤
│ Contextual footer with keyboard shortcuts                 │
└──────────────────────────────────────────────────────────┘
```

### Responsive Breakpoints
- 140+ cols: 3 panes (Characters | Conversation | Inspector)
- 100-139: 2 panes (Conversation | Inspector)
- 70-99: Single pane (Conversation)
- <70: Compact mode or line-oriented accessible mode

### Inspector Tabs
- State (mood, relationship, open threads, commitments)
- Memory (search, filter, correct, forget)
- Model (provider, GPU, tokens/sec, VRAM, cache)
- Jobs (scheduler, active reminders, delivery log)

### Voice in TUI
- Header shows: listening/speaking/thinking states
- Level meters, language indicator
- Expanded voice status popup (F4)

### Accessibility
- `--plain` mode: line-oriented, no alternate screen
- `--accessible` mode: sequential output, screen-reader friendly
- Monochrome support, ASCII box drawing fallback
- Every action keyboard-accessible via command palette

### Key Features
- Command palette (Ctrl+K) with fuzzy search
- Character switcher (Ctrl+O)
- Memory browser with filters
- Raw config editor ($EDITOR integration)
- Notification inbox with provenance

## Cross-Platform Strategy

### Behavioral Parity Contract
Given same character, state, conversation, model, and seed:
- Prompt compilation is equivalent
- Memory retrieval is equivalent
- Scheduling semantics are equivalent
- Data formats are equivalent

### Engine Packs (platform-specific)
- Windows x86-64: CPU + Vulkan (optional CUDA)
- Linux x86-64: CPU + Vulkan (optional CUDA/ROCm)
- macOS Apple Silicon: Metal
- macOS Intel: Metal or CPU

### Runtime File Layout
- Windows: %LOCALAPPDATA%\Aphrodite\
- macOS: ~/Library/Application Support/Aphrodite/
- Linux: ~/.local/share/aphrodite/

## Plugin System (3 tiers)

### Tier 1: WASM (default community)
- No filesystem, no network, bounded memory
- Explicit host functions, capability grants

### Tier 2: External process (Python/Node/native)
- Separate process, authenticated protocol
- Resource limits, explicit filesystem roots

### Tier 3: Built-in trusted
- Calendar, notifications, model providers, audio devices

## Build Order

1. **Contracts** — schemas, protocols, threat model
2. **Vertical text slice** — daemon + CLI + one character + llama.cpp
3. **Character + memory** — card system, FTS5, retrieval, correction
4. **Desktop product** — Tauri, onboarding, model downloader
5. **Proactive continuity** — commitments, scheduler, quiet hours
6. **Voice** — STT, TTS, barge-in, transcript correction
7. **Self-evolution** — observation, proposals, approval, rollback
8. **Plugins** — WASM host, capability broker, SDK
9. **Mobile + sync** — encrypted sync, notifications

## Key Design Principles

1. Python owns every durable state transition → Rust owns it instead
2. SQLite is the single source of truth
3. Model proposes commands; domain code authorizes and executes
4. Every external side effect has an idempotency key
5. Every event has correlation and causation IDs
6. Memories retain provenance, confidence, correction history
7. Model never writes SQL directly
8. No shell tools in initial product
9. Agent loop bounded by tool count, time, and tokens
10. Turn-taking quality before voice realism
