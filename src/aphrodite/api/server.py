"""REST API server for Aphrodite Agent."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import Config
from ..app import AphroditeApp
from ..journal import JournalManager
from ..simulation import SimulationEngine

STATIC_DIR = Path(__file__).parent / "static"
logger = logging.getLogger("aphrodite.api")


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
        self._token = secrets.token_urlsafe(32)

    async def initialize(self, character_name: str | None = None) -> None:
        self.app = AphroditeApp(self.config)
        await self.app.initialize(character_name)
        self.journal = JournalManager(
            self.app.db, self.config,
            provider=self.app.provider,
        )

    async def handle_chat(self, body: dict) -> dict:
        if not self.app:
            return {"error": "Not initialized"}, 503
        if not isinstance(body, dict):
            return {"error": "JSON body must be an object"}, 400
        message = body.get("message", "")
        if not isinstance(message, str):
            return {"error": "message must be a string"}, 400
        if not message.strip():
            return {"error": "Message is required"}, 400
        try:
            response = await self.app.chat(message)
        except Exception:
            logger.exception("Chat handler error")
            return {"error": "Internal server error"}, 500
        return {"response": response}

    async def handle_health(self) -> dict:
        return {
            "status": "healthy",
            "version": "0.1.0",
            "provider": self.config.provider_active,
            "model": self.config.active_provider.model,
            "character": self.app.character.name if self.app and self.app.character else "none",
        }

    async def handle_world_state(self) -> dict:
        if not self.app:
            return {"error": "Not initialized"}, 503
        state = await self.app.world.get_state()
        return {
            "character": self.app.character.name if self.app.character else "",
            "activity": state.activity,
            "mood": state.mood.label(),
            "weather": f"{state.weather.condition}, {state.weather.temperature_c}°C",
        }

    async def handle_world_advance(self, body: dict) -> dict:
        if not isinstance(body, dict):
            return {"error": "JSON body must be an object"}, 400
        hours = body.get("hours", 1)
        if not _is_valid_positive_hours(hours):
            return {"error": "hours must be a positive finite number"}, 400
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
            return {"date": entry.local_date, "summary": entry.summary_text, "body": entry.body_text}
        return {"message": "No journal entries found"}

    async def handle_memory_search(self, query: str) -> dict:
        results = await self.app.memory.search_long_term(query) if self.app else []
        return {
            "query": query,
            "results": [
                {"id": m.id, "content": m.content, "memory_type": m.memory_type.value, "confidence": m.confidence}
                for m in results
            ],
            "count": len(results),
        }

    async def handle_simulate(self, body: dict) -> dict:
        if not isinstance(body, dict):
            return {"error": "JSON body must be an object"}, 400
        hours = body.get("hours", 24)
        if not _is_valid_positive_hours(hours):
            return {"error": "hours must be a positive finite number"}, 400
        try:
            engine = SimulationEngine(self.app.db, self.config)
            report = await engine.run(
                hours=hours,
                character=body.get("character", self.app.character.name if self.app and self.app.character else "default"),
                speed=body.get("speed", 100),
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

    async def handle_close(self) -> dict:
        if self.app:
            await self.app.close()
        return {"status": "closed"}


def make_cors_headers(origin: str | None = None) -> dict:
    headers = {
        "Access-Control-Allow-Origin": origin or "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
        "Access-Control-Max-Age": "86400",
    }
    return headers


async def run_api_server(config: Config, host: str = "127.0.0.1",
                          port: int = 8765, character: str | None = None):
    handler = APIHandler(config)
    await handler.initialize(character)

    char_name = handler.app.character.name if handler.app.character else "default"
    print(f"🌐 Aphrodite Agent API")
    print(f"   URL:   http://{host}:{port}")
    print(f"   Chat:  http://{host}:{port}/")
    print(f"   Character: {char_name}")
    print(f"   Provider: {config.provider_active} ({config.active_provider.model})")
    print(f"   Server file: {Path(__file__).resolve()}")
    print(f"   PID: {os.getpid()}")

    try:
        from aiohttp import web
    except ImportError:
        print("aiohttp not installed. Install: pip install aiohttp")
        return

    async def serve_index(request):
        index_path = STATIC_DIR / "index.html"
        if index_path.exists():
            content = index_path.read_text(encoding="utf-8")
            return web.Response(text=content, content_type="text/html", headers=make_cors_headers())
        return web.Response(text="Web UI not found at " + str(index_path), status=404)

    async def handle_options(request):
        origin = request.headers.get("Origin", "*")
        return web.Response(headers=make_cors_headers(origin))

    async def handle_post(request):
        path = request.path
        origin = request.headers.get("Origin", "*")

        try:
            body = await request.json() if request.can_read_body else {}
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON in request body"}, status=400, headers=make_cors_headers(origin))
        except Exception:
            return web.json_response({"error": "Could not parse request body"}, status=400, headers=make_cors_headers(origin))

        try:
            if path == "/v1/chat":
                result = await handler.handle_chat(body)
            elif path == "/v1/world/advance":
                result = await handler.handle_world_advance(body)
            elif path == "/v1/simulate":
                result = await handler.handle_simulate(body)
            else:
                result = {"error": "Not found"}, 404
        except Exception as e:
            logger.exception("Unhandled POST error")
            result = {"error": "Internal server error"}, 500

        if isinstance(result, tuple):
            return web.json_response(result[0], status=result[1], headers=make_cors_headers(origin))
        return web.json_response(result, headers=make_cors_headers(origin))

    async def handle_get(request):
        path = request.path
        origin = request.headers.get("Origin", "*")

        try:
            if path == "/" or path == "":
                return await serve_index(request)
            elif path == "/health":
                result = await handler.handle_health()
            elif path == "/v1/world/state":
                result = await handler.handle_world_state()
            elif path == "/v1/journal/latest":
                result = await handler.handle_journal_latest()
            elif path == "/v1/characters":
                result = await handler.handle_characters()
            elif path.startswith("/v1/memory/search"):
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(str(request.url))
                q = parse_qs(parsed.query).get("q", [""])[0]
                result = await handler.handle_memory_search(q)
            else:
                result = {"error": "Not found"}, 404
        except Exception as e:
            logger.exception("Unhandled GET error")
            result = {"error": "Internal server error"}, 500

        if isinstance(result, tuple):
            return web.json_response(result[0], status=result[1], headers=make_cors_headers(origin))
        return web.json_response(result, headers=make_cors_headers(origin))

    app = web.Application()
    app.router.add_get("/", serve_index)
    app.router.add_get("/health", handle_get)
    app.router.add_get("/v1/world/state", handle_get)
    app.router.add_get("/v1/journal/latest", handle_get)
    app.router.add_get("/v1/memory/search", handle_get)
    app.router.add_get("/v1/characters", handle_get)
    app.router.add_post("/v1/chat", handle_post)
    app.router.add_post("/v1/world/advance", handle_post)
    app.router.add_post("/v1/simulate", handle_post)
    app.router.add_route("OPTIONS", "/{tail:.*}", handle_options)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    while True:
        await asyncio.sleep(86400)
