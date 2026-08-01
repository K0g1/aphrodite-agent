"""CLI entry point for Aphrodite Agent."""

from __future__ import annotations

import asyncio
from datetime import UTC
from pathlib import Path

import click

from aphrodite.api.server import run_api_server
from aphrodite.app import AphroditeApp
from aphrodite.character import validate_character_id
from aphrodite.config import Config, load_config
from aphrodite.db import Database
from aphrodite.export import ExportManager
from aphrodite.providers import ProviderError
from aphrodite.simulation import SimulationEngine


@click.group()
@click.option("--config", "-c", type=click.Path(), help="Config file path")
@click.option("--character", "-C", type=str, help="Character name")
@click.option("--debug", is_flag=True, help="Enable debug mode")
@click.pass_context
def cli(ctx, config, character, debug):
    """Aphrodite Agent — Personal AI companion."""
    from aphrodite import enable_utf8_stdio

    enable_utf8_stdio()
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config
    ctx.obj["character"] = character
    ctx.obj["debug"] = debug

    from aphrodite.logging import setup_logging

    setup_logging(debug=debug)


@cli.command()
@click.argument("message", required=False)
@click.option("--character", "-C", help="Character name")
@click.pass_context
def chat(ctx, message, character):
    """Start an interactive chat session."""
    config = _load_ctx_config(ctx)
    char = character or ctx.obj.get("character")

    asyncio.run(_run_chat(config, char, message))


async def _run_chat(config: Config, character_name: str | None, initial_message: str | None):
    """Run the chat loop."""
    app = AphroditeApp(config)
    await app.initialize(character_name)

    char_name = app.character.name if app.character else "default"
    print(f"\n💬 Chatting with {char_name}")
    print(f"   Provider: {config.provider_active} ({config.active_provider.model})")
    print("   Type 'quit' or 'exit' to stop\n")

    if initial_message:
        try:
            response = await app.chat(initial_message)
        except ProviderError as exc:
            print(f"⚠ Provider unavailable: {exc}\n")
        else:
            print(f"{char_name}: {response}\n")

    try:
        while True:
            try:
                user_input = input("You: ").strip()
            except EOFError:
                break

            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                print(f"\n{char_name}: See you later! 👋")
                break

            try:
                response = await app.chat(user_input)
            except ProviderError as exc:
                print(f"⚠ Provider unavailable: {exc}\n")
                continue
            print(f"{char_name}: {response}\n")
    except KeyboardInterrupt:
        print(f"\n{char_name}: See you later! 👋")
    finally:
        await app.close()


@cli.command()
@click.option("--character", "-C", help="Character name to create")
@click.pass_context
def create(ctx, character):
    """Create a new character interactively."""
    config = _load_ctx_config(ctx)
    name = character or click.prompt("Character name")
    try:
        validate_character_id(name)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="character") from exc

    char_dir = config.characters_dir / name
    if char_dir.exists():
        click.echo(f"Character '{name}' already exists at {char_dir}")
        return

    # Create basic character files
    char_dir.mkdir(parents=True, exist_ok=True)

    age = click.prompt("Age", type=int, default=24)
    pronouns = click.prompt("Pronouns", default="she/her")

    identity_content = f"""---
name: "{name}"
pronouns: "{pronouns}"
age: {age}
---

# Identity

{name} is a thoughtful companion.

# Values
- honesty
- kindness

# Likes
- good conversations

# Dislikes
- rudeness

# Boundaries
- maintains personal space
"""
    (char_dir / "identity.md").write_text(identity_content)

    personality_content = """---
sliders:
  warmth: 0.7
  directness: 0.5
  playfulness: 0.4
  expressiveness: 0.6
  initiative: 0.4
  verbosity: 0.4
  formality: 0.3
  flirtation: 0.0
---

# Personality

Warm and balanced personality.
"""
    (char_dir / "personality.md").write_text(personality_content)

    speech_content = """# Speech Style

## Register
casual

## Vocabulary
natural conversational

## Mannerisms
- None specified

## Avoid Saying
- None specified
"""
    (char_dir / "speech.md").write_text(speech_content)

    click.echo(f"\n✅ Character '{name}' created at {char_dir}")
    click.echo("   Edit the .md files to customize personality, speech, and more.")


@cli.command()
@click.pass_context
def characters(ctx):
    """List all characters."""
    config = _load_ctx_config(ctx)
    chars_dir = config.characters_dir

    if not chars_dir.exists():
        click.echo("No characters found.")
        return

    click.echo("Characters:\n")
    for d in sorted(chars_dir.iterdir()):
        if d.is_dir():
            char_files = list(d.glob("*.md"))
            click.echo(f"  📁 {d.name} ({len(char_files)} files)")


