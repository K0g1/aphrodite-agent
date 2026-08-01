"""Coverage hardening tests (2026-07-31 audit, phase 4)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from aphrodite.config import Config
from aphrodite.db.database import Database

# ---------------------------------------------------------------------------
# Logging module
# ---------------------------------------------------------------------------


def test_setup_logging_text_format(tmp_path):
    import logging

    from aphrodite.logging import setup_logging

    config = Config(data_directory=str(tmp_path / "data"))
    config.logging.format = "text"
    config.logging.level = "WARNING"
    root = setup_logging(config)
    try:
        assert root.level == logging.WARNING
        assert len(root.handlers) == 1
    finally:
        root.handlers.clear()


def test_setup_logging_json_format_and_debug(tmp_path):
    import logging

    from aphrodite.logging import JsonFormatter, setup_logging

    config = Config(data_directory=str(tmp_path / "data"))
    config.logging.format = "json"
    config.logging.debug_mode = True
    root = setup_logging(config)
    try:
        assert root.level == logging.DEBUG
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
    finally:
        root.handlers.clear()


def test_json_formatter_output(tmp_path):
    import logging

    from aphrodite.logging import JsonFormatter

    record = logging.LogRecord(
        name="aphrodite.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    out = JsonFormatter().format(record)
    parsed = json.loads(out)
    assert parsed["logger"] == "aphrodite.test"
    assert parsed["msg"] == "hello world"
    assert parsed["level"] == "INFO"


def test_get_logger_namespace():
    from aphrodite.logging import get_logger

    assert get_logger("x").name == "aphrodite.x"


# ---------------------------------------------------------------------------
# Proactive manager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proactive_disabled_returns_none(tmp_path):
    from aphrodite.character import Character, CharacterIdentity
    from aphrodite.proactive import ProactiveManager
    from aphrodite.types import MoodState, WorldState

    config = Config(data_directory=str(tmp_path / "data"))
    config.proactive.enabled = False
    db = Database(config.db_path)
    await db.initialize()
    try:
        mgr = ProactiveManager(db, config)
        char = Character(id="mira", identity=CharacterIdentity(name="Mira"))
        msg = await mgr.think_about_messaging(char, WorldState(), MoodState())
        assert msg is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_proactive_quiet_hours_suppress(tmp_path):

    from aphrodite.character import Character, CharacterIdentity
    from aphrodite.proactive import ProactiveManager
    from aphrodite.types import MoodState, WorldState

    config = Config(data_directory=str(tmp_path / "data"))
    config.proactive.enabled = True
    config.timezone = "UTC"
    db = Database(config.db_path)
    await db.initialize()
    try:
        mgr = ProactiveManager(db, config)
        char = Character(id="mira", identity=CharacterIdentity(name="Mira"))
        # 23:30 UTC is inside quiet hours (22:00-08:00).
        msg = await mgr.think_about_messaging(
            char,
            WorldState(),
            MoodState(),
            now_utc=datetime(2026, 7, 31, 23, 30, tzinfo=UTC),
        )
        assert msg is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_proactive_respects_min_gap_and_max_per_day(tmp_path):
    from datetime import timedelta

    from aphrodite.character import Character, CharacterIdentity
    from aphrodite.proactive import ProactiveManager
    from aphrodite.types import MoodState, WorldState

    config = Config(data_directory=str(tmp_path / "data"))
    config.proactive.enabled = True
    config.proactive.min_gap_minutes = 120
    config.proactive.max_per_day = 2
    config.timezone = "UTC"
    db = Database(config.db_path)
    await db.initialize()
    try:
        mgr = ProactiveManager(db, config)
        char = Character(id="mira", identity=CharacterIdentity(name="Mira"))
        base = datetime(2026, 7, 31, 9, 0, tzinfo=UTC)

        first = await mgr.think_about_messaging(char, WorldState(), MoodState(), now_utc=base)
        assert first is not None
        # Deliver it: only sent messages consume quota / gap.
        await mgr.mark_sent(first.id)
        # Too soon: within min gap.
        soon = await mgr.think_about_messaging(
            char, WorldState(), MoodState(), now_utc=base + timedelta(minutes=30)
        )
        assert soon is None
        # After gap: allowed (second of max_per_day).
        later = await mgr.think_about_messaging(
            char, WorldState(), MoodState(), now_utc=base + timedelta(hours=3)
        )
        assert later is not None
        await mgr.mark_sent(later.id)
        # After gap again: exceeds max_per_day.
        latest = await mgr.think_about_messaging(
            char, WorldState(), MoodState(), now_utc=base + timedelta(hours=6)
        )
        assert latest is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_proactive_goodnight_and_share_from_life(tmp_path):

    from aphrodite.character import Character, CharacterIdentity
    from aphrodite.proactive import ProactiveManager
    from aphrodite.types import MoodState, WorldState

    config = Config(data_directory=str(tmp_path / "data"))
    config.proactive.enabled = True
    config.timezone = "UTC"
    db = Database(config.db_path)
    await db.initialize()
    try:
        mgr = ProactiveManager(db, config)
        char = Character(id="mira", identity=CharacterIdentity(name="Mira"))
        # 21:30 -> goodnight (quiet hours start at 22:00)
        msg = await mgr.think_about_messaging(
            char,
            WorldState(),
            MoodState(),
            now_utc=datetime(2026, 7, 31, 21, 30, tzinfo=UTC),
        )
        assert msg is not None and msg.message_type == "goodnight"
        # Next day mid-morning with high valence -> share_from_life
        msg2 = await mgr.think_about_messaging(
            char,
            WorldState(activity="gardening"),
            MoodState(valence=0.8, arousal=0.6),
            now_utc=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
        )
        assert msg2 is not None and msg2.message_type == "share_from_life"
        assert "gardening" in msg2.content
    finally:
        await db.close()


def test_proactive_quiet_hours_wrap():
    from aphrodite.config import Config
    from aphrodite.proactive import ProactiveManager

    config = Config()
    config.timezone = "UTC"
    config.proactive.quiet_hours_start = "22:00"
    config.proactive.quiet_hours_end = "08:00"
    mgr = ProactiveManager.__new__(ProactiveManager)
    mgr.config = config

    base = datetime(2026, 7, 31, tzinfo=UTC)
    assert mgr._is_in_waking_hours(base.replace(hour=9)) is True
    assert mgr._is_in_waking_hours(base.replace(hour=23)) is False
    assert mgr._is_in_waking_hours(base.replace(hour=7, minute=59)) is False
    assert mgr._is_in_waking_hours(base.replace(hour=8)) is True


# ---------------------------------------------------------------------------
# Provider client
# ---------------------------------------------------------------------------


def test_strip_thinking_variants():
    from aphrodite.providers import Provider

    provider = Provider.__new__(Provider)
    assert provider._strip_thinking("<think>secret</think>hello") == "hello"
    assert provider._strip_thinking("<THINKING>secret</THINKING>hello") == "hello"
    assert provider._strip_thinking("before <thinking>secret") == "before "
    assert provider._strip_thinking("<think >x</think>ok") == "ok"


@pytest.mark.asyncio
async def test_stream_completion_happy_path():
    from aphrodite.config import ProviderInstanceConfig
    from aphrodite.providers import Provider

    class _Stream:
        def __init__(self):
            self._lines = [
                'data: {"choices": [{"delta": {"content": "Hello "}}]}',
                'data: {"choices": [{"delta": {"content": "world"}}]}',
                "data: [DONE]",
            ]

        def raise_for_status(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def aiter_lines(self):
            for line in self._lines:
                yield line

    class _Client:
        def __init__(self):
            self.is_closed = False

        def stream(self, method, url, json=None):
            return _Stream()

        async def aclose(self):
            self.is_closed = True

    config = ProviderInstanceConfig(base_url="http://localhost:9/v1", model="test")
    provider = Provider(config)
    provider._client = _Client()  # type: ignore[attr-defined]
    out = await provider.complete([{"role": "user", "content": "hi"}], stream=True)
    assert out == "Hello world"


@pytest.mark.asyncio
async def test_health_check_ok_and_failure():
    from aphrodite.config import ProviderInstanceConfig
    from aphrodite.providers import Provider

    class _Resp:
        def __init__(self, status, payload=None):
            self.status_code = status
            self._payload = payload

        def json(self):
            return self._payload

    class _Client:
        is_closed = False

        def __init__(self, ok):
            self.ok = ok

        async def get(self, url):
            if not self.ok:
                raise RuntimeError("connection refused")
            return _Resp(200, {"data": [{"id": "m"}]})

        async def aclose(self):
            pass

    config = ProviderInstanceConfig(base_url="http://localhost:9/v1", model="test")
    provider = Provider(config)
    provider._client = _Client(True)  # type: ignore[attr-defined]
    assert await provider.health_check() is True
    provider._client = _Client(False)  # type: ignore[attr-defined]
    assert await provider.health_check() is False


# ---------------------------------------------------------------------------
# Memory extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extraction_llm_happy_and_keyword_fallback(tmp_path):
    from aphrodite.extraction import MemoryExtractor

    class _LLM:
        def __init__(self, response):
            self.response = response

        async def complete(self, messages, **kwargs):
            return self.response

    extractor = MemoryExtractor(_LLM("preference|User likes mint tea|0.9|normal|0.6"))
    memories = await extractor.extract("I like mint tea", "Cool!")
    assert len(memories) == 1
    assert memories[0].memory_type.value == "preference"
    assert memories[0].confidence == 0.9

    # Garbage LLM output (non-raising) -> no memories, no fallback.
    extractor = MemoryExtractor(_LLM("not a valid line"))
    memories = await extractor.extract("I work at a hospital", "Nice")
    assert memories == []

    # NONE response -> nothing.
    extractor = MemoryExtractor(_LLM("NONE"))
    memories = await extractor.extract("Just chatting", "Sure")
    assert memories == []

    # No LLM -> keywords only.
    extractor = MemoryExtractor(None)
    memories = await extractor.extract("I live in Vancouver", "Cool")
    assert len(memories) == 1
    assert memories[0].memory_type.value == "fact"


@pytest.mark.asyncio
async def test_extraction_llm_error_falls_back(tmp_path):
    from aphrodite.extraction import MemoryExtractor

    class _Broken:
        async def complete(self, messages, **kwargs):
            raise RuntimeError("boom")

    memories = await MemoryExtractor(_Broken()).extract("I need to study", "Good luck")
    assert len(memories) == 1
    assert memories[0].memory_type.value == "open_loop"


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def _write_cli_config(tmp_path, extra=""):
    cfg = tmp_path / "aphrodite.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(f'[general]\ndata_directory = "{tmp_path / "data"}"\n{extra}', encoding="utf-8")
    return str(cfg)


def test_cli_version_and_characters(tmp_path):
    from click.testing import CliRunner

    from aphrodite_cli.main import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["version"])
    assert result.exit_code == 0
    assert "v0.1.0" in result.output

    cfg = _write_cli_config(tmp_path)
    result = runner.invoke(cli, ["--config", cfg, "characters"])
    assert result.exit_code == 0


def test_cli_create_character_and_export_import(tmp_path):
    from click.testing import CliRunner

    from aphrodite_cli.main import cli

    cfg = _write_cli_config(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        cli, ["--config", cfg, "create", "--character", "zelda"], input="24\nshe/her\n"
    )
    assert result.exit_code == 0, result.output
    assert "zelda" in result.output

    # characters now shows it
    result = runner.invoke(cli, ["--config", cfg, "characters"])
    assert "zelda" in result.output

    # export it
    card = tmp_path / "cards" / "zelda.aphrocard"
    result = runner.invoke(cli, ["--config", cfg, "export", "zelda", "-o", str(card)])
    assert result.exit_code == 0, result.output
    assert card.exists()

    # import it under a new data dir
    cfg2 = _write_cli_config(tmp_path / "second", "")
    result = runner.invoke(cli, ["--config", cfg2, "import-char", str(card)])
    assert result.exit_code == 0, result.output


def test_cli_export_memories_and_stats(tmp_path):
    from click.testing import CliRunner

    from aphrodite_cli.main import cli

    # create a db with a memory first
    data = tmp_path / "data"
    cfg = _write_cli_config(tmp_path)
    runner = CliRunner()

    async def _seed():
        config = Config(data_directory=str(data))
        db = Database(config.db_path)
        await db.initialize()
        from aphrodite.memory import MemoryManager

        await MemoryManager(db, config).add_memory("Test memory", memory_type="fact")
        await db.close()

    asyncio_run(_seed())

    out = tmp_path / "mem.json"
    result = runner.invoke(cli, ["--config", cfg, "export-memories", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()
    data_json = json.loads(out.read_text(encoding="utf-8"))
    assert data_json["count"] == 1

    result = runner.invoke(cli, ["--config", cfg, "stats"])
    assert result.exit_code == 0, result.output
    assert "Messages:" in result.output


def test_cli_advance_and_simulate_and_selftest(tmp_path):
    from click.testing import CliRunner

    from aphrodite_cli.main import cli

    cfg = _write_cli_config(tmp_path)
    runner = CliRunner()

    result = runner.invoke(cli, ["--config", cfg, "advance", "--hours", "1"])
    assert result.exit_code == 0, result.output
    assert "Advanced 1" in result.output

    result = runner.invoke(cli, ["--config", cfg, "simulate", "--hours", "1", "--speed", "10"])
    assert result.exit_code == 0, result.output
    assert "Simulation complete" in result.output

    result = runner.invoke(cli, ["--config", cfg, "selftest"])
    assert result.exit_code == 0, result.output


def test_cli_chat_provider_failure_is_graceful(tmp_path):
    from click.testing import CliRunner

    from aphrodite_cli.main import cli

    cfg = _write_cli_config(
        tmp_path,
        extra='[provider]\nactive = "broken"\n[provider.instances.broken]\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "test"\n',
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["--config", cfg, "chat", "hello"])
    assert result.exit_code == 0, result.output
    assert "Provider unavailable" in result.output


# ---------------------------------------------------------------------------
# API server handlers
# ---------------------------------------------------------------------------


async def _make_api_client(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    from aphrodite.api.server import APIHandler, create_api_application

    config = Config(data_directory=str(tmp_path / "data"))
    config.characters_dir.mkdir(parents=True, exist_ok=True)
    handler = APIHandler(config)
    await handler.initialize("mira")
    client = TestClient(TestServer(create_api_application(handler)))
    await client.start_server()
    return client, handler


@pytest.mark.asyncio
async def test_api_health_and_world_and_characters(tmp_path):
    client, handler = await _make_api_client(tmp_path)
    try:
        headers = {"Authorization": f"Bearer {handler.token}"}
        health = await client.get("/health")
        assert health.status == 200
        body = await health.json()
        assert body["status"] == "healthy"

        world = await client.get("/v1/world/state", headers=headers)
        assert world.status == 200
        assert (await world.json())["character"] == "Mira"

        chars = await client.get("/v1/characters", headers=headers)
        assert chars.status == 200
        assert (await chars.json())["characters"] != []

        journal = await client.get("/v1/journal/latest", headers=headers)
        assert journal.status == 200
    finally:
        await client.close()
        await handler.handle_close()


@pytest.mark.asyncio
async def test_api_advance_and_simulate(tmp_path):
    client, handler = await _make_api_client(tmp_path)
    try:
        headers = {"Authorization": f"Bearer {handler.token}"}
        adv = await client.post("/v1/world/advance", json={"hours": 2}, headers=headers)
        assert adv.status == 200
        body = await adv.json()
        assert body["hours_advanced"] == 2

        sim = await client.post("/v1/simulate", json={"hours": 1, "speed": 5}, headers=headers)
        assert sim.status == 200
        sim_body = await sim.json()
        assert sim_body["duration_hours"] == 1
    finally:
        await client.close()
        await handler.handle_close()


@pytest.mark.asyncio
async def test_api_validation_and_error_paths(tmp_path):
    client, handler = await _make_api_client(tmp_path)
    try:
        headers = {"Authorization": f"Bearer {handler.token}"}

        # Unauthorized
        resp = await client.get("/v1/characters")
        assert resp.status == 401

        # OPTIONS rejected (auth middleware runs first on /v1/*)
        resp = await client.options("/v1/chat")
        assert resp.status == 401

        # Bad JSON
        resp = await client.post("/v1/chat", data=b"{not json", headers=headers)
        assert resp.status == 400

        # Invalid hours
        resp = await client.post("/v1/world/advance", json={"hours": -5}, headers=headers)
        assert resp.status == 400

        # Unknown route (exists only for OPTIONS -> method not allowed)
        resp = await client.get("/v1/nope", headers=headers)
        assert resp.status == 405

        # Provider failure -> 502 (chat with unreachable provider)
        from aiohttp.test_utils import TestClient, TestServer

        from aphrodite.api.server import APIHandler, create_api_application
        from aphrodite.config import ProviderInstanceConfig

        bad_cfg = Config(data_directory=str(tmp_path / "data2"))
        bad_cfg.characters_dir.mkdir(parents=True, exist_ok=True)
        bad_cfg.provider_active = "broken"
        bad_cfg.providers["broken"] = ProviderInstanceConfig(
            base_url="http://127.0.0.1:9/v1", model="test"
        )
        bad_handler = APIHandler(bad_cfg)
        await bad_handler.initialize("mira")
        bad_client = TestClient(TestServer(create_api_application(bad_handler)))
        await bad_client.start_server()
        try:
            resp = await bad_client.post(
                "/v1/chat",
                json={"message": "hello"},
                headers={"Authorization": f"Bearer {bad_handler.token}"},
            )
            assert resp.status == 502
        finally:
            await bad_client.close()
            await bad_handler.handle_close()
    finally:
        await client.close()
        await handler.handle_close()


@pytest.mark.asyncio
async def test_api_chat_message_validation(tmp_path):
    client, handler = await _make_api_client(tmp_path)
    try:
        headers = {"Authorization": f"Bearer {handler.token}"}
        resp = await client.post("/v1/chat", json={}, headers=headers)
        assert resp.status == 400
        resp = await client.post("/v1/chat", json={"message": "   "}, headers=headers)
        assert resp.status == 400
        resp = await client.post("/v1/chat", json={"message": "x" * 20000}, headers=headers)
        assert resp.status == 413
    finally:
        await client.close()
        await handler.handle_close()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_env_var_resolution(tmp_path, monkeypatch):
    from aphrodite.config import load_config

    monkeypatch.setenv("APHRO_TEST_KEY", "sk-secret")
    cfg = tmp_path / "aphrodite.toml"
    cfg.write_text(
        '[provider.instances.primary]\napi_key = "${APHRO_TEST_KEY}"\n', encoding="utf-8"
    )
    config = load_config(cfg)
    assert config.providers["primary"].api_key == "sk-secret"


def test_config_invalid_values_rejected(tmp_path):
    from aphrodite.config import load_config

    cfg = tmp_path / "aphrodite.toml"
    cfg.write_text('[provider.instances.primary]\nbase_url = "not-a-url"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="base_url"):
        load_config(cfg)

    cfg.write_text('[general]\ntimezone = "Mars/Olympus"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="timezone"):
        load_config(cfg)


def test_config_context_profile_mapping(tmp_path):
    from aphrodite.config import load_config

    cfg = tmp_path / "aphrodite.toml"
    cfg.write_text('[context]\nactive_profile = "16k"\n', encoding="utf-8")
    config = load_config(cfg)
    assert config.max_input_tokens == 12000


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)
