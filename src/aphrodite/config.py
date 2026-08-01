"""Configuration system for Aphrodite Agent."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass
class ProviderInstanceConfig:
    enabled: bool = True
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    temperature: float = 0.8
    top_p: float = 1.0
    max_output_tokens: int = 2048
    context_window_tokens: int = 0
    timeout_seconds: int = 120
    retries: int = 2
    supports_streaming: bool = True


@dataclass
class MemoryRetrievalConfig:
    top_k: int = 12
    inject_max_items: int = 8
    inject_max_tokens: int = 1800
    min_score: float = 0.25
    weight_relevance: float = 0.55
    weight_recency: float = 0.25
    weight_importance: float = 0.20


@dataclass
class ProactiveConfig:
    enabled: bool = False
    max_per_day: int = 4
    min_gap_minutes: int = 180
    quiet_hours_start: str = "22:00"
    quiet_hours_end: str = "08:00"
    allow_check_in: bool = True
    allow_share_from_life: bool = True
    allow_miss_you: bool = True
    allow_goodnight: bool = True


@dataclass
class WorldConfig:
    enabled: bool = True
    tick_interval_seconds: int = 60
    state_update_interval_minutes: int = 15
    journal_time: str = "21:30"
    journal_fallback_time: str = "07:30"
    max_events_per_day: int = 20
    max_catchup_hours: int = 12


@dataclass
class MoodConfig:
    baseline_valence: float = 0.15
    baseline_arousal: float = 0.40
    baseline_dominance: float = 0.50
    baseline_affection: float = 0.55
    baseline_trust: float = 0.50
    baseline_curiosity: float = 0.65
    max_delta_per_turn: float = 0.08
    decay_valence: float = 0.08
    decay_arousal: float = 0.15


@dataclass
class LoggingConfig:
    level: str = "INFO"
    format: str = "json"
    debug_mode: bool = False
    log_prompts: bool = False
    log_responses: bool = False
    audit_enabled: bool = True


@dataclass
class APIConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8765
    websocket_enabled: bool = True
    allow_remote: bool = False


@dataclass
class Config:
    """Main configuration for Aphrodite Agent."""

    schema_version: int = 1

    # General
    language: str = "en-US"
    timezone: str = "system"
    default_character: str = "default"

    # Provider
    provider_active: str = "primary"
    providers: dict[str, ProviderInstanceConfig] = field(
        default_factory=lambda: {"primary": ProviderInstanceConfig()}
    )

    # Context
    context_profile: str = "8k"
    max_input_tokens: int = 5665

    # Memory
    short_term_max_entries: int = 30
    short_term_max_tokens: int = 500
    long_term_max_results: int = 8
    long_term_max_tokens: int = 450
    retrieval: MemoryRetrievalConfig = field(default_factory=MemoryRetrievalConfig)

    # Proactive
    proactive: ProactiveConfig = field(default_factory=ProactiveConfig)

    # World
    world: WorldConfig = field(default_factory=WorldConfig)

    # Mood
    mood: MoodConfig = field(default_factory=MoodConfig)

    # Logging
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    # API
    api: APIConfig = field(default_factory=APIConfig)

    # Paths
    data_directory: str = ""
    config_directory: str = ""

    @property
    def data_path(self) -> Path:
        if self.data_directory:
            return Path(self.data_directory)
        return Path.home() / ".local" / "share" / "aphrodite-agent"

    @property
    def config_path(self) -> Path:
        if self.config_directory:
            return Path(self.config_directory)
        return Path.home() / ".config" / "aphrodite-agent"

    @property
    def db_path(self) -> Path:
        return self.data_path / "aphrodite.db"

    @property
    def characters_dir(self) -> Path:
        return self.data_path / "characters"

    @property
    def active_provider(self) -> ProviderInstanceConfig:
        return self.providers.get(self.provider_active, self.providers["primary"])


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_path: Path | None = None) -> Config:
    """Load configuration from TOML files."""
    config = Config()

    # Try loading from default locations
    if config_path is None:
        for candidate in [
            Path("./aphrodite.toml"),
            Path.home() / ".config" / "aphrodite-agent" / "aphrodite.toml",
        ]:
            if candidate.exists():
                config_path = candidate
                break

    if config_path and config_path.exists():
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)
        raw = _resolve_env_vars(raw)
        _apply_raw_config(config, raw)

    _validate_config(config)

    # Ensure directories exist
    config.data_path.mkdir(parents=True, exist_ok=True)
    config.characters_dir.mkdir(parents=True, exist_ok=True)

    return config


def _require_int(name: str, value: Any, minimum: int, maximum: int | None = None) -> None:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        bounds = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        raise ValueError(f"{name} must be an integer in {bounds}")


def _require_number(name: str, value: Any, minimum: float, maximum: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"{name} must be between {minimum} and {maximum}")


def _validate_clock(name: str, value: Any) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError(f"{name} must use 24-hour HH:MM format")


def _validate_config(config: Config) -> None:
    """Fail fast on values that would otherwise break runtime subsystems."""
    if config.timezone != "system":
        try:
            ZoneInfo(config.timezone)
        except (ZoneInfoNotFoundError, TypeError) as exc:
            raise ValueError(f"Unknown timezone: {config.timezone!r}") from exc

    from .character import validate_character_id

    validate_character_id(config.default_character)
    if config.provider_active not in config.providers:
        raise ValueError(f"Unknown active provider: {config.provider_active!r}")
    for name, provider in config.providers.items():
        parsed_url = urlparse(provider.base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError(f"provider.instances.{name}.base_url must be an HTTP(S) URL")
        _require_number(f"provider.instances.{name}.temperature", provider.temperature, 0.0, 2.0)
        _require_number(f"provider.instances.{name}.top_p", provider.top_p, 0.0, 1.0)
        _require_int(f"provider.instances.{name}.max_output_tokens", provider.max_output_tokens, 1)
        _require_int(f"provider.instances.{name}.timeout_seconds", provider.timeout_seconds, 1)
        _require_int(f"provider.instances.{name}.retries", provider.retries, 0, 5)

    if config.context_profile not in {"4k", "8k", "16k", "32k"}:
        raise ValueError(f"Unknown context profile: {config.context_profile!r}")
    _require_int("memory.short_term_max_entries", config.short_term_max_entries, 1)
    _require_int("memory.long_term_max_results", config.long_term_max_results, 1)
    _require_int("proactive.max_per_day", config.proactive.max_per_day, 0)
    _require_int("proactive.min_gap_minutes", config.proactive.min_gap_minutes, 0)
    _validate_clock("proactive.quiet_hours_start", config.proactive.quiet_hours_start)
    _validate_clock("proactive.quiet_hours_end", config.proactive.quiet_hours_end)
    if config.proactive.quiet_hours_start == config.proactive.quiet_hours_end:
        raise ValueError("proactive.quiet_hours_start must differ from quiet_hours_end")
    _require_int(
        "world.state_update_interval_minutes", config.world.state_update_interval_minutes, 1
    )
    _require_int("world.max_catchup_hours", config.world.max_catchup_hours, 1)
    _validate_clock("world.journal_time", config.world.journal_time)
    _validate_clock("world.journal_fallback_time", config.world.journal_fallback_time)
    _require_int("api.port", config.api.port, 1, 65_535)

    # Valence is a bipolar dimension (-1..1); the other mood dimensions are 0..1.
    _require_number("mood.baseline_valence", config.mood.baseline_valence, -1.0, 1.0)
    for field_name in (
        "baseline_arousal",
        "baseline_dominance",
        "baseline_affection",
        "baseline_trust",
        "baseline_curiosity",
        "max_delta_per_turn",
        "decay_valence",
        "decay_arousal",
    ):
        _require_number(f"mood.{field_name}", getattr(config.mood, field_name), 0.0, 1.0)


def _resolve_env_vars(value: Any) -> Any:
    """Resolve ${VAR} or $VAR environment variable references in strings."""
    if isinstance(value, str):

        def _replace(match):
            var_name = match.group(1) or match.group(2)
            return os.environ.get(var_name, match.group(0))

        return re.sub(r"\$\{(\w+)\}|\$(\w+)", _replace, value)
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(v) for v in value]
    return value


def _apply_raw_config(config: Config, raw: dict[str, Any]) -> None:
    """Apply raw TOML dict to config object."""
    if "general" in raw:
        g = raw["general"]
        if "language" in g:
            config.language = g["language"]
        if "timezone" in g:
            config.timezone = g["timezone"]
        if "default_character" in g:
            config.default_character = g["default_character"]
        if "data_directory" in g:
            config.data_directory = g["data_directory"]

    if "provider" in raw:
        p = raw["provider"]
        if "active" in p:
            config.provider_active = p["active"]
        if "instances" in p:
            for name, inst in p["instances"].items():
                cfg = ProviderInstanceConfig()
                for k, v in inst.items():
                    if hasattr(cfg, k):
                        setattr(cfg, k, v)
                config.providers[name] = cfg

    if "context" in raw:
        c = raw["context"]
        if "active_profile" in c:
            config.context_profile = c["active_profile"]
        profile_map = {"4k": 2845, "8k": 5665, "16k": 12000, "32k": 28000}
        config.max_input_tokens = profile_map.get(config.context_profile, 5665)

    if "memory" in raw:
        m = raw["memory"]
        if "short_term_max_entries" in m:
            config.short_term_max_entries = m["short_term_max_entries"]
        if "long_term_max_results" in m:
            config.long_term_max_results = m["long_term_max_results"]
        if "retrieval" in m:
            r = m["retrieval"]
            for k, v in r.items():
                if hasattr(config.retrieval, k):
                    setattr(config.retrieval, k, v)

    if "proactive" in raw:
        pr = raw["proactive"]
        for k, v in pr.items():
            if hasattr(config.proactive, k):
                setattr(config.proactive, k, v)

    if "world" in raw:
        w = raw["world"]
        for k, v in w.items():
            if hasattr(config.world, k):
                setattr(config.world, k, v)

    if "mood" in raw:
        mo = raw["mood"]
        for k, v in mo.items():
            if hasattr(config.mood, k):
                setattr(config.mood, k, v)

    if "logging" in raw:
        lo = raw["logging"]
        for k, v in lo.items():
            if hasattr(config.logging, k):
                setattr(config.logging, k, v)

    if "api" in raw:
        a = raw["api"]
        for k, v in a.items():
            if hasattr(config.api, k):
                setattr(config.api, k, v)
