"""Regression tests for findings from the 2026-07-31 final audit (round 2)."""

from __future__ import annotations

import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aphrodite.config import Config
from aphrodite.db.database import Database
from aphrodite.export import ExportManager
from aphrodite.world import WorldEngine


@pytest.mark.asyncio
async def test_import_character_restores_memories(tmp_path):
    """A2: importing a .aphrocard must restore the exported memories, not drop them."""
    config = Config(data_directory=str(tmp_path / "data"))
    config.characters_dir.mkdir(parents=True)
    (config.characters_dir / "mira").mkdir()
    (config.characters_dir / "mira" / "identity.md").write_text(
        "---\nname: Mira\n---\n\n# Identity\nA test character.\n", encoding="utf-8"
    )

    db = Database(config.db_path)
    await db.initialize()
    from aphrodite.memory import MemoryManager

    mgr = MemoryManager(db, config)
    await mgr.add_memory("User loves purple umbrellas", memory_type="fact")

    card = str(tmp_path / "mira.aphrocard")
    await ExportManager(config, db).export_character("mira", card, include_memories=True)
    await db.close()

    # Wipe everything, then import into a fresh environment.
    import shutil

    shutil.rmtree(config.characters_dir)
    config.db_path.unlink(missing_ok=True)

    db2 = Database(config.db_path)
    await db2.initialize()
    imported = await ExportManager(config, db2).import_character(card)
    rows = await db2.fetch_all("SELECT content FROM memories WHERE status = 'active'")
    await db2.close()

    assert imported == "mira"
    assert len(rows) == 1
    assert rows[0]["content"] == "User loves purple umbrellas"


@pytest.mark.asyncio
async def test_event_local_date_uses_configured_timezone(tmp_path):
    """A5: events must be dated in the configured local timezone, not UTC."""
    config = Config(data_directory=str(tmp_path / "data"))
    config.timezone = "Pacific/Auckland"  # UTC+12/+13
    config.characters_dir.mkdir(parents=True, exist_ok=True)

    db = Database(config.db_path)
    await db.initialize()
    engine = WorldEngine(db, config)
    # 2026-07-31 23:30 UTC is 2026-08-01 11:30 in Auckland.
    await engine.update_state(datetime(2026, 7, 31, 23, 30, tzinfo=UTC))
    row = await db.fetch_one("SELECT local_date FROM events ORDER BY created_at_utc LIMIT 1")
    await db.close()

    assert row is not None
    assert row["local_date"] == "2026-08-01"


