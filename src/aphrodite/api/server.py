"""REST API server for Aphrodite Agent."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import math
import os
import secrets
from datetime import timedelta
from pathlib import Path
from typing import Any

from ..app import AphroditeApp
from ..config import Config
from ..journal import JournalManager
from ..providers import ProviderError
from ..simulation import SimulationEngine

STATIC_DIR = Path(__file__).parent / "static"
logger = logging.getLogger("aphrodite.api")
MAX_CHAT_CHARACTERS = 12_000
MAX_MEMORY_QUERY_CHARACTERS = 500
MAX_API_ADVANCE_HOURS = 8_760
MAX_API_SIM_HOURS = 720
MAX_API_SIM_SPEED = 100_000.0
APIResult = dict[str, Any] | tuple[dict[str, Any], int]

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def _validate_bind_host(host: str, allow_remote: bool) -> None:
    """Reject non-loopback API binds unless the user explicitly opted in."""
    if allow_remote:
        return
    if host.lower() == "localhost":
        return
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        raise ValueError(
            "Refusing non-loopback API bind; set api.allow_remote=true or use --allow-remote"
        )


def _is_valid_positive_hours(value: Any) -> bool:
    """Return True only for positive, finite, timedelta-representable hours."""
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    if value <= 0:
        return False
    try:
        timedelta(hours=value)
    except (OverflowError, ValueError):
        return False
    return True


class APIHandler:
    """Handles API requests."""

    def __init__(self, config: Config):
        self.config = config
        self.app: AphroditeApp | None = None
        self.journal: JournalManager | None = None
        self._token = os.environ.get("APHRODITE_API_TOKEN", "").strip() or secrets.token_urlsafe(32)
        # Bound concurrent expensive work (LLM calls) so a local process or a
        # misbehaving client cannot stack unbounded provider load.
        self._chat_semaphore = asyncio.Semaphore(4)
        self._simulate_semaphore = asyncio.Semaphore(2)
        self._proactive: Any = None

    @property
    def token(self) -> str:
        """Return the ephemeral bearer token for the bundled same-origin UI."""
        return self._token

    async def initialize(self, character_name: str | None = None) -> None:
        self.app = AphroditeApp(self.config)
        await self.app.initialize(character_name)
        self.journal = JournalManager(
            self.app.db,
            self.config,
            provider=self.app.provider,
        )

    async def handle_chat(self, body: dict) -> APIResult:
        if not isinstance(body, dict):
            return {"error": "JSON body must be an object"}, 400
        message = body.get("message", "")
        if not isinstance(message, str):
            return {"error": "message must be a string"}, 400
        if not message.strip():
            return {"error": "Message is required"}, 400
        if len(message) > MAX_CHAT_CHARACTERS:
            return {"error": f"message exceeds {MAX_CHAT_CHARACTERS} characters"}, 413
        if not self.app:
            return {"error": "Not initialized"}, 503
        try:
            async with self._chat_semaphore:
                response = await self.app.chat(message)
        except ValueError as exc:
            return {"error": str(exc)}, 400
        except ProviderError as exc:
            # Keep provider details server-side; clients get a generic message.
            logger.warning("Chat provider failure: %s", exc)
            return {"error": "Provider unavailable"}, 502
        except Exception:
            logger.exception("Chat handler error")
            return {"error": "Internal server error"}, 500
        return {"response": response}

    async def handle_health(self) -> dict:
        # Liveness is the primary signal; provider reachability is included
        # when the app is up (bounded so a slow provider cannot stall /health).
        provider_healthy: bool | None = None
        if self.app and self.app.provider:
            try:
                async with asyncio.timeout(2.0):
                    provider_healthy = await self.app.provider.health_check()
            except Exception:  # noqa: BLE001 - provider boundary: any failure = unhealthy
                provider_healthy = False
        return {
            "status": "healthy",
            "version": "0.1.0",
            "provider": self.config.provider_active,
            "model": self.config.active_provider.model,
            "character": self.app.character.name if self.app and self.app.character else "none",
            "provider_healthy": provider_healthy,
        }

    async def handle_world_state(self) -> APIResult:
        if not self.app or not self.app.world:
            return {"error": "Not initialized"}, 503
        state = await self.app.world.get_state()
        return {
            "character": self.app.character.name if self.app.character else "",
            "activity": state.activity,
            "mood": state.mood.label(),
            "weather": f"{state.weather.condition}, {state.weather.temperature_c}°C",
        }

    async def handle_world_advance(self, body: dict) -> APIResult:
        if not isinstance(body, dict):
            return {"error": "JSON body must be an object"}, 400
        hours = body.get("hours", 1)
        if not _is_valid_positive_hours(hours):
            return {"error": "hours must be a positive finite number"}, 400
        if hours > MAX_API_ADVANCE_HOURS:
            return {"error": f"hours must not exceed {MAX_API_ADVANCE_HOURS}"}, 400
        if not self.app or not self.app.world:
            return {"error": "Not initialized"}, 503
        try:
            events = await self.app.world.advance_time(hours)
        except ValueError as exc:
            return {"error": str(exc)}, 400
        except Exception:
            logger.exception("World advance error")
            return {"error": "Internal server error"}, 500
        return {"hours_advanced": hours, "events_generated": len(events)}

    async def handle_journal_latest(self) -> dict:
        entry = await self.journal.get_latest() if self.journal else None
        if entry:
            return {
                "date": entry.local_date,
                "summary": entry.summary_text,
                "body": entry.body_text,
            }
        return {"message": "No journal entries found"}

    async def handle_memory_search(self, query: str) -> APIResult:
        if not query.strip():
            return {"error": "query is required"}, 400
        if len(query) > MAX_MEMORY_QUERY_CHARACTERS:
            return {"error": f"query exceeds {MAX_MEMORY_QUERY_CHARACTERS} characters"}, 400
        if not self.app or not self.app.memory:
            return {"error": "Not initialized"}, 503
        results = await self.app.memory.search_long_term(query)
        return {
            "query": query,
            "results": [
                {
                    "id": m.id,
                    "content": m.content,
                    "memory_type": m.memory_type.value,
                    "confidence": m.confidence,
                }
                for m in results
            ],
            "count": len(results),
        }

    async def handle_simulate(self, body: dict) -> APIResult:
        if not isinstance(body, dict):
            return {"error": "JSON body must be an object"}, 400
        hours = body.get("hours", 24)
        if not _is_valid_positive_hours(hours):
            return {"error": "hours must be a positive finite number"}, 400
        if hours > MAX_API_SIM_HOURS:
            return {"error": f"hours must not exceed {MAX_API_SIM_HOURS}"}, 400
        speed = body.get("speed", 100)
        if not _is_valid_positive_hours(speed):
            return {"error": "speed must be a positive finite number"}, 400
        if speed > MAX_API_SIM_SPEED:
            return {"error": f"speed must not exceed {MAX_API_SIM_SPEED:g}"}, 400
        if not self.app:
            return {"error": "Not initialized"}, 503
        try:
            async with self._simulate_semaphore:
                engine = SimulationEngine(self.app.db, self.config)
                report = await engine.run(
                    hours=hours,
                    character=body.get(
                        "character",
                        self.app.character.name if self.app and self.app.character else "default",
                    ),
                    speed=speed,
                    mock_provider=body.get("mock_provider", True),
                )
        except ValueError as exc:
            return {"error": str(exc)}, 400
        except Exception:
            logger.exception("Simulate error")
            return {"error": "Internal server error"}, 500
        return {
            "simulation_id": report.simulation_id,
            "duration_hours": report.duration_hours,
            "real_time_ms": int(report.real_time_seconds * 1000),
            "total_events": report.total_events,
            "total_journal_entries": report.total_journal_entries,
            "total_messages": report.total_messages,
            "errors": report.errors,
        }

    async def handle_characters(self) -> dict:
        chars_dir = self.config.characters_dir
        chars = []
        if chars_dir.exists():
            for d in sorted(chars_dir.iterdir()):
                if d.is_dir():
                    files = list(d.glob("*.md"))
                    chars.append({"id": d.name, "files": len(files)})
        return {"characters": chars}

    async def handle_proactive(self) -> APIResult:
        """Return a pending proactive message, if one is due (delivery handoff)."""
        if not self.app or not self.app.character or not self.app.world:
            return {"error": "Not initialized"}, 503
        if self._proactive is None:
            from ..proactive import ProactiveManager

            self._proactive = ProactiveManager(self.app.db, self.config)
        state = await self.app.world.get_state()
        message = await self._proactive.think_about_messaging(self.app.character, state, state.mood)
        if message is None:
            return {"message": None}
        # The message is handed to the caller (UI/scheduler); only then mark it
        # as delivered so quotas count real sends.
        await self._proactive.mark_sent(message.id)
        return {
            "message": {
                "id": message.id,
                "type": message.message_type,
                "content": message.content,
            }
        }

    async def handle_close(self) -> dict:
        if self.app:
            await self.app.close()
        return {"status": "closed"}


def create_api_application(api_handler: APIHandler):
    """Create a testable aiohttp application around an API handler."""
    try:
        from aiohttp import web
    except ImportError as exc:
        raise RuntimeError("aiohttp is required for the API server") from exc

    @web.middleware
    async def require_auth(request, handler):
        if request.path.startswith("/v1/"):
            header = request.headers.get("Authorization", "")
            prefix = "Bearer "
            supplied = header[len(prefix) :] if header.startswith(prefix) else ""
            if not supplied or not secrets.compare_digest(supplied, api_handler.token):
                return web.json_response(
                    {"error": "Unauthorized"},
                    status=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
        return await handler(request)

    @web.middleware
    async def validate_host(request, handler):
        """Reject Host headers that are not loopback when bound to loopback.

        This closes the DNS-rebinding hole (a malicious page whose hostname
        resolves to 127.0.0.1 cannot read the token from / or call /v1/*).
        """
        if not api_handler.config.api.allow_remote:
            host_header = request.headers.get("Host", "")
            host_name = host_header.split(":", 1)[0].strip().strip("[]").lower()
            if host_name and host_name not in _LOOPBACK_HOSTS:
                return web.json_response({"error": "Forbidden"}, status=403)
        return await handler(request)

    @web.middleware
    async def security_headers(request, handler):
        resp = await handler(request)
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
        resp.headers.setdefault("Server", "aphrodite")
        return resp

    async def serve_index(request):
        index_path = STATIC_DIR / "index.html"
        if not index_path.exists():
            return web.Response(text="Web UI not found", status=404)
        content = index_path.read_text(encoding="utf-8").replace(
            "__APHRODITE_API_TOKEN__", json.dumps(api_handler.token)
        )
        return web.Response(text=content, content_type="text/html")

    async def handle_post(request):
        try:
            body = await request.json() if request.can_read_body else {}
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON in request body"}, status=400)
        except Exception:  # noqa: BLE001 - body parsing boundary: any parse failure -> 400
            return web.json_response({"error": "Could not parse request body"}, status=400)

        try:
            if request.path == "/v1/chat":
                result = await api_handler.handle_chat(body)
            elif request.path == "/v1/world/advance":
                result = await api_handler.handle_world_advance(body)
            elif request.path == "/v1/simulate":
                result = await api_handler.handle_simulate(body)
            else:
                result = {"error": "Not found"}, 404
        except Exception:
            logger.exception("Unhandled POST error")
            result = {"error": "Internal server error"}, 500

        if isinstance(result, tuple):
            return web.json_response(result[0], status=result[1])
        return web.json_response(result)

    async def handle_get(request):
        try:
            if request.path == "/health":
                result = await api_handler.handle_health()
            elif request.path == "/v1/world/state":
                result = await api_handler.handle_world_state()
            elif request.path == "/v1/journal/latest":
                result = await api_handler.handle_journal_latest()
            elif request.path == "/v1/characters":
                result = await api_handler.handle_characters()
            elif request.path == "/v1/memory/search":
                result = await api_handler.handle_memory_search(request.query.get("q", ""))
            elif request.path == "/v1/proactive":
                result = await api_handler.handle_proactive()
            else:
                result = {"error": "Not found"}, 404
        except Exception:
            logger.exception("Unhandled GET error")
            result = {"error": "Internal server error"}, 500

        if isinstance(result, tuple):
            return web.json_response(result[0], status=result[1])
        return web.json_response(result)

    async def reject_options(request):
        return web.json_response({"error": "Cross-origin requests are not allowed"}, status=403)

    app = web.Application(middlewares=[require_auth], client_max_size=64 * 1024)
    app.router.add_get("/", serve_index)
    app.router.add_get("/health", handle_get)
    app.router.add_get("/v1/world/state", handle_get)
    app.router.add_get("/v1/journal/latest", handle_get)
    app.router.add_get("/v1/memory/search", handle_get)
    app.router.add_get("/v1/characters", handle_get)
    app.router.add_get("/v1/proactive", handle_get)
    app.router.add_post("/v1/chat", handle_post)
    app.router.add_post("/v1/world/advance", handle_post)
    app.router.add_post("/v1/simulate", handle_post)
    app.router.add_route("OPTIONS", "/{tail:.*}", reject_options)
    app.middlewares.append(validate_host)
    app.middlewares.append(security_headers)
    return app


async def run_api_server(
    config: Config, host: str = "127.0.0.1", port: int = 8765, character: str | None = None
):
    _validate_bind_host(host, config.api.allow_remote)
    from aphrodite import enable_utf8_stdio

    enable_utf8_stdio()
    # Remote binds fail closed: the auto-generated token would be readable by
    # anyone who can reach /, so require an explicitly configured secret.
    if config.api.allow_remote and not os.environ.get("APHRODITE_API_TOKEN", "").strip():
        raise RuntimeError(
            "Refusing to start with allow_remote=true: set APHRODITE_API_TOKEN "
            "explicitly (an auto-generated token would be disclosed to every "
            "LAN client via the bundled UI)."
        )
    handler = APIHandler(config)
    await handler.initialize(character)

    if handler.app is None:
        raise RuntimeError("API application failed to initialize")
    char_name = handler.app.character.name if handler.app.character else "default"
    print("🌐 Aphrodite Agent API")
    print(f"   URL:   http://{host}:{port}")
    print(f"   Chat:  http://{host}:{port}/")
    print(f"   Character: {char_name}")
    print(f"   Provider: {config.provider_active} ({config.active_provider.model})")
    print(f"   Server file: {Path(__file__).resolve()}")
    print(f"   PID: {os.getpid()}")
    if config.api.allow_remote:
        print(
            "⚠ WARNING: binding beyond loopback. The web UI serves the API token "
            "inline at /, so anyone who can reach this server can read it. "
            "Set APHRODITE_API_TOKEN to pin a known token, or keep loopback binding."
        )

    from aiohttp import web

    app = create_api_application(handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    try:
        while True:
            await asyncio.sleep(86400)
    finally:
        await runner.cleanup()
        await handler.handle_close()
