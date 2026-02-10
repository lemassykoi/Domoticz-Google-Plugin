# Domoticz Google Audio Plugin

Domoticz Python plugin for Google Home and Nest audio speakers. Discovers audio devices and groups on the local network, provides status monitoring, volume/media control, and TTS voice notifications via gTTS.

**Audio only** — video devices (Chromecast, Google TV) are filtered out.

Tested on Linux (Raspberry Pi / Debian).

## Key Features

- Audio devices and groups are discovered automatically via mDNS/zeroconf
- Domoticz can control volume (including mute/unmute)
- Domoticz can control media playback (play/pause, seek, skip)
- Domoticz can control the active application (Spotify, YouTube, etc.)
- Voice notifications via Google Text-to-Speech (gTTS) — triggered by Domoticz notifications or event scripts
- When network connectivity is lost, devices show a red banner in the Domoticz UI
- Custom device icons included (Google Home Mini)

## Supported Audio Models

Google Home, Google Home Mini, Google Nest Mini, Google Nest Hub, Google Nest Audio, Nest Audio, Home Mini, Google Cast Group, Lenovo Smart Clock.

## Requirements

- Domoticz 2024+ with Python plugin support
- Python 3.9+
- `pychromecast` (13.0.4 or later)
- `gTTS` (optional — voice notifications disabled if not installed)

## Installation

```bash
cd domoticz/plugins
git clone https://github.com/lemassykoi/Domoticz-Google-Plugin.git
sudo pip install pychromecast gtts --break-system-packages
sudo systemctl restart domoticz
```

In the Domoticz web UI, go to **Setup → Hardware**, select **Google Audio Devices** from the dropdown, and click **Add**.

## Updating

```bash
cd domoticz/plugins/Domoticz-Google-Plugin
git pull
sudo systemctl restart domoticz
```

## Configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| Preferred Audio App | App to launch for 'Audio' script commands (Spotify / YouTube / None) | Spotify |
| Voice message volume | Volume during TTS playback (10–100%), restored afterwards | 50% |
| Voice Device/Group | Device or audio group name for Domoticz notifications (must match the device's friendly name exactly as seen in Google Home app) | (empty) |
| TTS Language | Language code for gTTS (e.g., `fr`, `en`, `de`, `es`) | `fr` |
| Room Plan Name | Domoticz room plan to auto-create and assign devices to | Google |
| Log to file | Write device status messages to `Messages.log` for debugging | False |
| Debug | Logging level (None / Plugin Debug / All) | None |

## Devices Created Per Speaker

Each discovered audio device creates 3 Domoticz devices:

| Device | Type | Description |
|--------|------|-------------|
| *Name* Status | Media Player | Shows current app/media info. On = playing, Off = idle/screensaver |
| *Name* Volume | Dimmer (0–100%) | Icon toggles mute, slider sets volume |
| *Name* Playing | Dimmer (0–100%) | Icon toggles play/pause, slider shows/sets position in current media |

A **Source** selector switch is created dynamically when apps are detected, showing all apps seen on the device.

## Script Commands

Commands can be sent to devices from Lua, dzVents, or Python event scripts.

| Command | Description |
|---------|-------------|
| `On` | Volume device: unmute. Playing device: resume |
| `Off` | Volume device: mute. Playing device: pause. Source device: quit app |
| `Set Level <N>` | Volume device: set volume %. Playing device: seek to N% of media. Source device: select app |
| `Play` / `Playing` | Resume current media |
| `Pause` / `Paused` | Pause current media |
| `Rewind` | Seek to start of current media |
| `Audio` | Switch to the preferred audio app |
| `Quit` | Quit the current application |
| `Sendnotification <text>` | Speak `<text>` on the target device via TTS |

### Example (Lua)

```lua
commandArray['Lounge Home Volume'] = 'Set Level 40'
commandArray['Lounge Home Playing'] = 'Pause'
```

## Voice Notifications (TTS)

See [NOTIFICATIONS.md](NOTIFICATIONS.md) for detailed examples using Lua, dzVents, Python, and the HTTP API.

### Quick Start

1. Set **Voice Device/Group** to your speaker's friendly name (e.g., `Bureau`)
2. Set **TTS Language** (e.g., `fr`)
3. From any Domoticz notification source, use subsystem `Google_Devices`:

```lua
-- Lua
commandArray['SendNotification'] = 'Title#Bonjour, ceci est un test.#0#sound##Google_Devices'
```

```bash
# HTTP API
curl "http://DOMOTICZ_IP:PORT/json.htm?type=command&param=sendnotification&subject=Test&body=Hello+world&subsystem=Google_Devices"
```

### How It Works

1. Plugin generates an MP3 via gTTS
2. Saves the device's current volume and app state
3. Sets volume to the configured notification level
4. Serves the MP3 via a built-in HTTP server (random port 10001–19999)
5. Waits for playback to complete
6. Restores the previous volume and app state

## Troubleshooting

### Devices not discovered
- Ensure the Domoticz host is on the same network/VLAN as the Google devices
- Check that mDNS/multicast traffic is allowed on your network
- Check the log for `Ignoring non-audio device` messages (video devices are filtered out)

### Voice notification not spoken
- Verify **Voice Device/Group** matches the device's friendly name exactly
- Ensure `gTTS` is installed: `pip install gTTS --break-system-packages`
- Check the Domoticz log for `gtts module import error` messages

### Notification cut short or "timed out"
- The plugin waits for playback duration + 5 seconds. Network latency may cause timeouts
- Check for TCP connection drops in the log
- The message may still have played correctly on the speaker

### Plugin won't restart after config change
- Python 3.13: zeroconf C extensions can't reload into a new sub-interpreter
- Restart the Domoticz service instead: `sudo systemctl restart domoticz`

### Thread warnings in log
- pychromecast spawns internal threads for each device connection
- The plugin waits up to 30 seconds for all threads to terminate on shutdown
- Occasional "thread is still running" messages during stop are normal and handled automatically

## License

See [LICENSE](LICENSE).
