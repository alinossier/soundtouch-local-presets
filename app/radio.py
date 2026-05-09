from __future__ import annotations

import logging

import aiohttp

from app import bose
from app.config import PresetConfig

LOGGER = logging.getLogger(__name__)


async def play_preset(session: aiohttp.ClientSession, speaker_ip: str, preset: PresetConfig) -> None:
    await bose.play_upnp_stream(session, speaker_ip, preset)
    LOGGER.info("UPnP playback request accepted for preset %s (%s)", preset.id, preset.name)
    try:
        now_playing = await bose.get_now_playing(session, speaker_ip)
    except Exception as exc:  # noqa: BLE001 - log best-effort verification without masking success.
        LOGGER.warning("Could not verify nowPlaying after playback request: %s", exc)
        return
    LOGGER.info("nowPlaying after playback request: %s", now_playing)
