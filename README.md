# SoundTouch Local Presets

Restore useful physical preset buttons on Bose SoundTouch speakers using only local network APIs.

Bose SoundTouch devices still expose a surprisingly capable local API: HTTP control on port `8090`, a `gabbo` WebSocket event stream on port `8080`, and UPnP/DLNA media-renderer playback on port `8091`. This project combines those interfaces into a small Dockerized daemon that listens for physical preset button presses and plays configurable internet radio streams without requiring the Bose app or Bose cloud services to make the playback decision.

The original use case was simple:

- Preset `1` should play FIP Radio.
- Preset `2` should play FIP Jazz.

The project is intentionally generic, though. You can change the speaker name, preset slots, stream names, and stream URLs in `config.yaml`.

## Why This Exists

Some SoundTouch firmware still reports preset-button events locally, but custom internet-radio playback through the SoundTouch-native `/select` path can fail with errors such as `UNKNOWN_SOURCE_ERROR` or `INVALID_SOURCE`. This daemon uses the physical preset event as the trigger, then starts playback through the speaker's UPnP AVTransport service, which has proven more reliable for direct MP3 streams.

This is not a full SoundTouch replacement and it does not try to model the entire Bose API. It is a narrow, practical bridge for people who want their hardware preset buttons back.

## How It Works

1. Discovers a SoundTouch speaker by name.
2. Connects to `ws://SPEAKER_IP:8080` using the required `gabbo` WebSocket subprotocol.
3. Listens for preset events such as `nowSelectionUpdated` with `<preset id="1">`.
4. Debounces repeated events.
5. Starts the configured stream with UPnP AVTransport:

   ```text
   POST http://SPEAKER_IP:8091/AVTransport/Control
   SOAPAction: SetAVTransportURI
   SOAPAction: Play
   ```

6. Periodically verifies that the physical preset slots are still labelled/stored with the configured stations so button presses keep producing useful preset IDs.
7. Reconnects after speaker restarts, network drops, and daemon restarts.

## Features

- Dockerized Python 3.12 asyncio service.
- Local-only communication with the speaker.
- mDNS and SSDP discovery.
- Optional preferred IP for faster startup.
- Persistent WebSocket listener with reconnect backoff.
- UPnP playback fallback for direct streams.
- Listener-only debug mode for inspecting raw Bose events.
- Environment-variable overrides for mounted or immutable configs.
- Health checks and preset repair.

## Requirements

- A Bose SoundTouch speaker reachable from the Docker host.
- Docker and Docker Compose.
- Host networking support for the container.
- The speaker and Docker host on the same LAN/VLAN.
- A speaker firmware that emits local WebSocket events.

Linux hosts are the easiest target. Docker Desktop on macOS/Windows may not support `network_mode: host` in the same way, which can make mDNS/SSDP discovery and local speaker access less predictable.

## Quick Start

1. Clone the repo.

2. Edit `config.yaml` and set your actual Bose speaker name:

   ```yaml
   speaker:
     name: "YOUR ACTUAL SPEAKER NAME"
     preferred_ip: null
   ```

   The name must match the value returned by:

   ```bash
   curl http://SPEAKER_IP:8090/info
   ```

3. Optionally set `speaker.preferred_ip` if you know the speaker's LAN IP.

4. Build and run:

   ```bash
   docker compose build
   docker compose up
   ```

5. Watch the logs and press preset `1` or `2` on the speaker:

   ```bash
   docker compose logs -f soundtouch-presets
   ```

Expected logs include:

```text
Matched configured speaker ... at ...
SoundTouch WebSocket connected
Detected preset 1 (FIP Radio)
Sending UPnP stream request for preset 1 (FIP Radio)
UPnP playback request accepted for preset 1 (FIP Radio)
```

The checked-in config intentionally contains `YOUR-SOUNDTOUCH-SPEAKER-NAME`. The service will fail fast until you replace it or set `SPEAKER_NAME`.

## Configuration

The default `config.yaml` maps:

- Preset `1` to FIP Radio.
- Preset `2` to FIP Jazz.

```yaml
speaker:
  name: "YOUR-SOUNDTOUCH-SPEAKER-NAME"
  preferred_ip: null

service:
  listener_only: false
  enforce_presets: true
  health_check_interval_seconds: 30
  preset_debounce_seconds: 3
  websocket_raw_log_level: "DEBUG"

presets:
  1:
    name: "FIP Radio"
    stream_url: "http://icecast.radiofrance.fr/fip-midfi.mp3?id=radiofrance"

  2:
    name: "FIP Jazz"
    stream_url: "http://icecast.radiofrance.fr/fipjazz-midfi.mp3?id=radiofrance"
```

Each preset also has SoundTouch metadata fields in the full config:

- `type`: descriptive local config type.
- `content_item_type`: usually `stationurl`.
- `location`: stored in the SoundTouch preset slot; for this project it usually matches `stream_url`.
- `source`: usually `LOCAL_INTERNET_RADIO`, used only for storing preset metadata.