@cli.command()
@click.argument("name", required=False)
@click.pass_context
def doctor(ctx, name=None):
    """Check system health."""
    config = _load_ctx_config(ctx)
    click.echo("🏥 Aphrodite Agent Health Check\n")

    problems = 0

    # Config
    click.echo("✓ Config loaded")

    # Database
    db_exists = config.db_path.exists()
    if not db_exists:
        problems += 1
    click.echo(
        f"{'✓' if db_exists else '✗'} Database {'found' if db_exists else 'not found'} ({config.db_path})"
    )

    # Characters
    chars_dir = config.characters_dir
    char_count = len(list(chars_dir.glob("*/*.md"))) if chars_dir.exists() else 0
    if char_count == 0:
        problems += 1
    click.echo(f"{'✓' if char_count > 0 else '⚠'} Character files: {char_count}")

    # Provider
    provider = config.active_provider
    click.echo(f"✓ Provider: {config.provider_active} ({provider.base_url})")
    click.echo(f"  Model: {provider.model}")

    # Directories
    click.echo(f"✓ Data dir: {config.data_path}")
    click.echo(f"✓ Config dir: {config.config_path}")

    click.echo("\n✨ Version: 0.1.0")
    if problems:
        click.echo(f"\n⚠ {problems} problem(s) found")
        raise click.exceptions.Exit(1)


@cli.command()
@click.option("--hours", "-H", type=float, default=1, help="Hours to advance")
@click.option("--character", "-C", help="Character name")
@click.pass_context
def advance(ctx, hours, character):
    """Advance world time (simulation)."""
    config = _load_ctx_config(ctx)
    char = character or (ctx.obj.get("character") if ctx.obj else None)

    async def _advance():
        from datetime import datetime

        app = AphroditeApp(config)
        await app.initialize(char)
        now = datetime.now(UTC)
        events = await app.world.advance_time(hours, now)
        state = await app.world.get_state()
        await app.close()

        click.echo(f"⏰ Advanced {hours} hours")
        click.echo(f"   Activity: {state.activity}")
        click.echo(f"   Mood: {state.mood.label()}")
        click.echo(f"   Weather: {state.weather.condition}, {state.weather.temperature_c}°C")
        if events:
            click.echo(f"   Events: {len(events)}")
            for e in events:
                click.echo(f"     - {e['summary']}")

    asyncio.run(_advance())


@cli.command()
@click.argument("name")
@click.option("--output", "-o", help="Output path")
@click.option("--include-memories", is_flag=True, default=False)
@click.pass_context
def export(ctx, name, output, include_memories):
    """Export a character to .aphrocard file."""
    config = _load_ctx_config(ctx)

    async def _export():
        export_mgr = ExportManager(config)
        path = await export_mgr.export_character(name, output, include_memories)
        click.echo(f"✅ Character '{name}' exported to {path}")

    asyncio.run(_export())


@cli.command()
@click.argument("file", type=click.Path(exists=True))
@click.pass_context
def import_char(ctx, file):
    """Import a character from .aphrocard file."""
    config = _load_ctx_config(ctx)

    async def _import():
        export_mgr = ExportManager(config)
        try:
            name = await export_mgr.import_character(file)
        except FileExistsError as exc:
            raise click.ClickException(f"Character already exists: {exc}") from exc
        except ValueError as exc:
            raise click.ClickException(f"Invalid archive: {exc}") from exc
        click.echo(f"✅ Character '{name}' imported")

    asyncio.run(_import())


@cli.command()
@click.option("--output", "-o", help="Output path")
@click.pass_context
def export_memories(ctx, output):
    """Export all memories to JSON."""
    config = _load_ctx_config(ctx)

    async def _export():
        db = Database(config.db_path)
        await db.initialize()
        export_mgr = ExportManager(config, db)
        path = await export_mgr.export_memories(output)
        click.echo(f"✅ Memories exported to {path}")
        await db.close()

    asyncio.run(_export())


@cli.command()
@click.pass_context
def stats(ctx):
    """Show system statistics."""
    config = _load_ctx_config(ctx)

    async def _stats():
        db = Database(config.db_path)
        await db.initialize()

        # Get counts
        msg_count = await db.fetch_one("SELECT COUNT(*) as c FROM messages")
        mem_count = await db.fetch_one("SELECT COUNT(*) as c FROM memories WHERE status = 'active'")
        event_count = await db.fetch_one("SELECT COUNT(*) as c FROM events")
        journal_count = await db.fetch_one("SELECT COUNT(*) as c FROM journal_entries")

        click.echo("📊 Aphrodite Agent Statistics\n")
        click.echo(f"   Messages:       {msg_count['c'] if msg_count else 0}")
        click.echo(f"   Active memories: {mem_count['c'] if mem_count else 0}")
        click.echo(f"   Events:          {event_count['c'] if event_count else 0}")
        click.echo(f"   Journal entries: {journal_count['c'] if journal_count else 0}")

        chars = await ExportManager(config).list_characters()
        click.echo(f"   Characters:      {len(chars)}")
        for c in chars:
            click.echo(f"     - {c['id']} ({c['files']} files, {c['size_kb']} KB)")

        await db.close()

    asyncio.run(_stats())


