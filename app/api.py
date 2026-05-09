from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable

import aiohttp
from aiohttp import web

from app import bose

if TYPE_CHECKING:
    from app.main import PresetDaemon

LOGGER = logging.getLogger(__name__)


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class ApiServer:
    def __init__(self, daemon: PresetDaemon, session: aiohttp.ClientSession) -> None:
        self.daemon = daemon
        self.session = session
        self.app = web.Application()
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self._setup_routes()

    async def start(self) -> None:
        api_config = self.daemon.config.api
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, api_config.host, api_config.port)
        await self.site.start()
        LOGGER.info("HTTP API listening on http://%s:%s", api_config.host, api_config.port)

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
            self.site = None

    def _setup_routes(self) -> None:
        self.app.router.add_get("/api/state", self._handle_state)
        self.app.router.add_get("/api/presets", self._handle_presets)
        self.app.router.add_post("/api/presets/{preset_id}/play", self._handle_play_preset)
        self.app.router.add_post("/api/speaker/volume-up", self._simple_action(bose.volume_up, "volume_up"))
        self.app.router.add_post("/api/speaker/volume-down", self._simple_action(bose.volume_down, "volume_down"))
        self.app.router.add_post(
            "/api/speaker/power-toggle",
            self._simple_action(bose.power_toggle, "power_toggle"),
        )
        self.app.router.add_post("/api/speaker/aux", self._handle_aux)

    async def _handle_state(self, request: web.Request) -> web.Response:
        ip = self.daemon.active_ip
        now_playing: str | None = None
        if ip:
            try:
                now_playing = await bose.get_now_playing(self.session, ip)
            except Exception as exc:  # noqa: BLE001 - state should still answer when nowPlaying fails.
                LOGGER.warning("Could not fetch nowPlaying for API state: %s", exc)

        return web.json_response(
            {
                "speaker": {
                    "name": self.daemon.config.speaker.name,
                    "ip": ip,
                    "online": ip is not None,
                },
                "service": {
                    "listener_only": self.daemon.config.service.listener_only,
                    "enforce_presets": self.daemon.config.service.enforce_presets,
                },
                "source": self.daemon.active_source,
                "presets": _serialize_presets(self.daemon),
                "now_playing_xml": now_playing,
            }
        )

    async def _handle_presets(self, request: web.Request) -> web.Response:
        return web.json_response({"presets": _serialize_presets(self.daemon)})

    async def _handle_play_preset(self, request: web.Request) -> web.Response:
        try:
            preset_id = int(request.match_info["preset_id"])
        except ValueError as exc:
            raise ApiError("preset_id must be an integer") from exc

        preset = self.daemon.config.presets.get(preset_id)
        if not preset:
            raise ApiError(f"Unknown preset {preset_id}", status=404)

        await self._require_online()
        await self.daemon.play_preset_id(self.session, preset_id)
        return web.json_response({"ok": True, "action": "play_preset", "preset": _serialize_preset(preset)})

    async def _handle_aux(self, request: web.Request) -> web.Response:
        ip = await self._require_online()
        await bose.select_aux(self.session, ip)
        self.daemon.active_source = "AUX"
        return web.json_response({"ok": True, "action": "aux"})

    def _simple_action(
        self,
        action: Callable[[aiohttp.ClientSession, str], Awaitable[None]],
        action_name: str,
    ) -> Callable[[web.Request], Awaitable[web.Response]]:
        async def handler(request: web.Request) -> web.Response:
            ip = await self._require_online()
            await action(self.session, ip)
            return web.json_response({"ok": True, "action": action_name})

        return handler

    async def _require_online(self) -> str:
        if not self.daemon.active_ip:
            raise ApiError("Speaker is not connected yet", status=503)
        return self.daemon.active_ip


@web.middleware
async def error_middleware(request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]) -> web.StreamResponse:
    try:
        return await handler(request)
    except ApiError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
    except Exception as exc:  # noqa: BLE001 - API should return JSON errors.
        LOGGER.exception("Unhandled API error: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


def create_api_server(daemon: PresetDaemon, session: aiohttp.ClientSession) -> ApiServer:
    server = ApiServer(daemon, session)
    server.app.middlewares.append(error_middleware)
    return server


def _serialize_presets(daemon: PresetDaemon) -> list[dict[str, object]]:
    return [_serialize_preset(preset) for preset in daemon.config.presets.values()]


def _serialize_preset(preset) -> dict[str, object]:
    return {
        "id": preset.id,
        "name": preset.name,
        "type": preset.type,
        "stream_url": preset.stream_url,
    }