@pytest.mark.asyncio
async def test_provider_stream_error_raises_not_empty_success():
    """A6: a streamed {'error': ...} object must raise ProviderError, not return ''."""
    from aphrodite.config import ProviderInstanceConfig
    from aphrodite.providers import Provider, ProviderError

    class _FakeStream:
        def __init__(self):
            self._lines = [
                'data: {"choices": [{"delta": {"content": "partial"}}]}',
                'data: {"error": {"message": "upstream exploded"}}',
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

    class _FakeClient:
        def __init__(self):
            self.is_closed = False

        def stream(
            self, method, url, json=None
        ):  # sync on purpose: httpx AsyncClient.stream is a coroutine fn
            return _FakeStream()

        async def aclose(self):
            self.is_closed = True

    config = ProviderInstanceConfig(base_url="http://localhost:9/v1", model="test")
    provider = Provider(config)
    provider._client = _FakeClient()  # type: ignore[attr-defined]

    with pytest.raises(ProviderError, match="upstream exploded"):
        await provider.complete([{"role": "user", "content": "hi"}], stream=True)


@pytest.mark.asyncio
async def test_chat_provider_failure_propagates_and_does_not_pollute_history(tmp_path):
    """A1: provider outage must raise and must not save '[Provider error...]' as a reply."""
    from aphrodite.app import AphroditeApp
    from aphrodite.providers import ProviderError

    class _FailingProvider:
        async def complete(self, messages, **kwargs):
            raise ProviderError("simulated outage", status_code=503)

        async def close(self):
            pass

    config = Config(data_directory=str(tmp_path / "data"))
    app = AphroditeApp(config)
    await app.initialize("mira")
    app.provider = _FailingProvider()  # type: ignore[assignment]

    with pytest.raises(ProviderError, match="simulated outage"):
        await app.chat("hello there")

    rows = await app.db.fetch_all("SELECT role, content FROM messages ORDER BY created_at_utc")
    await app.close()

    # The user message is rolled back on provider failure: no orphaned turns.
    assert rows == []


def test_export_manifest_is_truthful_without_database(tmp_path):
    """C1: manifest must not claim memories were included when none could be."""
    config = Config(data_directory=str(tmp_path / "data"))
    config.characters_dir.mkdir(parents=True)
    (config.characters_dir / "mira").mkdir()
    (config.characters_dir / "mira" / "identity.md").write_text(
        "---\nname: Mira\n---\n\n# Identity\nA test character.\n", encoding="utf-8"
    )

    async def _run():
        card = str(tmp_path / "mira.aphrocard")
        await ExportManager(config).export_character("mira", card, include_memories=True)
        with tarfile.open(card, "r:gz") as tar:
            member = tar.extractfile("mira/manifest.json")
            assert member is not None
            manifest = json.loads(member.read())
        return manifest

    manifest = asyncio_run(_run())
    assert manifest["includes_memories"] is False


@pytest.mark.asyncio
async def test_mood_decay_uses_configured_baselines(tmp_path):
    """B2: world mood decay must approach the configured baselines, not hardcoded ones."""
    config = Config(data_directory=str(tmp_path / "data"))
    config.mood.baseline_valence = 0.7
    config.mood.baseline_arousal = 0.1

    db = Database(config.db_path)
    await db.initialize()
    engine = WorldEngine(db, config)

    from aphrodite.types import MoodState

    decayed = engine._decay_mood(
        MoodState(
            valence=0.1, arousal=0.9, dominance=0.5, affection=0.55, trust=0.5, curiosity=0.65
        ),
        hours_elapsed=100,
    )
    await db.close()

    assert decayed.valence == pytest.approx(0.7, abs=0.01)
    assert decayed.arousal == pytest.approx(0.1, abs=0.01)


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Batch 2: atomicity, CLI exit codes, export path handling, API validation,
# character parser robustness.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_world_state_is_single_atomic_statement(tmp_path):
    """A8: multi-column world-state updates must land atomically (one statement)."""
    from aphrodite.world import WorldEngine

    config = Config(data_directory=str(tmp_path / "data"))
    config.characters_dir.mkdir(parents=True, exist_ok=True)
    db = Database(config.db_path)
    await db.initialize()
    engine = WorldEngine(db, config)
    await engine.update_state(datetime(2026, 7, 31, 10, 0, tzinfo=UTC))

    before = await db.fetch_one("SELECT revision FROM world_state WHERE id = 1")
    await db.update_world_state(
        current_activity="walking in the park",
        current_setting="park",
        updated_at_utc="2026-07-31T12:00:00+00:00",
    )
    after = await db.fetch_one(
        "SELECT revision, current_activity, current_setting FROM world_state WHERE id = 1"
    )
    await db.close()

    assert before is not None and after is not None
    assert after["revision"] == before["revision"] + 1
    assert after["current_activity"] == "walking in the park"
    assert after["current_setting"] == "park"


@pytest.mark.asyncio
async def test_update_world_state_unknown_column_is_noop(tmp_path):
    """A8: an unknown column must raise without bumping revision."""
    config = Config(data_directory=str(tmp_path / "data"))
    config.characters_dir.mkdir(parents=True, exist_ok=True)
    db = Database(config.db_path)
    await db.initialize()

    before = await db.fetch_one("SELECT revision FROM world_state WHERE id = 1")
    with pytest.raises(ValueError, match="Unknown world-state column"):
        await db.update_world_state(hacker_column="x")
    after = await db.fetch_one("SELECT revision FROM world_state WHERE id = 1")
    await db.close()

    assert before is not None and after is not None
    assert after["revision"] == before["revision"]


def test_doctor_exits_nonzero_when_database_missing(tmp_path):
    """C2: doctor must signal failure via exit code, not always exit 0."""
    from click.testing import CliRunner

    from aphrodite_cli.main import cli

    cfg = tmp_path / "aphrodite.toml"
    cfg.write_text(
        f'[general]\ndata_directory = "{(tmp_path / "data").as_posix()}"\n', encoding="utf-8"
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["--config", str(cfg), "doctor"])
    assert result.exit_code != 0
    assert "Database not found" in result.output


@pytest.mark.asyncio
async def test_export_creates_missing_output_parent_dirs(tmp_path):
    """C4: exporting to a path whose parent dirs do not exist must succeed."""
    config = Config(data_directory=str(tmp_path / "data"))
    config.characters_dir.mkdir(parents=True)
    (config.characters_dir / "mira").mkdir()
    (config.characters_dir / "mira" / "identity.md").write_text(
        "---\nname: Mira\n---\n\n# Identity\nA test character.\n", encoding="utf-8"
    )

    out = tmp_path / "nested" / "deeper" / "mira.aphrocard"
    card = await ExportManager(config).export_character("mira", str(out))
    assert Path(card).exists()


@pytest.mark.asyncio
async def test_memory_search_rejects_empty_query(tmp_path):
    """D2: empty memory-search query must return 400."""
    from aiohttp.test_utils import TestClient, TestServer

    from aphrodite.api.server import APIHandler, create_api_application

    config = Config(data_directory=str(tmp_path / "data"))
    config.characters_dir.mkdir(parents=True, exist_ok=True)
    handler = APIHandler(config)
    await handler.initialize("mira")
    client = TestClient(TestServer(create_api_application(handler)))
    await client.start_server()
    try:
        response = await client.get(
            "/v1/memory/search?q=",
            headers={"Authorization": f"Bearer {handler.token}"},
        )
        assert response.status == 400
    finally:
        await client.close()
        await handler.handle_close()


@pytest.mark.asyncio
async def test_simulate_rejects_absurd_speed(tmp_path):
    """D3: simulation speed must have an upper bound."""
    from aiohttp.test_utils import TestClient, TestServer

    from aphrodite.api.server import APIHandler, create_api_application

    config = Config(data_directory=str(tmp_path / "data"))
    config.characters_dir.mkdir(parents=True, exist_ok=True)
    handler = APIHandler(config)
    await handler.initialize("mira")
    client = TestClient(TestServer(create_api_application(handler)))
    await client.start_server()
    try:
        response = await client.post(
            "/v1/simulate",
            json={"hours": 1, "speed": 10**12},
            headers={"Authorization": f"Bearer {handler.token}"},
        )
        assert response.status == 400
    finally:
        await client.close()
        await handler.handle_close()


@pytest.mark.asyncio
async def test_simulation_run_does_not_touch_caller_database(tmp_path):
    """HIGH: a simulation must never write simulated state into the caller's DB."""
    from aphrodite.db import Database
    from aphrodite.simulation import SimulationEngine

    config = Config(data_directory=str(tmp_path / "data"))
    config.characters_dir.mkdir(parents=True, exist_ok=True)
    db = Database(config.db_path)
    await db.initialize()
    try:
        before = await db.fetch_one("SELECT * FROM world_state WHERE id = 1")
        report = await SimulationEngine(db, config).run(hours=24, speed=1000)
        after_state = await db.fetch_one("SELECT * FROM world_state WHERE id = 1")
        events = await db.fetch_all("SELECT COUNT(*) as c FROM events")
        journal = await db.fetch_all("SELECT COUNT(*) as c FROM journal_entries")
    finally:
        await db.close()

    assert report.duration_hours == 24
    assert after_state["last_processed_utc"] == before["last_processed_utc"]
    assert after_state["revision"] == before["revision"]
    assert events[0]["c"] == 0
    assert journal[0]["c"] == 0


@pytest.mark.asyncio
async def test_long_gap_test_is_isolated_from_caller_database(tmp_path):
    """run_long_gap_test must not advance the caller DB's clock."""
    from aphrodite.db import Database
    from aphrodite.simulation import SimulationEngine

    config = Config(data_directory=str(tmp_path / "data"))
    config.characters_dir.mkdir(parents=True, exist_ok=True)
    db = Database(config.db_path)
    await db.initialize()
    try:
        before = await db.fetch_one("SELECT last_processed_utc FROM world_state WHERE id = 1")
        result = await SimulationEngine(db, config).run_long_gap_test(hours=8760)
        after = await db.fetch_one("SELECT last_processed_utc FROM world_state WHERE id = 1")
    finally:
        await db.close()

    assert result["coherent"] is True
    assert after["last_processed_utc"] == before["last_processed_utc"]


@pytest.mark.asyncio
async def test_world_catchup_is_capped_by_config(tmp_path):
    """MEDIUM: a long offline gap must not decay mood by the full elapsed time."""
    from datetime import datetime

    from aphrodite.types import MoodState
    from aphrodite.world import WorldEngine

    config = Config(data_directory=str(tmp_path / "data"))
    config.world.max_catchup_hours = 2
    config.characters_dir.mkdir(parents=True, exist_ok=True)
    db = Database(config.db_path)
    await db.initialize()
    engine = WorldEngine(db, config)
    # Bootstrap the clock, then advance by 30 days in one update.
    await engine.update_state(datetime(2026, 7, 1, 0, 0, tzinfo=UTC))
    await db.update_world_state(mood_json=json.dumps(MoodState(valence=1.0, arousal=1.0).to_dict()))
    await engine.update_state(datetime(2026, 7, 31, 0, 0, tzinfo=UTC))
    row = await db.fetch_one("SELECT mood_json FROM world_state WHERE id = 1")
    await db.close()

    mood = json.loads(row["mood_json"])
    # With a 2h cap, valence can only decay a little from 1.0 (2h * rate 0.08).
    assert mood["valence"] > 0.5, f"mood snapped to baseline: {mood['valence']}"


def test_negative_baseline_valence_is_accepted(tmp_path):
    """MEDIUM: valence domain is [-1, 1], so negative baselines must load."""
    from aphrodite.config import load_config

    cfg = tmp_path / "aphrodite.toml"
    cfg.write_text("[mood]\nbaseline_valence = -0.3\n", encoding="utf-8")
    config = load_config(cfg)
    assert config.mood.baseline_valence == -0.3


# ---------------------------------------------------------------------------
# Batch 3: archive member edge cases, LIKE escaping, provider retries,
# atomic corrections, journal local dates, migration checks, self-test.
# ---------------------------------------------------------------------------


def test_archive_dot_member_handled_without_crash(tmp_path):
    """A '.' directory member must not crash the validator (IndexError)."""
    import tarfile

    from aphrodite.export import _safe_extract_tar

    archive = tmp_path / "dot.aphrocard"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(".")
        info.type = tarfile.DIRTYPE
        tar.addfile(info)

    with tarfile.open(archive, "r:gz") as tar:
        # Accepts it harmlessly (root dir, no-op) or rejects cleanly: either is
        # fine as long as it is not an unhandled IndexError.
        try:
            _safe_extract_tar(tar, tmp_path / "extract")
        except ValueError as exc:
            assert "unsafe archive member" in str(exc)


def test_export_skips_symlinked_markdown(tmp_path):
    """Export must not follow symlinks inside a character directory."""
    config = Config(data_directory=str(tmp_path / "data"))
    config.characters_dir.mkdir(parents=True)
    char_dir = config.characters_dir / "mira"
    char_dir.mkdir()
    (char_dir / "identity.md").write_text(
        "---\nname: Mira\n---\n\n# Identity\nA test character.\n", encoding="utf-8"
    )
    outside = tmp_path / "outside.md"
    outside.write_text("secret outside content", encoding="utf-8")
    (char_dir / "smuggled.md").symlink_to(outside)

    async def _run():
        card = str(tmp_path / "mira.aphrocard")
        await ExportManager(config).export_character("mira", card)
        with tarfile.open(card, "r:gz") as tar:
            return tar.getnames()

    names = asyncio_run(_run())
    assert any(n.endswith("identity.md") for n in names)
    assert not any(n.endswith("smuggled.md") for n in names)


@pytest.mark.asyncio
async def test_memory_search_escapes_like_wildcards(tmp_path):
    """User % and _ must match literally, not act as SQL wildcards."""
    from aphrodite.memory import MemoryManager

    config = Config(data_directory=str(tmp_path / "data"))
    config.characters_dir.mkdir(parents=True, exist_ok=True)
    db = Database(config.db_path)
    await db.initialize()
    mgr = MemoryManager(db, config)
    await mgr.add_memory("Progress is 100% complete", memory_type="fact")
    await mgr.add_memory("Plain text memory", memory_type="fact")

    percent_hits = await mgr.search_long_term("%")
    underscore_hits = await mgr.search_long_term("_")
    plain_hits = await mgr.search_long_term("plain")
    await db.close()

    # Bare % only matches memories containing a literal percent sign.
    assert [m.content for m in percent_hits] == ["Progress is 100% complete"]
    # Bare _ matches nothing (no literal underscores stored).
    assert len(underscore_hits) == 0
    # Plain queries still work.
    assert len(plain_hits) == 1


@pytest.mark.asyncio
async def test_provider_retries_transient_503_then_succeeds():
    """A retryable 503 must be retried and eventually succeed."""
    from aphrodite.config import ProviderInstanceConfig
    from aphrodite.providers import Provider

    class _Resp:
        def __init__(self, status, text, payload=None):
            self.status_code = status
            self._text = text
            self._payload = payload or {}

        def raise_for_status(self):
            import httpx

            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"status {self.status_code}", request=None, response=self
                )

        def json(self):
            return self._payload

    class _FakeClient:
        def __init__(self):
            self.is_closed = False
            self.calls = 0

        async def post(self, url, json=None):
            self.calls += 1
            if self.calls < 3:
                return _Resp(503, "unavailable")
            return _Resp(
                200,
                "ok",
                {"choices": [{"message": {"content": "recovered <thinking>y</thinking>"}}]},
            )

        async def aclose(self):
            self.is_closed = True

    config = ProviderInstanceConfig(base_url="http://localhost:9/v1", model="test", retries=2)
    provider = Provider(config)
    fake = _FakeClient()
    provider._client = fake  # type: ignore[attr-defined]

    result = await provider.complete([{"role": "user", "content": "hi"}])
    assert result == "recovered"
    assert fake.calls == 3