@cli.command()
def version():
    """Show version."""
    click.echo("Aphrodite Agent v0.1.0")


@cli.command()
@click.option("--port", type=int, default=8765, help="Port to listen on")
@click.option("--host", default="127.0.0.1", help="Host to bind to")
@click.option("--allow-remote", is_flag=True, help="Allow binding beyond loopback")
@click.option("--character", "-C", help="Character name")
@click.pass_context
def api(ctx, port, host, allow_remote, character):
    """Start the REST API server."""
    config = _load_ctx_config(ctx)
    if allow_remote:
        config.api.allow_remote = True
    char = character or (ctx.obj.get("character") if ctx.obj else None)
    asyncio.run(run_api_server(config, host, port, char))


@cli.command()
@click.option("--hours", "-H", type=float, default=24, help="Hours to simulate")
@click.option("--speed", "-S", type=float, default=100, help="Speed multiplier")
@click.option("--character", "-C", help="Character name")
@click.option(
    "--mock-provider/--live-provider",
    default=True,
    help="Use the deterministic mock or configured live provider",
)
@click.option("--report", is_flag=True, help="Generate detailed report")
@click.pass_context
def simulate(ctx, hours, speed, character, mock_provider, report):
    """Run a simulation."""
    config = _load_ctx_config(ctx)
    char = character or (ctx.obj.get("character") if ctx.obj else None)

    async def _run():
        from aphrodite.db import Database

        db = Database(config.db_path)
        await db.initialize()

        engine = SimulationEngine(db, config)
        result = await engine.run(
            hours=hours,
            character=char or "mira",
            speed=speed,
            mock_provider=mock_provider,
        )

        click.echo("\n⏱  Simulation complete")
        click.echo(
            f"   Duration: {result.duration_hours}h in {result.real_time_seconds:.1f}s ({speed}x speed)"
        )
        click.echo(f"   Events: {result.total_events}")
        click.echo(f"   Journal entries: {result.total_journal_entries}")
        click.echo(f"   Messages sent: {result.total_messages}")
        click.echo(f"   Errors: {result.errors}")

        if report:
            click.echo("\n📊 Report:")
            click.echo(f"   Consistency: {result.consistency_score:.1%}")
            click.echo(f"   Events by type: {result.events_by_type}")

        await db.close()

    asyncio.run(_run())


@cli.command()
@click.pass_context
def selftest(ctx):
    """Run quick built-in checks (determinism, long-gap, stress)."""
    config = _load_ctx_config(ctx)

    async def _run():
        db = Database(config.db_path)
        await db.initialize()
        try:
            engine = SimulationEngine(db, config)
            det = await engine.run_determinism_test(runs=2)
            gap = await engine.run_long_gap_test(hours=24)
            stress = await engine.run_stress_test(interactions=50)
        finally:
            await db.close()

        click.echo("🔬 Aphrodite Self-Test\n")
        click.echo(
            f"   Determinism: {det['identical']}/{det['runs']} identical"
            f" ({'PASS' if det['diverged'] == 0 else 'FAIL'})"
        )
        click.echo(
            f"   Long gap (24h): coherent={gap['coherent']}"
            f" ({'PASS' if gap['coherent'] else 'FAIL'})"
        )
        click.echo(
            f"   Stress (50): {stress['interactions']} ok, {stress['errors']} errors"
            f" ({'PASS' if stress['errors'] == 0 else 'FAIL'})"
        )
        failed = det["diverged"] != 0 or not gap["coherent"] or stress["errors"] != 0
        if failed:
            raise click.exceptions.Exit(1)

    asyncio.run(_run())


@cli.command()
@click.pass_context
def proactive_check(ctx):
    """Check for and deliver a pending proactive message, if one is due."""
    config = _load_ctx_config(ctx)

    async def _run():
        app = AphroditeApp(config)
        await app.initialize()
        try:
            if app.world is None or app.character is None:
                click.echo("World or character unavailable.")
                return
            from aphrodite.proactive import ProactiveManager

            manager = ProactiveManager(app.db, config)
            state = await app.world.get_state()
            message = await manager.think_about_messaging(app.character, state, state.mood)
            if message is None:
                click.echo("No proactive message due.")
                return
            await manager.mark_sent(message.id)
            click.echo(f"💌 [{message.message_type}] {message.content}")
        finally:
            await app.close()

    asyncio.run(_run())


def _load_ctx_config(ctx=None) -> Config:
    """Load config from context."""
    config_path = None
    if ctx and ctx.obj.get("config_path"):
        config_path = Path(ctx.obj["config_path"])
    return load_config(config_path)


if __name__ == "__main__":
    cli()
