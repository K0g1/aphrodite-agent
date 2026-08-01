"""Regression tests added during the July 2026 critical audit."""

from __future__ import annotations

import pytest

from aphrodite.character import parse_character
from aphrodite.config import Config
from aphrodite.export import ExportManager


def test_generated_nested_personality_sliders_are_loaded(tmp_path):
    character_dir = tmp_path / "mira"
    character_dir.mkdir()
    (character_dir / "personality.md").write_text(
        """---
sliders:
  warmth: 0.9
  directness: 0.2
  flirtation: 0.1
---

# Personality
Custom personality.
""",
        encoding="utf-8",
    )

    character = parse_character(character_dir)

    assert character.personality.warmth == 0.9
    assert character.personality.directness == 0.2
    assert character.personality.flirtation == 0.1


@pytest.mark.asyncio
async def test_export_rejects_character_id_path_traversal(tmp_path):
    config = Config(data_directory=str(tmp_path / "data"))
    config.characters_dir.mkdir(parents=True)

    with pytest.raises(ValueError, match="character ID"):
        await ExportManager(config).export_character("../../outside")


def test_archive_extraction_rejects_parent_traversal(tmp_path):
    import io
    import tarfile

    from aphrodite.export import _safe_extract_tar

    archive = tmp_path / "malicious.aphrocard"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("../../escaped.txt")
        payload = b"escape attempt"
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))

    with tarfile.open(archive, "r:gz") as tar:
        with pytest.raises(ValueError, match="unsafe archive member"):
            _safe_extract_tar(tar, tmp_path / "extract")

    assert not (tmp_path / "escaped.txt").exists()