@pytest.mark.asyncio
async def test_provider_gives_up_after_retries_exhausted():
    """A persistently failing provider must raise ProviderError, not hang."""
    from aphrodite.config import ProviderInstanceConfig
    from aphrodite.providers import Provider, ProviderError

    class _Resp:
        def __init__(self, status):
            self.status_code = status
            self._text = "nope"

        @property
        def text(self):
            return self._text

        def raise_for_status(self):
            import httpx

            raise httpx.HTTPStatusError(f"status {self.status_code}", request=None, response=self)

    class _FakeClient:
        def __init__(self):
            self.is_closed = False
            self.calls = 0

        async def post(self, url, json=None):
            self.calls += 1
            return _Resp(503)

        async def aclose(self):
            self.is_closed = True

    config = ProviderInstanceConfig(base_url="http://localhost:9/v1", model="test", retries=1)
    provider = Provider(config)
    fake = _FakeClient()
    provider._client = fake  # type: ignore[attr-defined]

    with pytest.raises(ProviderError, match="Provider error 503"):
        await provider.complete([{"role": "user", "content": "hi"}])
    assert fake.calls == 2  # initial attempt + 1 retry


@pytest.mark.asyncio
async def test_correct_memory_is_atomic_and_keeps_one_active(tmp_path):
    """correct_memory must supersede the old memory and insert the new one."""
    from aphrodite.memory import MemoryManager

    config = Config(data_directory=str(tmp_path / "data"))
    config.characters_dir.mkdir(parents=True, exist_ok=True)
    db = Database(config.db_path)
    await db.initialize()
    mgr = MemoryManager(db, config)
    original = await mgr.add_memory("User lives in London", memory_type="fact")

    await mgr.correct_memory(original.id, "User lives in Paris")

    rows = await db.fetch_all("SELECT status, content FROM memories ORDER BY created_at_utc")
    await db.close()

    statuses = [r["status"] for r in rows]
    assert statuses.count("active") == 1
    assert statuses.count("superseded") == 1
    active = next(r for r in rows if r["status"] == "active")
    assert active["content"] == "User lives in Paris"


