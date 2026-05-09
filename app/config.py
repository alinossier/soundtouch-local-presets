from __future__ import annotations

from dataclasses import dataclass
import base64
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml


@dataclass(frozen=True)
class SpeakerConfig:
    name: str
    preferred_ip: str | None = None


@dataclass(frozen=True)
class ServiceConfig:
    listener_only: bool = False
    enforce_presets: bool = True
    health_check_interval_seconds: int = 30
    preset_debounce_seconds: float = 3.0
    websocket_raw_log_level: str = "DEBUG"


@dataclass(frozen=True)
class ApiConfig:
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8765


@dataclass(frozen=True)
class PresetConfig:
    id: int
    name: str
    type: str
    content_item_type: str | None
    stream_url: str
    location: str
    source: str


@dataclass(frozen=True)
class AppConfig:
    speaker: SpeakerConfig
    service: ServiceConfig
    api: ApiConfig
    presets: dict[int, PresetConfig]


DEFAULT_SPEAKER_NAME = "YOUR-SOUNDTOUCH-SPEAKER-NAME"


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_str(name: str, default: str | None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _local_internet_radio_location(name: str, stream_url: str) -> str:
    payload = json.dumps(
        {"name": name, "imageUrl": "", "streamUrl": stream_url},
        separators=(",", ":"),
    ).encode("utf-8")
    data = base64.b64encode(payload).decode("ascii")
    return (
        "https://content.api.bose.io/core02/svc-bmx-adapter-orion/prod/orion/station"
        f"?data={quote(data, safe='')}"
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} must contain a YAML mapping")
    return data


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = Path(path or os.getenv("CONFIG_PATH", "config.yaml"))
    data = _load_yaml(config_path)

    speaker_data = data.get("speaker") or {}
    service_data = data.get("service") or {}
    api_data = data.get("api") or {}
    presets_data = data.get("presets") or {}

    speaker = SpeakerConfig(
        name=_env_str("SPEAKER_NAME", speaker_data.get("name")) or DEFAULT_SPEAKER_NAME,
        preferred_ip=_env_str("SPEAKER_PREFERRED_IP", speaker_data.get("preferred_ip")),
    )
    if speaker.name == DEFAULT_SPEAKER_NAME:
        raise ValueError(
            "Set speaker.name in config.yaml or SPEAKER_NAME to your Bose SoundTouch speaker name"
        )

    service = ServiceConfig(
        listener_only=_env_bool("LISTENER_ONLY", bool(service_data.get("listener_only", False))),
        enforce_presets=_env_bool("ENFORCE_PRESETS", bool(service_data.get("enforce_presets", True))),
        health_check_interval_seconds=int(
            os.getenv(
                "HEALTH_CHECK_INTERVAL_SECONDS",
                service_data.get("health_check_interval_seconds", 30),
            )
        ),
        preset_debounce_seconds=float(
            os.getenv("PRESET_DEBOUNCE_SECONDS", service_data.get("preset_debounce_seconds", 3))
        ),
        websocket_raw_log_level=str(
            os.getenv("WEBSOCKET_RAW_LOG_LEVEL", service_data.get("websocket_raw_log_level", "DEBUG"))
        ).upper(),
    )

    api = ApiConfig(
        enabled=_env_bool("API_ENABLED", bool(api_data.get("enabled", True))),
        host=_env_str("API_HOST", api_data.get("host")) or "0.0.0.0",
        port=int(os.getenv("API_PORT", api_data.get("port", 8765))),
    )

    presets: dict[int, PresetConfig] = {}
    for raw_id, raw_preset in presets_data.items():
        preset_id = int(raw_id)
        env_prefix = f"PRESET_{preset_id}"
        preset = raw_preset or {}
        stream_url = _env_str(f"{env_prefix}_STREAM_URL", preset.get("stream_url"))
        if not stream_url:
            raise ValueError(f"Preset {preset_id} requires stream_url")
        name = _env_str(f"{env_prefix}_NAME", preset.get("name")) or f"Preset {preset_id}"
        source = _env_str(f"{env_prefix}_SOURCE", preset.get("source")) or "LOCAL_INTERNET_RADIO"
        location = _env_str(f"{env_prefix}_LOCATION", preset.get("location"))
        if not location and source == "LOCAL_INTERNET_RADIO":
            location = _local_internet_radio_location(name, stream_url)
        elif not location:
            location = stream_url
        presets[preset_id] = PresetConfig(
            id=preset_id,
            name=name,
            type=_env_str(f"{env_prefix}_TYPE", preset.get("type")) or "internet_radio",
            content_item_type=_env_str(f"{env_prefix}_CONTENT_ITEM_TYPE", preset.get("content_item_type")),
            stream_url=stream_url,
            location=location,
            source=source,
        )

    return AppConfig(speaker=speaker, service=service, api=api, presets=presets)