@pytest.mark.asyncio
async def test_v1_api_requires_bearer_token_and_does_not_enable_cross_origin_reads(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    from aphrodite.api.server import APIHandler, create_api_application

    config = Config(data_directory=str(tmp_path / "data"))
    config.characters_dir.mkdir(parents=True)
    handler = APIHandler(config)
    client = TestClient(TestServer(create_api_application(handler)))
    await client.start_server()
    try:
        unauthorized = await client.get("/v1/characters")
        assert unauthorized.status == 401

        authorized = await client.get(
            "/v1/characters",
            headers={
                "Authorization": f"Bearer {handler.token}",
                "Origin": "https://attacker.example",
            },
        )
        assert authorized.status == 200
        assert "Access-Control-Allow-Origin" not in authorized.headers
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_bundled_ui_uses_token_and_does_not_interpolate_api_data_into_html(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    from aphrodite.api.server import APIHandler, create_api_application

    config = Config(data_directory=str(tmp_path / "data"))
    handler = APIHandler(config)
    client = TestClient(TestServer(create_api_application(handler)))
    await client.start_server()
    try:
        response = await client.get("/")
        html = await response.text()
    finally:
        await client.close()

    assert f'const API_TOKEN = "{handler.token}";' in html
    assert 'headers.set("Authorization", `Bearer ${API_TOKEN}`)' in html
    for unsafe_interpolation in ("${d.body", "${d.summary", "${c.id", "${r.content", "${e.message"):
        assert unsafe_interpolation not in html


def test_public_api_bind_requires_explicit_opt_in():
    from aphrodite.api.server import _validate_bind_host

    _validate_bind_host("127.0.0.1", allow_remote=False)
    _validate_bind_host("::1", allow_remote=False)
    with pytest.raises(ValueError, match="allow_remote"):
        _validate_bind_host("0.0.0.0", allow_remote=False)
    _validate_bind_host("0.0.0.0", allow_remote=True)


@pytest.mark.asyncio
async def test_memory_source_message_id_round_trips_through_sqlite(tmp_path):
    from aphrodite.db import Database
    from aphrodite.memory import MemoryManager

    config = Config(data_directory=str(tmp_path))
    database = Database(config.db_path)
    await database.initialize()
    try:
        manager = MemoryManager(database, config)
        created = await manager.add_memory(
            "User prefers concise answers.",
            source_message_id="message-123",
        )
        stored = await database.fetch_one("SELECT * FROM memories WHERE id = ?", (created.id,))
        loaded = manager._row_to_memory(stored)
    finally:
        await database.close()

    assert stored["source_message_id"] == "message-123"
    assert loaded.source_message_id == "message-123"


@pytest.mark.asyncio
async def test_journal_json_fields_round_trip(tmp_path):
    from datetime import datetime, timezone

    from aphrodite.character import Character, CharacterIdentity
    from aphrodite.db import Database
    from aphrodite.journal import JournalManager
    from aphrodite.types import MoodState

    config = Config(data_directory=str(tmp_path))
    database = Database(config.db_path)
    await database.initialize()
    try:
        journal = JournalManager(database, config)
        mood = MoodState(valence=0.7, arousal=0.2)
        await journal.write_entry(
            Character(identity=CharacterIdentity(name="Mira")),
            mood,
            world_events=[{"id": "event-1", "title": "Made tea"}],
            local_date="2026-07-29",
            now_utc=datetime(2026, 7, 30, tzinfo=timezone.utc),
        )
        loaded = await journal.get_entry("2026-07-29")
    finally:
        await database.close()

    assert loaded is not None
    assert loaded.source_event_ids == ["event-1"]
    assert loaded.mood_before.valence == 0.7
    assert loaded.mood_before.arousal == 0.2


@pytest.mark.asyncio
async def test_database_schema_contains_proactive_events_table(tmp_path):
    from aphrodite.db import Database

    database = Database(tmp_path / "aphrodite.db")
    await database.initialize()
    try:
        row = await database.fetch_one(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("proactive_events",),
        )
    finally:
        await database.close()

    assert row == {"name": "proactive_events"}


@pytest.mark.asyncio
async def test_first_world_update_initializes_persisted_clock(tmp_path):
    from datetime import datetime, timezone

    from aphrodite.db import Database
    from aphrodite.world import WorldEngine

    config = Config(data_directory=str(tmp_path))
    database = Database(config.db_path)
    await database.initialize()
    try:
        engine = WorldEngine(database, config)
        now = datetime(2026, 7, 29, 18, 0, tzinfo=timezone.utc)
        await engine.update_state(now)
        stored = await database.get_world_state()
    finally:
        await database.close()

    assert stored["last_processed_utc"] == now.isoformat()


def test_proactive_quiet_hours_use_configured_timezone_and_wrap_midnight():
    from datetime import datetime, timezone

    from aphrodite.proactive import ProactiveManager

    config = Config(timezone="America/Vancouver")
    manager = ProactiveManager.__new__(ProactiveManager)
    manager.config = config

    quiet_local_23 = datetime(2026, 7, 30, 6, 0, tzinfo=timezone.utc)
    awake_local_12 = datetime(2026, 7, 30, 19, 0, tzinfo=timezone.utc)

    assert not manager._is_in_waking_hours(quiet_local_23)
    assert manager._is_in_waking_hours(awake_local_12)


def test_proactive_template_selection_is_stable_for_supplied_time():
    from datetime import datetime, timezone
    import hashlib

    from aphrodite.character import Character, CharacterIdentity
    from aphrodite.proactive import PROACTIVE_PROMPTS, ProactiveManager
    from aphrodite.types import WorldState

    manager = ProactiveManager.__new__(ProactiveManager)
    character = Character(identity=CharacterIdentity(name="Mira"))
    now = datetime(2026, 7, 30, 19, 0, tzinfo=timezone.utc)
    templates = PROACTIVE_PROMPTS["check_in"]
    seed = f"Mira|check_in|{now.isoformat()}"
    expected = templates[int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(templates)]

    actual = manager._generate_message("check_in", character, WorldState(), now)

    assert actual == expected


@pytest.mark.asyncio
async def test_provider_preserves_explicit_zero_temperature():
    from aphrodite.config import ProviderInstanceConfig
    from aphrodite.providers import Provider

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    class FakeClient:
        is_closed = False

        def __init__(self):
            self.payload = None

        async def post(self, path, json):
            self.payload = json
            return FakeResponse()

    provider = Provider(ProviderInstanceConfig(temperature=0.8))
    fake_client = FakeClient()
    provider._client = fake_client

    await provider.complete([{"role": "user", "content": "extract"}], temperature=0.0)

    assert fake_client.payload["temperature"] == 0.0


def test_prompt_assembly_rebuilds_sections_until_request_fits_usable_budget():
    from datetime import datetime, timezone

    from aphrodite.character import Character, CharacterIdentity
    from aphrodite.context import assemble_prompt
    from aphrodite.types import Memory, MoodState, WorldState

    memories = [Memory(content=f"memory-{index} " + "x" * 500) for index in range(30)]
    result = assemble_prompt(
        character=Character(identity=CharacterIdentity(name="Mira", core_identity="Companion")),
        mood=MoodState(),
        world=WorldState(),
        short_term_memories=memories,
        long_term_memories=memories,
        recent_turns=[],
        current_user_message="latest request",
        now=datetime(2026, 7, 30, tzinfo=timezone.utc),
        max_input_tokens=1400,
        response_reserve=300,
    )

    assert result.total_input_tokens <= 1100
    assert result.messages[-1] == {"role": "user", "content": "latest request"}


@pytest.mark.asyncio
async def test_long_gap_report_reads_database_state_mapping(tmp_path):
    from aphrodite.db import Database
    from aphrodite.simulation import SimulationEngine

    config = Config(data_directory=str(tmp_path))
    database = Database(config.db_path)
    await database.initialize()
    try:
        result = await SimulationEngine(database, config).run_long_gap_test(hours=24)
    finally:
        await database.close()

    assert result["hours_simulated"] == 24
    assert isinstance(result["final_activity"], str)
    assert isinstance(result["coherent"], bool)


@pytest.mark.asyncio
async def test_determinism_runs_use_isolated_engines_and_leave_no_database_artifacts(tmp_path):
    from aphrodite.db import Database
    from aphrodite.simulation import SimulationEngine

    config = Config(data_directory=str(tmp_path))
    database = Database(config.db_path)
    await database.initialize()
    try:
        result = await SimulationEngine(database, config).run_determinism_test(runs=3)
    finally:
        await database.close()

    assert result["runs"] == 3
    assert result["identical"] == 3
    assert result["diverged"] == 0
    assert not list(tmp_path.glob("test_run_*.db"))


@pytest.mark.asyncio
async def test_scripted_state_assertion_affects_simulation_consistency_report(tmp_path):
    from aphrodite.db import Database
    from aphrodite.simulation import SimulationEngine, SimulationScript

    config = Config(data_directory=str(tmp_path))
    database = Database(config.db_path)
    await database.initialize()
    try:
        report = await SimulationEngine(database, config).run(
            hours=0,
            script=SimulationScript(
                steps=[{"type": "check_state", "expected": {"current_activity": "impossible"}}]
            ),
        )
    finally:
        await database.close()

    assert report.warnings == 1
    assert report.consistency_score == 0.0


@pytest.mark.asyncio
async def test_live_simulation_mode_constructs_the_configured_provider_without_calling_it(tmp_path):
    from aphrodite.db import Database
    from aphrodite.providers import Provider
    from aphrodite.simulation import SimulationEngine

    config = Config(data_directory=str(tmp_path))
    database = Database(config.db_path)
    await database.initialize()
    try:
        engine = SimulationEngine(database, config)
        report = await engine.run(hours=0, mock_provider=False)
    finally:
        await database.close()

    assert report.provider_mode == "live"
    assert isinstance(engine.provider, Provider)


def test_world_local_time_uses_configured_timezone():
    from datetime import datetime, timezone

    from aphrodite.world import WorldEngine

    engine = WorldEngine.__new__(WorldEngine)
    engine.config = Config(timezone="Asia/Tokyo")

    local = engine._to_local_time(datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc))

    assert local.hour == 21
    assert str(local.tzinfo) == "Asia/Tokyo"


@pytest.mark.asyncio
async def test_api_rejects_excessive_chat_search_and_simulation_workloads():
    from aphrodite.api.server import APIHandler

    class StubApp:
        async def chat(self, message):
            raise AssertionError("oversized chat must not reach the app")

    handler = APIHandler(Config())
    handler.app = StubApp()

    chat_result = await handler.handle_chat({"message": "x" * 12_001})
    search_result = await handler.handle_memory_search("q" * 501)
    simulation_result = await handler.handle_simulate({"hours": 8_761})

    assert chat_result[1] == 413
    assert search_result[1] == 400
    assert simulation_result[1] == 400


@pytest.mark.asyncio
async def test_world_state_update_rejects_unknown_sql_columns(tmp_path):
    from aphrodite.db import Database

    database = Database(tmp_path / "aphrodite.db")
    await database.initialize()
    try:
        with pytest.raises(ValueError, match="Unknown world-state column"):
            await database.update_world_state(**{"current_activity = 'owned' --": "ignored"})
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_journal_due_time_uses_configured_timezone(tmp_path):
    from datetime import datetime, timezone

    from aphrodite.db import Database
    from aphrodite.journal import JournalManager

    config = Config(data_directory=str(tmp_path), timezone="Asia/Tokyo")
    database = Database(config.db_path)
    await database.initialize()
    try:
        journal = JournalManager(database, config)
        due = await journal.is_due(datetime(2026, 7, 30, 12, 30, tzinfo=timezone.utc))
    finally:
        await database.close()

    assert due


def test_load_config_rejects_unknown_timezone(tmp_path):
    from aphrodite.config import load_config

    config_file = tmp_path / "aphrodite.toml"
    config_file.write_text(
        f'[general]\ntimezone = "Mars/Olympus_Mons"\ndata_directory = "{tmp_path / "data"}"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unknown timezone"):
        load_config(config_file)


def test_simulation_cli_exposes_explicit_live_provider_mode():
    from click.testing import CliRunner

    from aphrodite_cli.main import cli

    result = CliRunner().invoke(cli, ["simulate", "--help"])

    assert result.exit_code == 0
    assert "--live-provider" in result.output


@pytest.mark.asyncio
async def test_app_chat_orchestrates_persistence_world_and_cleanup_without_network(tmp_path):
    from typing import cast

    from aphrodite.app import AphroditeApp
    from aphrodite.providers import Provider

    class FakeProvider:
        def __init__(self):
            self.closed = False

        async def complete(self, messages, **kwargs):
            if messages and "Analyze this conversation" in messages[0]["content"]:
                return "NONE"
            return "A grounded local response."

        async def close(self):
            self.closed = True

    config = Config(data_directory=str(tmp_path), default_character="mira")
    app = AphroditeApp(config)
    await app.initialize()
    fake_provider = FakeProvider()
    app.provider = cast(Provider, fake_provider)

    try:
        response = await app.chat("Please remember that I like tea.")
        rows = await app.db.get_recent_messages("default", limit=10)
        state = await app.db.get_world_state()
    finally:
        await app.close()

    assert response == "A grounded local response."
    assert {row["role"] for row in rows} == {"user", "assistant"}
    assert state is not None and state["last_processed_utc"]
    assert fake_provider.closed
    assert (config.characters_dir / "mira" / "identity.md").exists()
