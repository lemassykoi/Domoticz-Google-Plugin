# Google Audio Plugin — Notification Guide

## How TTS Notifications Work

When a notification is sent, the plugin:
1. Generates an MP3 file using Google Text-to-Speech (gTTS)
2. Starts an HTTP server on a random port (10001–19999) to serve the file
3. Saves the device's current volume/app state
4. Sets the notification volume (Mode3 parameter)
5. Tells the Google device to play the MP3 from `http://<pi-ip>:<port>/<uuid>.mp3`
6. Waits for playback to complete
7. Restores the previous volume and app state

The HTTP server address is logged at startup, e.g.:
```
Notifications will use IP Address: 192.168.0.10:14378 to serve audio media.
```

---

## Method 1: Domoticz Notification System (Recommended)

The plugin registers as `Google_Devices` in Domoticz's notification system.
The target device is set via the **Voice Device/Group** parameter (Mode1).

### From Lua Scripts

```lua
-- Basic notification
commandArray['SendNotification'] = 'Title#Message text#0#sound##Google_Devices'

-- Format: Subject#Text#Priority#Sound#Extra#Subsystems
-- Only the Text field is spoken. Title/Subject are ignored by TTS.

-- Example:
commandArray['SendNotification'] = 'Test#Bonjour, ceci est un message de test.#0#sound##Google_Devices'
```

### From dzVents Scripts

```lua
domoticz.notify(
    'Title',                    -- subject (not spoken)
    'Bonjour, message de test', -- text (this is spoken)
    domoticz.PRIORITY_NORMAL,
    domoticz.SOUND_DEFAULT,
    '',                         -- extra
    domoticz.NSS_GOOGLE_DEVICES -- or 'Google_Devices'
)
```

Note: `domoticz.NSS_GOOGLE_DEVICES` may not exist in your dzVents version.
Use the string `'Google_Devices'` instead if needed:

```lua
domoticz.notify('Title', 'Message text', domoticz.PRIORITY_NORMAL, '', '', 'Google_Devices')
```

### From the Domoticz HTTP API

```bash
# Send via the JSON API (from any machine on the network)
curl "http://DOMOTICZ_IP:PORT/json.htm?type=command&param=sendnotification&subject=Test&body=Bonjour+ceci+est+un+test&subsystem=Google_Devices"
```

Replace `DOMOTICZ_IP:PORT` with your Domoticz address (e.g., `192.168.0.10:80`).

### From Python Scripts (Domoticz event scripts)

```python
import DomoticzEvents as DE
DE.Send_Notification("Title", "Message text", 0, "", 0, "", "Google_Devices")
```

---

## Method 2: Direct Device Command (Sendnotification)

You can send a notification to a **specific device** (not just the one configured in Mode1)
by sending a command directly to that device's Status unit.

### From Lua

```lua
-- Find the device Unit number for the target device's Status entry
-- e.g., "GOOGLE - Bureau Status" might be Unit 2
commandArray['OpenURL'] = 'http://127.0.0.1:80/json.htm?type=command&param=switchlight&idx=IDX&switchcmd=Sendnotification&level=0&passcode=MESSAGE_TEXT'
```

This is less practical than Method 1. Use the notification system instead.

### From the HTTP API

```bash
curl "http://DOMOTICZ_IP:PORT/json.htm?type=command&param=udevice&idx=IDX&nvalue=0&svalue=Sendnotification%20Your%20message%20here"
```

---

## Method 3: Timed Lua Script (Automated)

Create a file in `domoticz/scripts/lua/` named `script_time_<name>.lua`:

```lua
-- script_time_test_google_tts.lua
-- Runs every minute; sends a notification at a specific time

commandArray = {}

-- Send at 08:00 every day
if (os.date('%H:%M') == '08:00') then
    commandArray['SendNotification'] = 'Morning#Bonjour, il est huit heures.#0#sound##Google_Devices'
end

return commandArray
```

### Weather Announcement Example

```lua
-- script_time_weather_announce.lua
commandArray = {}

if (os.date('%H:%M') == '07:30') then
    local temp = otherdevices_svalues['Outside Temperature']
    local msg = string.format("Bonjour. La température extérieure est de %s degrés.", temp)
    commandArray['SendNotification'] = 'Météo#' .. msg .. '#0#sound##Google_Devices'
end

return commandArray
```

---

## Configuration Parameters

| Parameter | Field | Description |
|-----------|-------|-------------|
| Voice Device/Group | Mode1 | Target device name (must match exactly, e.g., `Bureau`) |
| TTS Language | Mode2 | Language code for gTTS (e.g., `fr`, `en`, `de`, `es`) |
| Voice message volume | Mode3 | Volume during playback (10–100%), restored after |
| Preferred Audio App | Address | App to launch for 'Audio' commands |

---

## Troubleshooting

### Message is not spoken
- Check that **Mode1** (Voice Device/Group) matches the device's friendly name exactly
- Check the Domoticz log for errors
- Ensure `gTTS` is installed: `pip install gTTS --break-system-packages` (sudo if used in root env)

### Message is cut short
- The plugin chunks the MP3 file over HTTP in 16KB pieces
- Check for "No transport" errors in the log — this means a TCP connection dropped during file transfer
- The Google device retries automatically, but if all retries fail the audio may be incomplete

### "timed out" in log
- The playback exceeded the expected duration + 5 seconds safety margin
- This can happen with network latency or slow device response
- The message may still have played correctly on the speaker

### Plugin won't restart (interpreter error)
- Python 3.13 limitation: zeroconf C extensions can't reload into a new sub-interpreter
- You must restart the Domoticz service (`sudo systemctl restart domoticz`) to apply config changes
- Simply updating plugin settings in the UI and clicking "Update" will fail on restart