@pytest.mark.asyncio
async def test_journal_entry_uses_local_date_not_utc(tmp_path):
    """A journal entry written near UTC midnight must land on the local day."""
    from aphrodite.character import Character, CharacterIdentity
    from aphrodite.journal import JournalManager
    from aphrodite.types import MoodState

    config = Config(data_directory=str(tmp_path / "data"))
    config.timezone = "Pacific/Auckland"
    config.characters_dir.mkdir(parents=True, exist_ok=True)
    db = Database(config.db_path)
    await db.initialize()
    manager = JournalManager(db, config)
    char = Character(id="mira", identity=CharacterIdentity(name="Mira"))
    # 2026-07-31 23:30 UTC == 2026-08-01 11:30 NZST
    entry = await manager.write_entry(
        char, MoodState(), now_utc=datetime(2026, 7, 31, 23, 30, tzinfo=UTC)
    )
    await db.close()

    assert entry.local_date == "2026-08-01"


@pytest.mark.asyncio
async def test_newer_database_schema_is_rejected(tmp_path):
    """Opening a DB from a newer build must fail fast, not corrupt data."""
    config = Config(data_directory=str(tmp_path / "data"))
    config.characters_dir.mkdir(parents=True, exist_ok=True)
    db = Database(config.db_path)
    await db.initialize()
    await db.execute("UPDATE schema_metadata SET value = '99' WHERE key = 'schema_version'")
    await db.commit()
    await db.close()

    db2 = Database(config.db_path)
    with pytest.raises(RuntimeError, match="newer than this build"):
        await db2.initialize()


