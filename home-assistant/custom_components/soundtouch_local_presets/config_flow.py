from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_URL, DEFAULT_URL, DOMAIN


class SoundTouchLocalPresetsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            url = str(user_input[CONF_URL]).rstrip("/")
            session = async_get_clientsession(self.hass)
            try:
                await _validate_url(session, url)
            except (aiohttp.ClientError, TimeoutError, ValueError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Bose SoundTouch", data={CONF_URL: url})

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_URL, default=DEFAULT_URL): str}),
            errors=errors,
        )


async def _validate_url(session: aiohttp.ClientSession, url: str) -> None:
    async with session.get(f"{url}/api/state", timeout=aiohttp.ClientTimeout(total=5)) as response:
        if response.status != 200:
            raise ValueError(f"Unexpected HTTP status {response.status}")
        data = await response.json()
        if "presets" not in data:
            raise ValueError("Daemon response did not include presets")
