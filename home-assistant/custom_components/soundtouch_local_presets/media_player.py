from __future__ import annotations

from typing import Any

import aiohttp

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_URL

AUX_SOURCE = "AUX"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    session = async_get_clientsession(hass)
    async_add_entities([SoundTouchLocalPresetsMediaPlayer(session, entry.data[CONF_URL])], True)


class SoundTouchLocalPresetsMediaPlayer(MediaPlayerEntity):
    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_has_entity_name = False
    _attr_name = "Bose SoundTouch"
    _attr_should_poll = True
    _attr_supported_features = (
        MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.VOLUME_STEP
    )
    _attr_unique_id = "soundtouch_local_presets_receiver"

    def __init__(self, session: aiohttp.ClientSession, base_url: str) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._presets: list[dict[str, Any]] = []
        self._attr_source_list = [AUX_SOURCE]
        self._attr_source: str | None = None
        self._attr_state = MediaPlayerState.OFF

    async def async_update(self) -> None:
        try:
            data = await self._request_json("GET", "/api/state")
        except (aiohttp.ClientError, TimeoutError):
            self._attr_state = MediaPlayerState.OFF
            return

        self._presets = list(data.get("presets", []))
        preset_names = [str(preset["name"]) for preset in self._presets if preset.get("name")]
        self._attr_source_list = [*preset_names, AUX_SOURCE]
        self._attr_source = data.get("source")

        speaker = data.get("speaker", {})
        self._attr_state = MediaPlayerState.ON if speaker.get("online") else MediaPlayerState.OFF

    async def async_select_source(self, source: str) -> None:
        if source == AUX_SOURCE:
            await self._request_json("POST", "/api/speaker/aux")
            self._attr_source = AUX_SOURCE
            return

        preset = next((preset for preset in self._presets if preset.get("name") == source), None)
        if preset is None:
            await self.async_update()
            preset = next((preset for preset in self._presets if preset.get("name") == source), None)
        if preset is None:
            return

        await self._request_json("POST", f"/api/presets/{preset['id']}/play")
        self._attr_source = source
        self._attr_state = MediaPlayerState.ON

    async def async_volume_up(self) -> None:
        await self._request_json("POST", "/api/speaker/volume-up")

    async def async_volume_down(self) -> None:
        await self._request_json("POST", "/api/speaker/volume-down")

    async def async_turn_on(self) -> None:
        await self._request_json("POST", "/api/speaker/power-toggle")
        self._attr_state = MediaPlayerState.ON

    async def async_turn_off(self) -> None:
        await self._request_json("POST", "/api/speaker/power-toggle")
        self._attr_state = MediaPlayerState.OFF

    async def _request_json(self, method: str, path: str) -> dict[str, Any]:
        async with self._session.request(
            method,
            f"{self._base_url}{path}",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            response.raise_for_status()
            return await response.json()
