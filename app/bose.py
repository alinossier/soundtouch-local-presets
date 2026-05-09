from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any
from xml.sax.saxutils import escape

import aiohttp
from defusedxml import ElementTree

from app.config import PresetConfig

LOGGER = logging.getLogger(__name__)


class BoseError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpeakerInfo:
    ip: str
    name: str
    device_id: str | None = None


def _xml_text(root: ElementTree.Element, tag: str) -> str | None:
    found = root.find(tag)
    if found is not None and found.text:
        return found.text.strip()
    return None


async def get_info(session: aiohttp.ClientSession, ip: str, timeout: float = 5) -> SpeakerInfo:
    url = f"http://{ip}:8090/info"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
        text = await response.text()
        if response.status != 200:
            raise BoseError(f"GET /info on {ip} returned HTTP {response.status}: {text[:200]}")

    root = ElementTree.fromstring(text)
    name = _xml_text(root, "name")
    if not name:
        raise BoseError(f"GET /info on {ip} did not include a speaker name")
    return SpeakerInfo(ip=ip, name=name, device_id=root.attrib.get("deviceID"))


async def get_now_playing(session: aiohttp.ClientSession, ip: str, timeout: float = 5) -> str:
    url = f"http://{ip}:8090/nowPlaying"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
        text = await response.text()
        if response.status != 200:
            raise BoseError(f"GET /nowPlaying on {ip} returned HTTP {response.status}: {text[:200]}")
        return text


async def get_presets(session: aiohttp.ClientSession, ip: str, timeout: float = 5) -> ElementTree.Element:
    url = f"http://{ip}:8090/presets"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
        text = await response.text()
        if response.status != 200:
            raise BoseError(f"GET /presets on {ip} returned HTTP {response.status}: {text[:200]}")
    return ElementTree.fromstring(text)


def build_content_item_xml(preset: PresetConfig) -> str:
    attributes = {
        "source": preset.source,
        "location": preset.location,
        "sourceAccount": "",
        "isPresetable": "true",
    }
    if preset.content_item_type:
        attributes["type"] = preset.content_item_type
    serialized_attributes = " ".join(
        f'{name}="{escape(value)}"' for name, value in attributes.items()
    )
    return (
        f"<ContentItem {serialized_attributes}>"
        f"<itemName>{escape(preset.name)}</itemName>"
        "</ContentItem>"
    )


def build_preset_xml(preset: PresetConfig) -> str:
    return f'<preset id="{preset.id}">{build_content_item_xml(preset)}</preset>'


def preset_matches(current_presets: ElementTree.Element, preset: PresetConfig) -> bool:
    for preset_element in current_presets.findall("preset"):
        if preset_element.attrib.get("id") != str(preset.id):
            continue
        content_item = preset_element.find("ContentItem")
        if content_item is None:
            return False
        item_name = content_item.find("itemName")
        return (
            content_item.attrib.get("source") == preset.source
            and content_item.attrib.get("type") == preset.content_item_type
            and content_item.attrib.get("location") == preset.location
            and (item_name.text if item_name is not None else None) == preset.name
        )
    return False


async def store_preset(session: aiohttp.ClientSession, ip: str, preset: PresetConfig) -> str:
    payload = build_preset_xml(preset)
    url = f"http://{ip}:8090/storePreset"
    LOGGER.info("Storing preset %s as %s on %s", preset.id, preset.name, ip)
    async with session.post(
        url,
        data=payload.encode("utf-8"),
        headers={"Content-Type": "application/xml"},
        timeout=aiohttp.ClientTimeout(total=10),
    ) as response:
        text = await response.text()
        if response.status >= 300:
            raise BoseError(f"POST /storePreset on {ip} returned HTTP {response.status}: {text[:500]}")
        return text


async def ensure_presets(
    session: aiohttp.ClientSession,
    ip: str,
    presets: dict[int, PresetConfig],
) -> None:
    current_presets = await get_presets(session, ip)
    for preset in presets.values():
        if preset_matches(current_presets, preset):
            LOGGER.debug("Preset %s already matches %s", preset.id, preset.name)
            continue
        await store_preset(session, ip, preset)


