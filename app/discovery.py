from __future__ import annotations

import asyncio
from contextlib import suppress
import ipaddress
import logging
import socket

import aiohttp
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

from app.bose import SpeakerInfo, get_info

LOGGER = logging.getLogger(__name__)

SOUNDTOUCH_SERVICE_TYPES = (
    "_soundtouch._tcp.local.",
    "_soundtouch-ws._tcp.local.",
)


class _ZeroconfCollector(ServiceListener):
    def __init__(self) -> None:
        self.addresses: set[str] = set()

    def add_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        self._collect(zc, service_type, name)

    def update_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        self._collect(zc, service_type, name)

    def remove_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        LOGGER.debug("mDNS service removed: %s %s", service_type, name)

    def _collect(self, zc: Zeroconf, service_type: str, name: str) -> None:
        info = zc.get_service_info(service_type, name, timeout=1000)
        if not info:
            return
        for address in info.addresses:
            family = socket.AF_INET if len(address) == 4 else socket.AF_INET6
            self.addresses.add(socket.inet_ntop(family, address))


async def discover_speaker(
    session: aiohttp.ClientSession,
    speaker_name: str,
    preferred_ip: str | None,
    last_known_ip: str | None = None,
) -> SpeakerInfo:
    candidates: list[str] = []
    for candidate in (preferred_ip, last_known_ip):
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    for ip in candidates:
        LOGGER.info("Checking configured/last-known SoundTouch candidate %s", ip)
        info = await _match_candidate(session, ip, speaker_name)
        if info:
            return info

    LOGGER.info("Discovering SoundTouch speakers via mDNS")
    for ip in await _discover_mdns_addresses():
        if ip not in candidates:
            candidates.append(ip)
            info = await _match_candidate(session, ip, speaker_name)
            if info:
                return info

    LOGGER.info("Discovering SoundTouch speakers via SSDP")
    for ip in await _discover_ssdp_addresses():
        if ip not in candidates:
            candidates.append(ip)
            info = await _match_candidate(session, ip, speaker_name)
            if info:
                return info

    raise LookupError(f"Could not discover SoundTouch speaker named {speaker_name!r}")


async def _match_candidate(
    session: aiohttp.ClientSession, ip: str, speaker_name: str
) -> SpeakerInfo | None:
    try:
        parsed = ipaddress.ip_address(ip)
        if parsed.version != 4:
            LOGGER.debug("Skipping non-IPv4 candidate %s", ip)
            return None
    except ValueError:
        LOGGER.warning("Skipping invalid IP candidate %s", ip)
        return None

    try:
        info = await get_info(session, ip)
    except Exception as exc:  # noqa: BLE001 - discovery should continue across bad candidates.
        LOGGER.debug("Candidate %s did not answer as SoundTouch: %s", ip, exc)
        return None

    LOGGER.info("Discovered SoundTouch candidate ip=%s name=%s device_id=%s", ip, info.name, info.device_id)
    if info.name == speaker_name:
        LOGGER.info("Matched configured speaker %s at %s", speaker_name, ip)
        return info
    return None


async def _discover_mdns_addresses(timeout: float = 5) -> list[str]:
    def collect() -> list[str]:
        collector = _ZeroconfCollector()
        zc = Zeroconf()
        browsers = []
        try:
            browsers = [ServiceBrowser(zc, service_type, collector) for service_type in SOUNDTOUCH_SERVICE_TYPES]
            import time

            time.sleep(timeout)
            return sorted(collector.addresses)
        finally:
            for browser in browsers:
                with suppress(Exception):
                    browser.cancel()
            zc.close()

    return await asyncio.to_thread(collect)


async def _discover_ssdp_addresses(timeout: float = 3) -> list[str]:
    request = "\r\n".join(
        [
            "M-SEARCH * HTTP/1.1",
            "HOST: 239.255.255.250:1900",
            'MAN: "ssdp:discover"',
            "MX: 2",
            "ST: urn:schemas-upnp-org:device:MediaRenderer:1",
            "",
            "",
        ]
    ).encode("ascii")

    def collect() -> list[str]:
        addresses: set[str] = set()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.settimeout(timeout)
            sock.sendto(request, ("239.255.255.250", 1900))
            while True:
                try:
                    data, addr = sock.recvfrom(65535)
                except socket.timeout:
                    break
                text = data.decode("utf-8", errors="replace").lower()
                if "soundtouch" in text or "bose" in text:
                    addresses.add(addr[0])
        finally:
            sock.close()
        return sorted(addresses)

    return await asyncio.to_thread(collect)
