# SoundTouch Local Presets for Home Assistant

Experimental custom integration for the SoundTouch Local Presets daemon.

It exposes one Home Assistant `media_player` entity:

- Device class: receiver
- Name: `Bose SoundTouch`
- Sources: every preset configured in the daemon, plus `AUX`
- Volume: step up and step down through the daemon API
- Power: toggle through the daemon API

## Install

Copy this folder to your Home Assistant config directory:

```text
/config/custom_components/soundtouch_local_presets
```

Restart Home Assistant, then add the integration from:

```text
Settings -> Devices & services -> Add integration -> SoundTouch Local Presets
```

Use the daemon URL, for example:

```text
http://192.168.1.25:8765
```

Use the IP address of the machine running the daemon, not the Bose speaker IP.