async def select_content(session: aiohttp.ClientSession, ip: str, preset: PresetConfig) -> str:
    payload = build_content_item_xml(preset)
    url = f"http://{ip}:8090/select"
    LOGGER.info("Sending playback request for preset %s (%s) to %s", preset.id, preset.name, ip)
    async with session.post(
        url,
        data=payload.encode("utf-8"),
        headers={"Content-Type": "application/xml"},
        timeout=aiohttp.ClientTimeout(total=10),
    ) as response:
        text = await response.text()
        if response.status >= 300:
            raise BoseError(f"POST /select on {ip} returned HTTP {response.status}: {text[:500]}")
        return text


async def send_key(session: aiohttp.ClientSession, ip: str, key: str) -> None:
    await _send_key_state(session, ip, key, "press")
    await _send_key_state(session, ip, key, "release")


async def _send_key_state(session: aiohttp.ClientSession, ip: str, key: str, state: str) -> str:
    payload = f'<key state="{escape(state)}" sender="Gabbo">{escape(key)}</key>'
    url = f"http://{ip}:8090/key"
    async with session.post(
        url,
        data=payload.encode("utf-8"),
        headers={"Content-Type": "application/xml"},
        timeout=aiohttp.ClientTimeout(total=5),
    ) as response:
        text = await response.text()
        if response.status >= 300:
            raise BoseError(f"POST /key {key} {state} on {ip} returned HTTP {response.status}: {text[:500]}")
        return text


async def volume_up(session: aiohttp.ClientSession, ip: str) -> None:
    LOGGER.info("Sending volume up to %s", ip)
    await send_key(session, ip, "VOLUME_UP")


async def volume_down(session: aiohttp.ClientSession, ip: str) -> None:
    LOGGER.info("Sending volume down to %s", ip)
    await send_key(session, ip, "VOLUME_DOWN")


async def power_toggle(session: aiohttp.ClientSession, ip: str) -> None:
    LOGGER.info("Sending power toggle to %s", ip)
    await send_key(session, ip, "POWER")


async def select_aux(session: aiohttp.ClientSession, ip: str) -> str:
    payload = '<ContentItem source="AUX" sourceAccount="AUX"><itemName>AUX IN</itemName></ContentItem>'
    url = f"http://{ip}:8090/select"
    LOGGER.info("Selecting AUX on %s", ip)
    async with session.post(
        url,
        data=payload.encode("utf-8"),
        headers={"Content-Type": "application/xml"},
        timeout=aiohttp.ClientTimeout(total=10),
    ) as response:
        text = await response.text()
        if response.status >= 300:
            raise BoseError(f"POST /select AUX on {ip} returned HTTP {response.status}: {text[:500]}")
        return text


def _soap_envelope(action: str, body: str) -> str:
    return (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        "<s:Body>"
        f'<u:{action} xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
        f"{body}"
        f"</u:{action}>"
        "</s:Body>"
        "</s:Envelope>"
    )


async def _post_avtransport(session: aiohttp.ClientSession, ip: str, action: str, body: str) -> str:
    url = f"http://{ip}:8091/AVTransport/Control"
    payload = _soap_envelope(action, body)
    async with session.post(
        url,
        data=payload.encode("utf-8"),
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": f'"urn:schemas-upnp-org:service:AVTransport:1#{action}"',
        },
        timeout=aiohttp.ClientTimeout(total=10),
    ) as response:
        text = await response.text()
        if response.status >= 300:
            raise BoseError(
                f"UPnP AVTransport {action} on {ip} returned HTTP {response.status}: {text[:500]}"
            )
        return text


async def play_upnp_stream(session: aiohttp.ClientSession, ip: str, preset: PresetConfig) -> None:
    LOGGER.info("Sending UPnP stream request for preset %s (%s) to %s", preset.id, preset.name, ip)
    await _post_avtransport(
        session,
        ip,
        "SetAVTransportURI",
        "<InstanceID>0</InstanceID>"
        f"<CurrentURI>{escape(preset.stream_url)}</CurrentURI>"
        "<CurrentURIMetaData></CurrentURIMetaData>",
    )
    await _post_avtransport(
        session,
        ip,
        "Play",
        "<InstanceID>0</InstanceID><Speed>1</Speed>",
    )


def parse_xml_message(message: str) -> ElementTree.Element | None:
    try:
        return ElementTree.fromstring(message)
    except ElementTree.ParseError:
        return None


def element_to_dict(element: ElementTree.Element) -> dict[str, Any]:
    return {
        "tag": element.tag,
        "attributes": dict(element.attrib),
        "text": (element.text or "").strip(),
        "children": [element_to_dict(child) for child in list(element)],
    }
