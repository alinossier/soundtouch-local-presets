# SoundTouch Local Presets

Make the physical preset buttons on a Bose SoundTouch speaker useful again.

If you woke up one day and your Bose SoundTouch presets no longer play the internet radio stations you expect, this project is for you. It runs on a small computer on your home network, listens for preset button presses from your speaker, and starts the radio stream you choose.

The default example is:

- Preset `1` plays FIP Radio.
- Preset `2` plays FIP Jazz.

You can change those to any direct MP3 radio streams you like.

## What You Need

- A Bose SoundTouch speaker.
- A desktop, mini PC, NAS, Raspberry Pi, or server that stays on at home.
- Docker and Docker Compose installed on that machine.
- The speaker and that machine connected to the same home network.

This is easiest on Linux. It may work elsewhere, but Docker networking is simpler and more reliable on Linux for this kind of local-device project.

## The Short Version

1. Install Docker.
2. Clone this repo.
3. Put your speaker name in `config.yaml`.
4. Run `docker compose up -d --build`.
5. Press preset `1` or `2` on your Bose.

That is the whole idea. The rest of this README walks through each step.

## Step 1: Find Your Speaker Name

Open the Bose SoundTouch app and look for the name of your speaker.

Examples:

```text
Kitchen
Living Room
Office SoundTouch
```

You need the exact name.

If you know your speaker's IP address, you can also check it with:

```bash
curl http://SPEAKER_IP:8090/info
```

Look for the `<name>...</name>` value.

## Step 2: Edit The Config

Open `config.yaml`.

Change this:

```yaml
speaker:
  name: "YOUR-SOUNDTOUCH-SPEAKER-NAME"
```

To your real speaker name:

```yaml
speaker:
  name: "Kitchen"
```

If you know the speaker IP address, you can also set:

```yaml
speaker:
  name: "Kitchen"
  preferred_ip: "192.168.1.50"
```

If you do not know the IP, leave it as:

```yaml
preferred_ip: null
```

The service will try to discover the speaker automatically.

## Step 3: Choose Your Radio Stations

The default config uses FIP:

```yaml
presets:
  1:
    name: "FIP Radio"
    stream_url: "http://icecast.radiofrance.fr/fip-midfi.mp3?id=radiofrance"

  2:
    name: "FIP Jazz"
    stream_url: "http://icecast.radiofrance.fr/fipjazz-midfi.mp3?id=radiofrance"
```

To use different stations, replace `name` and `stream_url`.

Try to use plain HTTP MP3 streams when possible. Older SoundTouch speakers often handle those better than HTTPS, AAC, or HLS streams.

## Step 4: Start It

From this project folder, run:

```bash
docker compose up -d --build
```

Check that it started:

```bash
docker compose logs -f soundtouch-presets
```

You want to see something like:

```text
Matched configured speaker Kitchen at 192.168.1.50
SoundTouch WebSocket connected
```

Now press preset `1` on the speaker.

You should see:

```text
Detected preset 1 (FIP Radio)
Sending UPnP stream request for preset 1 (FIP Radio)
UPnP playback request accepted for preset 1 (FIP Radio)
```

Press preset `2` and you should see the same kind of message for preset `2`.

## Step 5: Keep It Running

The Docker Compose file already has:

```yaml
restart: unless-stopped
```

So after it is running in the background, Docker should restart it after reboots or crashes.

Useful commands:

```bash
docker compose logs -f soundtouch-presets
docker compose restart soundtouch-presets
docker compose down
docker compose up -d
```

## Recommended: Give Your Speaker A Fixed IP

This project can discover the speaker automatically, but life is easier if your speaker keeps the same IP address.

In your router settings, create a DHCP reservation for the Bose speaker.

General steps:

1. Open your router admin page.
2. Find connected devices.
3. Find the Bose speaker.
4. Reserve its current IP address.
5. Put that IP in `config.yaml` as `preferred_ip`.

Every router is a little different, but the feature is usually called one of:

- DHCP reservation
- Static lease
- Address reservation
- Reserved IP