The actual playback path uses `stream_url` through UPnP AVTransport.

## Environment Overrides

You can override config values without editing `config.yaml`:

```text
LOG_LEVEL=DEBUG
CONFIG_PATH=/app/config.yaml
SPEAKER_NAME=Kitchen SoundTouch
SPEAKER_PREFERRED_IP=192.168.1.50
LISTENER_ONLY=false
ENFORCE_PRESETS=true
PRESET_DEBOUNCE_SECONDS=3
HEALTH_CHECK_INTERVAL_SECONDS=30
WEBSOCKET_RAW_LOG_LEVEL=DEBUG
PRESET_1_NAME=FIP Radio
PRESET_1_STREAM_URL=http://icecast.radiofrance.fr/fip-midfi.mp3?id=radiofrance
PRESET_2_NAME=FIP Jazz
PRESET_2_STREAM_URL=http://icecast.radiofrance.fr/fipjazz-midfi.mp3?id=radiofrance
```

See `.env.example` for a starting point.

## Docker Compose

The compose file uses host networking:

```yaml
network_mode: host
```

That is intentional. Discovery protocols and direct access to speaker ports `8080`, `8090`, and `8091` are much easier when the container is on the host network.

Run in the foreground:

```bash
LOG_LEVEL=DEBUG docker compose up
```

Run as a background service:

```bash
docker compose up -d
```

View logs:

```bash
docker compose logs -f soundtouch-presets
```

Stop:

```bash
docker compose down
```

## DHCP Reservation

Create a DHCP reservation for your speaker in your router. This is optional but strongly recommended.

Typical flow:

1. Find the Bose speaker in your router's connected devices list.
2. Copy its MAC address.
3. Add a DHCP reservation for that MAC address.
4. Reboot the speaker or renew its lease.
5. Put the reserved IP in `speaker.preferred_ip`.

## Listener-Only Debug Mode

Before enabling playback, you can confirm that your speaker emits usable local preset events.

Set:

```yaml
service:
  listener_only: true
```

Then run with debug logging and press the speaker's preset buttons:

```bash
LOG_LEVEL=DEBUG docker compose up
```

Look for raw WebSocket messages containing `nowSelectionUpdated` and `<preset id="...">`.

## Troubleshooting

If the service refuses to start with a speaker-name error:

- Replace `YOUR-SOUNDTOUCH-SPEAKER-NAME` in `config.yaml`.
- Or set `SPEAKER_NAME` in the environment.

If the speaker is not discovered:

- Set `speaker.preferred_ip`.
- Confirm `curl http://SPEAKER_IP:8090/info` works from the Docker host.
- Confirm the speaker and Docker host are on the same LAN/VLAN.
- Confirm the container is using host networking.

If WebSocket connects but button presses do not appear:

- Run with `LOG_LEVEL=DEBUG`.
- Press each preset button briefly and inspect `Raw WebSocket message` lines.
- Assign a harmless real station to the Bose preset in the SoundTouch app, then press the button again.
- Open an issue with the raw XML if your firmware emits a different event shape.

If playback fails:

- Test the stream from the Docker host:

  ```bash
  curl -I "http://icecast.radiofrance.fr/fip-midfi.mp3?id=radiofrance"
  ```

- Prefer plain HTTP MP3 streams over HTTPS, AAC, or HLS for older SoundTouch models.
- Confirm the speaker exposes UPnP AVTransport:

  ```bash
  curl http://SPEAKER_IP:8091/XD/BO5EBO5E-F00D-F00D-FEED-DEVICEID.xml
  ```

- Inspect `/nowPlaying`:

  ```bash
  curl http://SPEAKER_IP:8090/nowPlaying
  ```

A successful UPnP playback state usually shows `source="UPNP"` and `PLAY_STATE`.

## Limitations

- Only preset IDs configured in `config.yaml` are handled.
- The daemon currently targets direct stream URLs, especially MP3 radio streams.
- It does not provide a web UI.
- It does not replace the full Bose cloud stack.
- UPnP playback may not preserve rich station artwork or metadata.

## Project Notes

Useful additions for your own fork:

- Screenshots or sample logs from your own setup.
- Any stream URLs you want as defaults.
- A note about which SoundTouch model and firmware you tested.

## Related Work

- Gesellix Bose SoundTouch: a much broader Go toolkit and cloud-replacement project for SoundTouch devices: https://github.com/gesellix/Bose-SoundTouch
- Bose SoundTouch Web API PDF: https://assets.bosecreative.com/m/496577402d128874/original/SoundTouch-Web-API.pdf

## Stream References

- FIP stream listing cross-check: https://fluxurlradio.fr/radios/fip
- Community-maintained FIP stream list: https://gist.github.com/harperreed/b5ceabbbbca0a79d478ec0b81f449892