def test_selftest_exits_zero_on_healthy_system(tmp_path):
    """aphrodite selftest must exit 0 when all built-in checks pass."""
    from click.testing import CliRunner

    from aphrodite_cli.main import cli

    cfg = tmp_path / "aphrodite.toml"
    cfg.write_text(
        f'[general]\ndata_directory = "{(tmp_path / "data").as_posix()}"\n', encoding="utf-8"
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["--config", str(cfg), "selftest"])
    assert result.exit_code == 0, result.output
    assert "Determinism" in result.output


# ---------------------------------------------------------------------------
# Batch 4: final-audit reconciliation fixes.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_rejects_non_loopback_host_header(tmp_path):
    """DNS-rebinding guard: non-loopback Host headers get 403 on loopback binds."""
    from aiohttp.test_utils import TestClient, TestServer

    from aphrodite.api.server import APIHandler, create_api_application

    config = Config(data_directory=str(tmp_path / "data"))
    config.characters_dir.mkdir(parents=True, exist_ok=True)
    handler = APIHandler(config)
    client = TestClient(TestServer(create_api_application(handler)))
    await client.start_server()
    try:
        resp = await client.get("/", headers={"Host": "evil.example"})
        assert resp.status == 403
        resp = await client.get("/", headers={"Host": "127.0.0.1:9999"})
        assert resp.status == 200
        resp = await client.get("/", headers={"Host": "localhost"})
        assert resp.status == 200
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_api_security_headers_present(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    from aphrodite.api.server import APIHandler, create_api_application

    config = Config(data_directory=str(tmp_path / "data"))
    handler = APIHandler(config)
    client = TestClient(TestServer(create_api_application(handler)))
    await client.start_server()
    try:
        resp = await client.get("/health")
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert "frame-ancestors 'none'" in resp.headers.get("Content-Security-Policy", "")
        assert resp.headers.get("Server") == "aphrodite"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_api_proactive_endpoint(tmp_path, monkeypatch):
    """The proactive subsystem is wired: /v1/proactive returns a message when due."""
    import datetime as _datetime

    from aiohttp.test_utils import TestClient, TestServer

    import aphrodite.proactive as proactive_mod
    from aphrodite.api.server import APIHandler, create_api_application

    # Pin "now" to a waking hour so the test is not time-of-day dependent.
    real_datetime = proactive_mod.datetime

    class _FakeDatetime:
        @staticmethod
        def now(tz=None):
            return _datetime.datetime(2026, 8, 1, 10, 0, tzinfo=_datetime.UTC)

    monkeypatch.setattr(proactive_mod, "datetime", _FakeDatetime)

    config = Config(data_directory=str(tmp_path / "data"))
    config.proactive.enabled = True
    config.timezone = "UTC"
    config.characters_dir.mkdir(parents=True, exist_ok=True)
    handler = APIHandler(config)
    await handler.initialize("mira")
    client = TestClient(TestServer(create_api_application(handler)))
    await client.start_server()
    try:
        resp = await client.get(
            "/v1/proactive", headers={"Authorization": f"Bearer {handler.token}"}
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["message"] is not None
        assert body["message"]["content"]
    finally:
        await client.close()
        await handler.handle_close()
        monkeypatch.setattr(proactive_mod, "datetime", real_datetime)


@pytest.mark.asyncio
async def test_simulation_rejects_nan_and_inf(tmp_path):
    from aphrodite.db import Database
    from aphrodite.simulation import SimulationEngine

    config = Config(data_directory=str(tmp_path / "data"))
    config.characters_dir.mkdir(parents=True, exist_ok=True)
    db = Database(config.db_path)
    await db.initialize()
    engine = SimulationEngine(db, config)
    try:
        with pytest.raises(ValueError, match="finite"):
            await engine.run(hours=float("nan"))
        with pytest.raises(ValueError, match="finite"):
            await engine.run(hours=1, speed=float("inf"))
    finally:
        await db.close()


def test_simulation_script_from_jsonl_reports_line_numbers(tmp_path):
    from aphrodite.simulation import SimulationScript

    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"type": "advance_time", "hours": 1}\nnot json\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        SimulationScript.from_jsonl(str(bad))


def test_simulated_clock_speed_floor_and_frozen_advance_hours():
    from aphrodite.simulation import SimulatedClock

    clock = SimulatedClock(speed=-5)
    assert clock.speed == 0.1

    clock.freeze()
    before = clock.now_utc()
    clock.advance_hours(24)
    assert clock.now_utc() == before


@pytest.mark.asyncio
async def test_extraction_skips_only_malformed_line(tmp_path):
    from aphrodite.extraction import MemoryExtractor

    class _LLM:
        async def complete(self, messages, **kwargs):
            return "preference|Likes tea|0.9|normal|0.7\nfact|Works at hospital|high|normal|0.5"

    memories = await MemoryExtractor(_LLM()).extract("I like tea", "Cool")
    assert len(memories) == 1
    assert memories[0].content == "Likes tea"
    assert memories[0].memory_type.value == "preference"


@pytest.mark.asyncio
async def test_keyword_fallback_rejects_fragments(tmp_path):
    from aphrodite.extraction import MemoryExtractor

    # "i am." is too short to be a useful memory; nothing should be stored.
    memories = await MemoryExtractor(None).extract("I am.", "ok")
    assert memories == []


def test_config_rejects_equal_quiet_hours(tmp_path):
    from aphrodite.config import load_config

    cfg = tmp_path / "aphrodite.toml"
    cfg.write_text(
        '[proactive]\nquiet_hours_start = "22:00"\nquiet_hours_end = "22:00"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="quiet_hours_start must differ"):
        load_config(cfg)


@pytest.mark.asyncio
async def test_save_event_uses_real_creation_time(tmp_path):
    config = Config(data_directory=str(tmp_path / "data"))
    config.characters_dir.mkdir(parents=True, exist_ok=True)
    db = Database(config.db_path)
    await db.initialize()
    try:
        await db.save_event(
            event_id="evt-1",
            event_type="test",
            layer=1,
            provenance="system",
            status="completed",
            title="t",
            summary="s",
            starts_at="2026-01-01T00:00:00+00:00",
            local_date="2026-01-01",
        )
        row = await db.fetch_one(
            "SELECT created_at_utc, starts_at_utc FROM events WHERE id = 'evt-1'"
        )
        assert row["created_at_utc"] != "2026-01-01T00:00:00+00:00"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_database_initialize_is_reentrant(tmp_path):
    config = Config(data_directory=str(tmp_path / "data"))
    config.characters_dir.mkdir(parents=True, exist_ok=True)
    db = Database(config.db_path)
    await db.initialize()
    await db.initialize()  # must not leak the prior connection
    await db.execute("SELECT 1")
    await db.close()
    assert db._db is None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Batch 5: final-audit coverage gaps (tar caps, busy retry, export_journal,
# server startup paths).
# ---------------------------------------------------------------------------


def _tar_bytes(members):
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members:
            if data is None:
                info = tarfile.TarInfo(name)
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_archive_member_count_cap(tmp_path):
    import io
    import tarfile

    from aphrodite.export import _safe_extract_tar

    members = [(f"m{i}.md", b"x" * 10) for i in range(300)]
    buf = _tar_bytes(members)
    with (
        tarfile.open(fileobj=io.BytesIO(buf), mode="r:gz") as tar,
        pytest.raises(ValueError, match="too many members"),
    ):
        _safe_extract_tar(tar, tmp_path)


def test_archive_member_size_cap(tmp_path):
    import io
    import tarfile

    from aphrodite.export import _safe_extract_tar

    buf = _tar_bytes([("big.bin", b"y" * (11 * 1024 * 1024))])
    with (
        tarfile.open(fileobj=io.BytesIO(buf), mode="r:gz") as tar,
        pytest.raises(ValueError, match="member size"),
    ):
        _safe_extract_tar(tar, tmp_path)


def test_archive_total_size_cap(tmp_path):
    import io
    import tarfile

    from aphrodite.export import _safe_extract_tar

    members = [(f"f{i}.bin", b"z" * (6 * 1024 * 1024)) for i in range(12)]
    buf = _tar_bytes(members)
    with (
        tarfile.open(fileobj=io.BytesIO(buf), mode="r:gz") as tar,
        pytest.raises(ValueError, match="size limit"),
    ):
        _safe_extract_tar(tar, tmp_path)


@pytest.mark.asyncio
async def test_world_state_tolerates_corrupt_json(tmp_path):
    """Corrupt mood_json/weather_json must not break get_state (500s)."""
    from aphrodite.world import WorldEngine

    config = Config(data_directory=str(tmp_path / "data"))
    config.characters_dir.mkdir(parents=True, exist_ok=True)
    db = Database(config.db_path)
    await db.initialize()
    await db.execute("UPDATE world_state SET mood_json = '{not json', weather_json = 'oops'")
    await db.commit()
    engine = WorldEngine(db, config)
    state = await engine.get_state()
    assert state.mood.valence == 0.15  # defaults
    await db.close()


@pytest.mark.asyncio
async def test_character_garbage_age_does_not_brick_app(tmp_path):
    from aphrodite.character import parse_character

    d = tmp_path / "char"
    d.mkdir()
    (d / "identity.md").write_text("---\nage: twenty\n---\n", encoding="utf-8")
    char = parse_character(d)
    assert char.identity.age == 24


@pytest.mark.asyncio
async def test_extraction_failure_does_not_lose_exchange(tmp_path):
    """Extraction failures must not roll back a completed exchange."""
    from aphrodite.app import AphroditeApp

    class _GoodProvider:
        async def complete(self, messages, **kwargs):
            return "hi there"

        async def close(self):
            pass

    class _BrokenExtractor:
        async def extract(self, *a, **kw):
            raise RuntimeError("extractor exploded")

    config = Config(data_directory=str(tmp_path / "data"))
    app = AphroditeApp(config)
    await app.initialize()
    try:
        app.provider = _GoodProvider()  # type: ignore[assignment]
        app._extractor = _BrokenExtractor()  # type: ignore[assignment]
        response = await app.chat("hello")
        assert response == "hi there"
        rows = await app.db.fetch_all("SELECT role FROM messages")
        assert [r["role"] for r in rows] == ["user", "assistant"]
    finally:
        await app.close()


def test_cli_import_char_existing_character_is_clean(tmp_path):
    from click.testing import CliRunner

    from aphrodite_cli.main import cli

    data = tmp_path / "data"
    cfg = tmp_path / "aphrodite.toml"
    cfg.write_text(f'[general]\ndata_directory = "{data.as_posix()}"\n', encoding="utf-8")

    runner = CliRunner()
    runner.invoke(
        cli, ["--config", str(cfg), "create", "--character", "mira"], input="24\nshe/her\n"
    )
    card = tmp_path / "mira.aphrocard"

    async def _export():
        from aphrodite.config import Config
        from aphrodite.export import ExportManager

        await ExportManager(Config(data_directory=str(data))).export_character("mira", str(card))

    asyncio_run(_export())
    result = runner.invoke(cli, ["--config", str(cfg), "import-char", str(card)])
    assert result.exit_code == 1
    assert "already exists" in result.output
    assert "Traceback" not in result.output


@pytest.mark.asyncio
async def test_remote_bind_fails_closed_without_explicit_token(tmp_path, monkeypatch):
    """allow_remote without APHRODITE_API_TOKEN must refuse to start."""
    monkeypatch.delenv("APHRODITE_API_TOKEN", raising=False)
    config = Config(data_directory=str(tmp_path / "data"))
    config.api.allow_remote = True
    from aphrodite.api.server import run_api_server

    with pytest.raises(RuntimeError, match="APHRODITE_API_TOKEN"):
        await run_api_server(config, host="0.0.0.0", port=0)


@pytest.mark.asyncio
async def test_remote_bind_with_explicit_token_starts(tmp_path, monkeypatch):
    monkeypatch.setenv("APHRODITE_API_TOKEN", "explicit-secret")
    config = Config(data_directory=str(tmp_path / "data"))
    config.api.allow_remote = True
    config.characters_dir.mkdir(parents=True, exist_ok=True)
    import asyncio

    from aphrodite.api.server import run_api_server

    task = asyncio.create_task(run_api_server(config, host="0.0.0.0", port=0))
    await asyncio.sleep(0.5)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, OSError):
        pass