## Local `.env` File

Instead of editing `config.yaml`, you can create a local `.env` file.

Example:

```text
LOG_LEVEL=INFO
SPEAKER_NAME=Kitchen
SPEAKER_PREFERRED_IP=192.168.1.50
LISTENER_ONLY=false
ENFORCE_PRESETS=true
```

The `.env` file is ignored by git, so it is safe for your personal settings.

## Test Without Playing Anything

If you want to first check whether your speaker sends preset button events, enable listener-only mode.

In `config.yaml`:

```yaml
service:
  listener_only: true
```

Then run:

```bash
LOG_LEVEL=DEBUG docker compose up
```

Press the preset buttons and look for messages containing:

```text
nowSelectionUpdated
preset id="1"
preset id="2"
```

When that works, set `listener_only` back to `false`.

## How It Works

The service uses local APIs exposed by Bose SoundTouch speakers:

- Port `8090`: SoundTouch HTTP API for `/info`, `/presets`, and `/nowPlaying`.
- Port `8080`: SoundTouch WebSocket events using the `gabbo` subprotocol.
- Port `8091`: UPnP/DLNA playback using `AVTransport`.

The important flow is:

1. The daemon finds your speaker.
2. It opens a WebSocket connection to the speaker.
3. You press preset `1` or `2`.
4. The speaker emits a local event.
5. The daemon sees that event.
6. The daemon tells the speaker to play the configured stream over UPnP.

Why UPnP? Some SoundTouch devices still accept stored custom radio presets but fail to play them directly through Bose's native internet-radio source. UPnP playback has been more reliable for direct MP3 streams.

## Troubleshooting

### The Service Says I Need To Set A Speaker Name

Edit `config.yaml` and replace:

```text
YOUR-SOUNDTOUCH-SPEAKER-NAME
```

With the real name of your speaker.

Or set:

```text
SPEAKER_NAME=Kitchen
```

In `.env`.

### The Speaker Is Not Found

Try setting `preferred_ip` in `config.yaml`.

You can test the speaker IP with:

```bash
curl http://SPEAKER_IP:8090/info
```

If that does not work:

- Make sure your computer/server and speaker are on the same network.
- Make sure the speaker is powered on.
- Make sure Docker is running with host networking.
- Check whether your router blocks devices from talking to each other.

### Button Presses Do Not Show Up

Run in debug mode:

```bash
LOG_LEVEL=DEBUG docker compose up
```

Then press the preset buttons and look at the logs.

If nothing appears, try assigning a normal radio station to that preset in the Bose app, then press the button again. Some speakers only emit useful preset events when the preset slot contains something.

### The Button Is Detected But Nothing Plays

First test the stream URL from your server:

```bash
curl -I "http://icecast.radiofrance.fr/fip-midfi.mp3?id=radiofrance"
```

If the stream does not respond, choose a different URL.

Then check what the speaker says is playing:

```bash
curl http://SPEAKER_IP:8090/nowPlaying
```

A good result usually includes:

```text
source="UPNP"
PLAY_STATE
```

### The Speaker Restarts Or The Network Drops

The service should reconnect automatically. You can also restart it manually:

```bash
docker compose restart soundtouch-presets
```

## What This Project Does Not Do

- It is not a full Bose SoundTouch replacement.
- It does not provide a web UI.
- It does not manage every Bose feature.
- It currently focuses on preset buttons and direct radio streams.
- It may not show rich artwork or metadata on the speaker.

## Related Work

- Gesellix Bose SoundTouch: a broader Go toolkit and cloud-replacement project for SoundTouch devices: https://github.com/gesellix/Bose-SoundTouch
- Bose SoundTouch Web API PDF: https://assets.bosecreative.com/m/496577402d128874/original/SoundTouch-Web-API.pdf

## Stream References

- FIP stream listing cross-check: https://fluxurlradio.fr/radios/fip
- Community-maintained FIP stream list: https://gist.github.com/harperreed/b5ceabbbbca0a79d478ec0b81f449892
