from __future__ import annotations

import asyncio
import logging
import signal
import time
from typing import Iterable

import aiohttp
from defusedxml import ElementTree
import websockets

from app import bose
from app.api import create_api_server
from app.config import AppConfig, load_config
from app.discovery import discover_speaker
from app.logging import configure_logging
from app.radio import play_preset

LOGGER = logging.getLogger(__name__)
BACKOFF_SECONDS = (1, 2, 5, 10, 30)


class PresetDaemon:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.last_known_ip: str | None = None
        self._stop = asyncio.Event()
        self._last_trigger_at: dict[int, float] = {}
        self._active_ip: str | None = None
        self.active_source: str | None = None

    def stop(self) -> None:
        self._stop.set()

    @property
    def active_ip(self) -> str | None:
        return self._active_ip

    async def run(self) -> None:
        mode = "listener-only debug" if self.config.service.listener_only else "playback"
        LOGGER.info("Starting SoundTouch preset daemon in %s mode", mode)
        async with aiohttp.ClientSession() as session:
            api_server = None
            if self.config.api.enabled:
                api_server = create_api_server(self, session)
                await api_server.start()
            backoff_index = 0
            try:
                while not self._stop.is_set():
                    try:
                        speaker = await discover_speaker(
                            session,
                            self.config.speaker.name,
                            self.config.speaker.preferred_ip,
                            self.last_known_ip,
                        )
                        self.last_known_ip = speaker.ip
                        self._active_ip = speaker.ip
                        if self.config.service.enforce_presets and not self.config.service.listener_only:
                            await bose.ensure_presets(session, speaker.ip, self.config.presets)
                        backoff_index = 0
                        await self._listen_until_disconnect(session, speaker.ip)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - daemon loop must survive network errors.
                        self._active_ip = None
                        delay = BACKOFF_SECONDS[min(backoff_index, len(BACKOFF_SECONDS) - 1)]
                        backoff_index += 1
                        LOGGER.warning("Speaker connection loop failed: %s; retrying in %ss", exc, delay)
                        try:
                            await asyncio.wait_for(self._stop.wait(), timeout=delay)
                        except TimeoutError:
                            pass
            finally:
                if api_server:
                    await api_server.stop()

    async def _listen_until_disconnect(self, session: aiohttp.ClientSession, ip: str) -> None:
        websocket_url = f"ws://{ip}:8080"
        LOGGER.info("Connecting SoundTouch WebSocket %s", websocket_url)
        async with websockets.connect(
            websocket_url,
            subprotocols=["gabbo"],
            ping_interval=20,
            ping_timeout=20,
        ) as websocket:
            LOGGER.info("SoundTouch WebSocket connected to %s", ip)
            health_task = asyncio.create_task(self._health_check_loop(session, ip, websocket))
            try:
                async for message in websocket:
                    if self._stop.is_set():
                        break
                    await self._handle_message(session, ip, message)
            finally:
                health_task.cancel()
                await asyncio.gather(health_task, return_exceptions=True)
                LOGGER.info("SoundTouch WebSocket disconnected from %s", ip)

    async def _health_check_loop(self, session: aiohttp.ClientSession, ip: str, websocket: websockets.ClientConnection) -> None:
        interval = self.config.service.health_check_interval_seconds
        while not self._stop.is_set():
            await asyncio.sleep(interval)
            try:
                info = await bose.get_info(session, ip)
                if self.config.service.enforce_presets and not self.config.service.listener_only:
                    await bose.ensure_presets(session, ip, self.config.presets)
            except Exception as exc:  # noqa: BLE001 - convert failed health into reconnect.
                LOGGER.warning("Health check failed for %s: %s", ip, exc)
                self._active_ip = None
                await websocket.close()
                return
            LOGGER.debug("Health check ok for %s (%s)", info.name, ip)

    async def _handle_message(self, session: aiohttp.ClientSession, ip: str, message: object) -> None:
        if isinstance(message, bytes):
            text = message.decode("utf-8", errors="replace")
        else:
            text = str(message)

        log_level = getattr(logging, self.config.service.websocket_raw_log_level, logging.DEBUG)
        LOGGER.log(log_level, "Raw WebSocket message: %s", text)

        root = bose.parse_xml_message(text)
        if root is None:
            LOGGER.debug("Ignoring non-XML WebSocket message")
            return

        detected = sorted(self._detect_preset_ids(root))
        if not detected:
            LOGGER.debug("Parsed WebSocket XML without preset match: %s", bose.element_to_dict(root))
            return

        for preset_id in detected:
            preset = self.config.presets.get(preset_id)
            if not preset:
                LOGGER.info("Detected unmapped preset %s", preset_id)
                continue

            LOGGER.info("Detected preset %s (%s)", preset_id, preset.name)
            if self._is_debounced(preset_id):
                LOGGER.info("Ignoring preset %s because it is inside debounce window", preset_id)
                continue
            self._mark_triggered(preset_id)

            if self.config.service.listener_only:
                LOGGER.info("Listener-only mode: would play preset %s (%s)", preset_id, preset.name)
                continue

            try:
                await self.play_preset_id(session, preset_id)
            except Exception as exc:  # noqa: BLE001 - keep listening after playback failures.
                LOGGER.exception("Playback failed for preset %s (%s): %s", preset_id, preset.name, exc)

    async def play_preset_id(self, session: aiohttp.ClientSession, preset_id: int) -> None:
        preset = self.config.presets[preset_id]
        if not self._active_ip:
            raise RuntimeError("Speaker is not connected yet")
        await asyncio.sleep(0.5)
        await play_preset(session, self._active_ip, preset)
        self.active_source = preset.name

    def _detect_preset_ids(self, root: ElementTree.Element) -> set[int]:
        matches: set[int] = set()
        interesting_tags = {
            "key",
            "keyEvent",
            "nowSelectionUpdated",
            "nowSelection",
            "preset",
            "presetID",
            "presetId",
            "preset_id",
        }

        for element in root.iter():
            tag = _strip_namespace(element.tag)
            attrs = {k.lower(): v for k, v in element.attrib.items()}
            text = (element.text or "").strip()
            haystack = " ".join([tag, text, " ".join(f"{k}={v}" for k, v in attrs.items())]).lower()

            if tag in interesting_tags or "preset" in haystack or "presets_" in haystack:
                LOGGER.info("Preset-related WebSocket element: tag=%s attrs=%s text=%s", tag, element.attrib, text)

            for value in _candidate_values(tag, text, attrs):
                preset_id = _parse_preset_id(value)
                if preset_id is not None:
                    matches.add(preset_id)

            for preset_id in self.config.presets:
                if f"preset_{preset_id}" in haystack or f"presets_{preset_id}" in haystack:
                    matches.add(preset_id)

        return matches

    def _is_debounced(self, preset_id: int) -> bool:
        last_at = self._last_trigger_at.get(preset_id, 0)
        return time.monotonic() - last_at < self.config.service.preset_debounce_seconds

    def _mark_triggered(self, preset_id: int) -> None:
        self._last_trigger_at[preset_id] = time.monotonic()


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _candidate_values(tag: str, text: str, attrs: dict[str, str]) -> Iterable[str]:
    lowered_tag = tag.lower()
    if lowered_tag in {"preset", "presetid", "preset_id"} and text:
        yield text

    for key, value in attrs.items():
        if "preset" in key or key in {"id", "key", "value"}:
            yield value

    if text:
        lowered_text = text.lower()
        if lowered_text.startswith(("preset_", "presets_")):
            yield lowered_text.rsplit("_", 1)[-1]
        elif lowered_tag in {"key", "keyevent"} and text.isdigit():
            yield text


def _parse_preset_id(value: str) -> int | None:
    cleaned = value.strip().lower()
    for prefix in ("preset_", "presets_"):
        if cleaned.startswith(prefix):
            cleaned = cleaned.removeprefix(prefix)
    try:
        return int(cleaned)
    except ValueError:
        return None


async def _amain() -> None:
    configure_logging()
    config = load_config()
    daemon = PresetDaemon(config)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, daemon.stop)
    await daemon.run()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
