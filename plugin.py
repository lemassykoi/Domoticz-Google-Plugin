# Google Audio Devices
#
# Listens for Google Home audio devices and monitors the ones it finds.
# New ones are added automatically and named using their friendly name.
# Audio only: video devices (Chromecast, Google TV) are filtered out.
#
# Author: Dnpwwo, 2019 - Modified by lemassykoi 2026
#         Based on the Domoticz plugin authored by Tsjippy (https://github.com/Tsjippy)
#         Huge shout out to Paulus Shoutsen (https://github.com/balloob) for his pychromecast library
#         And Fred Clift (https://github.com/minektur) who wrote the initial communication layer
#
"""
<plugin key="GoogleDevs" name="Google Audio Devices" author="lemassykoi" version="2026.2" wikilink="https://github.com/lemassykoi/Domoticz-Google-Plugin" externallink="https://store.google.com/product/google-home">
    <description>
        <h2>Domoticz Google Audio Plugin</h2><br/>
        <h3>Key Features</h3>
        <ul style="list-style-type:square">
            <li style="line-height:normal">Audio devices (Google Home, Nest speakers, audio groups) are discovered automatically</li>
            <li style="line-height:normal">Video devices (Chromecast, Google TV) are ignored</li>
            <li style="line-height:normal">When network connectivity is lost the Domoticz UI will optionally show the device(s) with Red banner</li>
            <li style="line-height:normal">Domoticz can control the Volume including Mute/Unmute</li>
            <li style="line-height:normal">Domoticz can control the playing media. Play/Pause and skip forward and backwards</li>
            <li style="line-height:normal">Google devices can be the targets of native Domoticz notifications (TTS via gTTS)</li>
            <li style="line-height:normal">Voice notifications can be sent to selected Google devices from event scripts (Lua or Python)</li>
        </ul>
        <h3>Devices</h3>
        <ul style="list-style-type:square">
            <li style="line-height:normal">Status - Basic status indicator, On/Off</li>
            <li style="line-height:normal">Volume - Icon mutes/unmutes, slider shows/sets volume</li>
            <li style="line-height:normal">Source - Selector switch for content source (App)</li>
            <li style="line-height:normal">Playing - Icon Pauses/Resumes, slider shows/sets percentage through media</li>
        </ul>
        <h3>Configuration</h3>
        <ul style="list-style-type:square">
            <li style="line-height:normal">TTS Media Server Port - Fixed port for the HTTP server that serves TTS audio to Google devices</li>
            <li style="line-height:normal">Voice Device/Group - If specified, device (or Audio Group) will receive audible notifications</li>
            <li style="line-height:normal">TTS Language - Language code for gTTS text-to-speech</li>
            <li style="line-height:normal">Voice message volume - Volume to play messages (previous level will be restored afterwards)</li>
            <li style="line-height:normal">Room Plan Name - Name of the Domoticz room plan to create and assign devices to</li>
            <li style="line-height:normal">Preferred Audio App - Application to select when scripts request 'Audio' mode</li>
            <li style="line-height:normal">Debug - Logging level for troubleshooting</li>
        </ul>
    </description>
    <params>
        <param field="Port" label="TTS Media Server Port" width="75px" required="true" default="15555"/>
        <param field="Mode1" label="Voice Device/Group" width="150px"/>
        <param field="Mode2" label="TTS Language" width="75px" required="false" default="fr"/>
        <param field="Mode3" label="Voice message volume" width="50px" required="true">
            <options>
                <option label="10%" value="10"/>
                <option label="20%" value="20"/>
                <option label="30%" value="30"/>
                <option label="40%" value="40"/>
                <option label="50%" value="50" default="true"/>
                <option label="60%" value="60"/>
                <option label="70%" value="70"/>
                <option label="80%" value="80"/>
                <option label="90%" value="90"/>
                <option label="100%" value="100"/>
            </options>
        </param>
        <param field="Mode4" label="Room Plan Name" width="200px" required="false" default="Google"/>
        <param field="Mode5" label="Preferred Audio App" width="150px">
            <options>
                <option label="Spotify" value="Spotify" default="true"/>
                <option label="Youtube" value="Youtube"/>
                <option label="None" value="" />
            </options>
        </param>
        <param field="Mode6" label="Debug" width="150px">
            <options>
                <option label="None" value="0" default="true"/>
                <option label="Plugin Debug" value="2"/>
                <option label="All" value="1"/>
            </options>
        </param>
    </params>
</plugin>
"""
import Domoticz  # type: ignore

import sys
import os
import threading as _threading
import time
import json
import queue as _queue
import urllib.parse
import pychromecast
import pychromecast.config as Consts
try:
    from gtts import gTTS
    voiceEnabled = True
except Exception as err:
    voiceEnabled = False
    voiceError = str(err)

KB_TO_XMIT = 1024 * 16

DEV_STATUS  = "-1"
DEV_VOLUME  = "-2"
DEV_PLAYING = "-3"
DEV_SOURCE  = "-4"

APP_NONE = 0
APP_OTHER = 40
Apps = {'Backdrop': Consts.APP_BACKDROP, 'Spotify': 'CC32E753', 'Youtube': Consts.APP_YOUTUBE, 'Other': ''}

AUDIO_MODELS = ["Google Home", "Google Home Mini", "Google Nest Mini", "Google Nest Hub",
                "Google Nest Audio", "Nest Audio", "Home Mini", "Google Cast Group",
                "Lenovo Smart Clock"]

langOverride = {}

_domoticz_port = None

def get_domoticz_http_port():
    try:
        with open("/proc/self/cmdline", "rb") as f:
            args = [a.decode() for a in f.read().split(b'\x00') if a]
        for i, arg in enumerate(args):
            if arg == "-www" and i + 1 < len(args):
                return int(args[i + 1])
    except Exception:
        pass
    return None

def is_audio_device(model_name):
    if model_name is None:
        return False
    model_lower = model_name.lower()
    for audio_model in AUDIO_MODELS:
        if audio_model.lower() in model_lower:
            return True
    return False


class RoomPlanManager:
    def __init__(self):
        self.conn = None
        self.plan_name = ""
        self.state = "IDLE"
        self.plan_idx = None
        self.plan_device_set = set()
        self.pending_add = []

    def start(self, plan_name, port, created_device_idxs):
        self.plan_name = plan_name
        self.pending_add = [str(x) for x in created_device_idxs if x is not None]
        if not self.pending_add or not self.plan_name or not port:
            return
        self.conn = Domoticz.Connection(
            Name="DomoticzPlanHTTP", Transport="TCP/IP", Protocol="HTTP",
            Address="127.0.0.1", Port=str(port)
        )
        self.state = "GET_PLANS"
        self.conn.Connect()

    def on_connect(self, status, description):
        if status != 0:
            Domoticz.Error(f"PlanHTTP connect failed: {description}")
            self.state = "ERROR"
            return
        self._send_next()

    def on_message(self, data):
        try:
            raw = data.get("Data", b"") if isinstance(data, dict) else data
            obj = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except Exception as e:
            Domoticz.Error(f"PlanHTTP invalid JSON: {e}")
            self.state = "ERROR"
            return
        self._handle_response(obj)
        self._send_next()

    def _send_api(self, params):
        qs = urllib.parse.urlencode(params)
        self.conn.Send({"Verb": "GET", "URL": f"/json.htm?{qs}",
                        "Headers": {"Host": "127.0.0.1", "Accept": "application/json",
                                    "Connection": "keep-alive"}})

    def _send_next(self):
        if self.state in ("IDLE", "DONE", "ERROR"):
            return

        if self.state in ("GET_PLANS", "GET_PLANS_AFTER_CREATE"):
            self._send_api({"type": "command", "param": "getplans", "order": "name", "used": "true"})
        elif self.state == "ADD_PLAN":
            self._send_api({"type": "command", "param": "addplan", "name": self.plan_name})
        elif self.state == "GET_PLAN_DEVICES":
            self._send_api({"type": "command", "param": "getplandevices", "idx": int(self.plan_idx)})
        elif self.state == "ADD_DEVICE_NEXT":
            self._add_next_device()

    def _add_next_device(self):
        while self.pending_add:
            dev_idx = self.pending_add.pop(0)
            if dev_idx in self.plan_device_set:
                Domoticz.Debug(f"Device IDX {dev_idx} already in plan - skipping")
                continue
            Domoticz.Log(f"Adding device IDX {dev_idx} to plan IDX {self.plan_idx}...")
            self._send_api({"type": "command", "param": "addplanactivedevice",
                            "activeidx": int(dev_idx), "activetype": 0, "idx": int(self.plan_idx)})
            return
        self.state = "DONE"
        Domoticz.Log(f"Room plan '{self.plan_name}' sync complete.")
        if self.conn is not None:
            self.conn.Disconnect()
            self.conn = None

    def _handle_response(self, obj):
        if obj.get("status") != "OK" and self.state != "GET_PLAN_DEVICES":
            Domoticz.Error(f"PlanHTTP API error in state {self.state}: {obj}")
            self.state = "ERROR"
            return

        if self.state in ("GET_PLANS", "GET_PLANS_AFTER_CREATE"):
            found = None
            for p in obj.get("result", []) or []:
                if p.get("Name") == self.plan_name:
                    found = p.get("idx")
                    break
            if found:
                Domoticz.Log(f"Found room plan '{self.plan_name}' with IDX: {found}")
                self.plan_idx = found
                self.state = "GET_PLAN_DEVICES"
            elif self.state == "GET_PLANS":
                Domoticz.Log(f"Room plan '{self.plan_name}' not found. Creating it...")
                self.state = "ADD_PLAN"
            else:
                Domoticz.Error(f"Created plan '{self.plan_name}' but failed to find its IDX.")
                self.state = "ERROR"

        elif self.state == "ADD_PLAN":
            Domoticz.Log(f"Room plan '{self.plan_name}' created. Re-fetching IDX...")
            self.state = "GET_PLANS_AFTER_CREATE"

        elif self.state == "GET_PLAN_DEVICES":
            self.plan_device_set = set()
            for d in obj.get("result", []) or []:
                devidx = d.get("devidx")
                if devidx is not None:
                    self.plan_device_set.add(str(devidx))
            self.state = "ADD_DEVICE_NEXT"

        elif self.state == "ADD_DEVICE_NEXT":
            pass


class GoogleDevice:
    def __init__(self, googleDevice):
        self.Name = googleDevice.name
        self.Model = googleDevice.model_name
        self.UUID = str(googleDevice.uuid)
        self.GoogleDevice = googleDevice
        self.Ready = False
        self.Active = False
        self.LogToFile("Google device created: " + str(self))
        self.State = {}

        googleDevice.register_status_listener(self.CastStatusListener(self))
        googleDevice.media_controller.register_status_listener(self.MediaStatusListener(self))
        googleDevice.register_connection_listener(self.ConnectionListener(self))
        googleDevice.start()

    class CastStatusListener:
        def __init__(self, parent):
            self.parent = parent

        def new_cast_status(self, status):
            global Apps
            try:
                if status is None:
                    return

                self.parent.LogToFile(status)
                self.parent.Ready = True

                for Unit in list(Devices):
                    if Devices[Unit].DeviceID.find(self.parent.UUID + DEV_STATUS) >= 0:
                        if status.display_name is None or status.display_name == 'Backdrop':
                            self.parent.Active = False
                            nValue = 9
                            sValue = 'Screensaver'
                            UpdateDevice(Unit, nValue, sValue, Devices[Unit].TimedOut)
                        else:
                            UpdateDevice(Unit, Devices[Unit].nValue, status.display_name, Devices[Unit].TimedOut)

                    elif Devices[Unit].DeviceID.find(self.parent.UUID + DEV_VOLUME) >= 0:
                        nValue = 2
                        if status.volume_muted:
                            nValue = 0
                        sValue = int(status.volume_level * 100)
                        UpdateDevice(Unit, nValue, str(sValue), Devices[Unit].TimedOut)

                    elif Devices[Unit].DeviceID.find(self.parent.UUID + DEV_SOURCE) >= 0:
                        nValue = sValue = APP_NONE
                        if status.display_name is not None and status.app_id != Consts.APP_BACKDROP:
                            level_names = Devices[Unit].Options['LevelNames'].split("|")
                            if status.display_name not in level_names:
                                nValue = sValue = len(level_names) * 10
                                Devices[Unit].Options['LevelNames'] = Devices[Unit].Options['LevelNames'] + "|" + status.display_name
                                Devices[Unit].Update(nValue, str(sValue), Options=Devices[Unit].Options)

                                seenApps = getConfigItem("Apps", Apps)
                                if status.display_name not in seenApps:
                                    seenApps[status.display_name] = status.app_id
                                    setConfigItem("Apps", seenApps)
                            else:
                                for i, level in enumerate(level_names):
                                    if level == status.display_name:
                                        nValue = sValue = i * 10
                                        break

                        UpdateDevice(Unit, nValue, str(sValue), Devices[Unit].TimedOut)

            except RuntimeError:
                pass
            except Exception as err:
                Domoticz.Error(f"new_cast_status: {err}")
                exc_type, exc_obj, exc_tb = sys.exc_info()
                fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
                Domoticz.Error(f"{exc_type}, {fname}, Line: {exc_tb.tb_lineno}")
                Domoticz.Error(str(status))

    class MediaStatusListener:
        def __init__(self, parent):
            self.parent = parent

        def new_media_status(self, status):
            try:
                if status is None:
                    return

                self.parent.LogToFile(status)
                self.parent.Ready = True

                for Unit in list(Devices):
                    if Devices[Unit].DeviceID.find(self.parent.UUID) >= 0:
                        nValue = Devices[Unit].nValue
                        sValue = Devices[Unit].sValue
                        if Devices[Unit].DeviceID.find(self.parent.UUID + DEV_STATUS) >= 0:
                            liveStream = ""
                            if status.stream_type_is_live:
                                liveStream = "[Live] "
                            if status.media_is_generic:
                                nValue = 4
                                sValue = liveStream + stringOrBlank(status.title)
                            elif status.media_is_tvshow:
                                nValue = 4
                                sValue = liveStream + stringOrBlank(status.series_title) + "[S" + stringOrBlank(status.season) + ":E" + stringOrBlank(status.episode) + "] " + stringOrBlank(status.title)
                            elif status.media_is_movie:
                                nValue = 4
                                sValue = liveStream + stringOrBlank(status.title)
                            elif status.media_is_photo:
                                nValue = 6
                                sValue = stringOrBlank(status.title)
                            elif status.media_is_musictrack:
                                nValue = 5
                                sValue = liveStream + stringOrBlank(status.artist) + " (" + stringOrBlank(status.album_name) + ") " + stringOrBlank(status.title)

                            if status.player_is_paused:
                                nValue = 2

                            sValue = sValue.lstrip(":")
                            sValue = sValue.rstrip(", :")
                            sValue = sValue.replace("()", "")
                            sValue = sValue.replace("[] ", "")
                            sValue = sValue.replace("[S:E] ", "")
                            sValue = sValue.replace("  ", " ")
                            sValue = sValue.replace(", :", ":")
                            sValue = sValue.replace(", (", " (")
                            if len(sValue) > 40:
                                sValue = sValue.replace(", ", ",")
                            if len(sValue) > 40:
                                sValue = sValue.replace(" (", "(")
                            if len(sValue) > 40:
                                sValue = sValue.replace(") ", ")")
                            if len(sValue) > 40:
                                sValue = sValue.replace(": ", ":")
                            if len(sValue) > 40:
                                sValue = sValue.replace(" [", "[")
                            if len(sValue) > 40:
                                sValue = sValue.replace("] ", "]")
                            sValue = sValue.replace(",(", "(")
                            sValue = sValue.strip()
                            if len(sValue) == 0:
                                sValue = Devices[Unit].sValue
                            UpdateDevice(Unit, nValue, str(sValue), Devices[Unit].TimedOut)

                        elif Devices[Unit].DeviceID.find(self.parent.UUID + DEV_PLAYING) >= 0:
                            if status.duration is None or status.current_time is None:
                                sValue = '0'
                            else:
                                try:
                                    sValue = str(int((status.adjusted_current_time / status.duration) * 100))
                                except ZeroDivisionError:
                                    sValue = '0'
                                except TypeError:
                                    sValue = '0'
                            if status.player_is_playing:
                                nValue = 2
                                if sValue == '0':
                                    sValue = '1'
                            elif status.player_is_paused:
                                nValue = 0
                                if sValue == '0':
                                    sValue = '1'
                            else:
                                nValue = 0
                                sValue = '0'
                            UpdateDevice(Unit, nValue, str(sValue), Devices[Unit].TimedOut)

            except RuntimeError:
                pass
            except Exception as err:
                Domoticz.Error(f"new_media_status: {err}")
                exc_type, exc_obj, exc_tb = sys.exc_info()
                fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
                Domoticz.Error(f"{exc_type}, {fname}, Line: {exc_tb.tb_lineno}")
                Domoticz.Error(str(status))

    class ConnectionListener:
        def __init__(self, parent):
            self.parent = parent

        def new_connection_status(self, new_status):
            try:
                self.parent.LogToFile(new_status)
                Domoticz.Status(self.parent.Name + " is now: " + str(new_status))
                if new_status.status in ("DISCONNECTED", "LOST", "FAILED"):
                    self.parent.Ready = False
                    self.parent.Active = False

                for Unit in list(Devices):
                    if Devices[Unit].DeviceID.find(self.parent.UUID) >= 0:
                        UpdateDevice(Unit, Devices[Unit].nValue, Devices[Unit].sValue, (1, 0)[new_status.status == "CONNECTED"])

            except Exception as err:
                Domoticz.Error(f"new_connection_status: {err}")
                Domoticz.Error(f"new_connection_status: {new_status}")

    def LogToFile(self, status):
        if Parameters["Mode6"] != "0" and status is not None:
            log_path = os.path.join(Parameters["HomeFolder"], "Messages.log")
            print(time.strftime('%Y-%m-%d %H:%M:%S') + " [" + self.Name + "] " + str(status), file=open(log_path, "a"))

    @property
    def VolumeUnit(self):
        for Unit in Devices:
            if Devices[Unit].DeviceID == self.UUID + DEV_VOLUME:
                return Unit
        return None

    @property
    def PlayingUnit(self):
        for Unit in Devices:
            if Devices[Unit].DeviceID == self.UUID + DEV_PLAYING:
                return Unit
        return None

    def UpdatePlaying(self):
        if self.GoogleDevice.media_controller.status is not None and self.GoogleDevice.media_controller.status.duration is not None:
            if self.GoogleDevice.media_controller.status.player_is_playing:
                try:
                    sValue = str(int((self.GoogleDevice.media_controller.status.adjusted_current_time / self.GoogleDevice.media_controller.status.duration) * 100))
                    Unit = self.PlayingUnit
                    if Unit is not None:
                        UpdateDevice(Unit, Devices[Unit].nValue, str(sValue), Devices[Unit].TimedOut)
                except ZeroDivisionError:
                    pass
                except TypeError:
                    pass
                except Exception as err:
                    Domoticz.Error(f"UpdatePlaying: {err}")
                    exc_type, exc_obj, exc_tb = sys.exc_info()
                    fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
                    Domoticz.Error(f"{exc_type}, {fname}, Line: {exc_tb.tb_lineno}")

    def StoreState(self):
        self.State.clear()
        if self.GoogleDevice.status is not None:
            self.State['Volume'] = self.GoogleDevice.status.volume_level
            self.State['Muted'] = self.GoogleDevice.status.volume_muted
            self.State['App'] = self.GoogleDevice.app_id
        if self.GoogleDevice.media_controller.status is not None:
            self.State['SupportsSeek'] = self.GoogleDevice.media_controller.status.supports_seek

        self.GoogleDevice.quit_app()
        self.GoogleDevice.set_volume(int(Parameters["Mode3"]) / 100)
        self.GoogleDevice.set_volume_muted(False)

    def RestoreState(self, stop_event=None):
        if self.State.get('Volume') is not None:
            waited = 0
            while not self.Ready and waited < 10:
                Domoticz.Debug(f"RestoreState: Waiting for '{self.Name}' to reconnect...")
                if stop_event is not None:
                    stop_event.wait(1.0)
                    if stop_event.is_set():
                        return
                else:
                    time.sleep(1.0)
                waited += 1
            if not self.Ready:
                Domoticz.Error(f"RestoreState: '{self.Name}' did not reconnect in time, state not restored.")
                return
            try:
                self.GoogleDevice.quit_app()
            except Exception as err:
                Domoticz.Error(f"RestoreState: Failed to quit app: {err}")
            try:
                if 'Volume' in self.State:
                    self.GoogleDevice.set_volume(self.State['Volume'])
                if 'Muted' in self.State:
                    self.GoogleDevice.set_volume_muted(self.State['Muted'])
            except Exception as err:
                Domoticz.Error(f"RestoreState: Failed to restore volume: {err}")
        else:
            Domoticz.Log("No device state to restore after notification")

    def __str__(self):
        return f"'{self.Name}', Model: '{self.Model}', UUID: '{self.UUID}'"


class BasePlugin:

    def __init__(self):
        global voiceEnabled
        self.googleDevices = {}
        self.castBrowser = None
        self.messageServer = None
        self.messageQueue = None
        self.messageThread = None
        self.stop_event = _threading.Event()
        self.appPrefs = {}
        self.planMgr = RoomPlanManager()
        if voiceEnabled:
            self.messageQueue = _queue.Queue()
            self.messageThread = _threading.Thread(name="GoogleNotify", target=BasePlugin.handleMessage, args=(self,))

    def handleMessage(self):
        global voiceEnabled
        Domoticz.Debug("handleMessage: Entering notification handler")
        ipAddress = GetIP()
        ipPort = Parameters["Port"]

        if len(ipAddress) > 0:
            Domoticz.Log(f"Notifications will use IP Address: {ipAddress}:{ipPort} to serve audio media.")
            self.messageServer = Domoticz.Connection(Name="Message Server", Transport="TCP/IP", Protocol="HTTP", Port=ipPort)
            self.messageServer.Listen()
        else:
            Domoticz.Error("Unable to determine host external IP address: Voice notifications will not be enabled")
            voiceEnabled = False

        while voiceEnabled and not self.stop_event.is_set():
            try:
                try:
                    Message = self.messageQueue.get(timeout=1.0)
                except _queue.Empty:
                    continue
                if Message is None:
                    self.messageQueue.task_done()
                    break

                messagesDir = os.path.join(Parameters['HomeFolder'], 'Messages')
                if not os.path.exists(messagesDir):
                    os.mkdir(messagesDir)
                Domoticz.Debug(f"handleMessage: '{Message['Text']}', to be sent to '{Message['Target']}'")

                for uuid in list(self.googleDevices):
                    if self.stop_event.is_set():
                        break
                    if self.googleDevices[uuid].GoogleDevice.name == Message["Target"]:
                        if self.googleDevices[uuid].GoogleDevice.status is not None and self.googleDevices[uuid].GoogleDevice.status.volume_muted:
                            Domoticz.Log(f"Device '{Message['Target']}' is muted, notification skipped.")
                            break
                        if self.googleDevices[uuid].Ready:
                            language = Parameters.get("Mode2", "").strip()
                            if not language:
                                language = Parameters["Language"]
                            if language in langOverride:
                                language = langOverride[language]
                            Domoticz.Debug(f"handleMessage: TTS language='{language}'")
                            tts = gTTS(Message["Text"], lang=language)
                            messageFileName = os.path.join(messagesDir, uuid + '.mp3')
                            tts.save(messageFileName)
                            if not os.path.exists(messageFileName):
                                Domoticz.Error(f"'{messageFileName}' not found, translation must have failed.")
                                break
                            else:
                                Domoticz.Debug(f"'{messageFileName}' created, {os.path.getsize(messageFileName)} bytes")

                            self.googleDevices[uuid].StoreState()
                            mc = self.googleDevices[uuid].GoogleDevice.media_controller
                            cacheBuster = str(int(time.time() * 1000))
                            mediaUrl = f"http://{ipAddress}:{ipPort}/{uuid}.mp3?t={cacheBuster}"
                            fileSize = os.path.getsize(messageFileName)
                            estimatedDuration = fileSize * 8 / 64000
                            mc.play_media(mediaUrl, 'audio/mpeg')
                            mc.block_until_active(timeout=10)
                            self.stop_event.wait(1.5)
                            if self.stop_event.is_set():
                                break
                            sawPlaying = False
                            durationSet = False
                            endTime = time.time() + max(15, estimatedDuration + 10)
                            playbackCompleted = False
                            while time.time() < endTime and not self.stop_event.is_set():
                                mc.update_status()
                                self.stop_event.wait(0.5)
                                if self.stop_event.is_set():
                                    break
                                if mc.status.player_is_playing or mc.status.player_is_paused:
                                    sawPlaying = True
                                if sawPlaying and not durationSet and mc.status.duration is not None:
                                    endTime = time.time() + mc.status.duration + 5
                                    durationSet = True
                                if sawPlaying and mc.status.player_is_idle:
                                    playbackCompleted = True
                                    break
                                if sawPlaying:
                                    if mc.status.duration is not None:
                                        Domoticz.Debug(f"Playing ({str(mc.status.adjusted_current_time)[:4]} of {mc.status.duration}, timeout in {str(endTime - time.time())[:4]} seconds)")
                                    else:
                                        Domoticz.Debug(f"Playing (unknown duration, timeout in {str(endTime - time.time())[:4]} seconds)")
                                else:
                                    Domoticz.Debug(f"Waiting for player to start (timeout in {str(endTime - time.time())[:4]} seconds)")
                            if not self.stop_event.is_set():
                                self.stop_event.wait(2.0)
                            self.googleDevices[uuid].RestoreState(self.stop_event)

                            if playbackCompleted:
                                Domoticz.Log(f"Notification sent to '{Message['Target']}' completed")
                                try:
                                    os.remove(messageFileName)
                                except OSError:
                                    pass
                            else:
                                Domoticz.Error(f"Notification sent to '{Message['Target']}' timed out")
                        else:
                            Domoticz.Error(f"Google device '{Message['Target']}' is not connected, ignored.")

            except Exception as err:
                Domoticz.Error(f"handleMessage: {err}")
                exc_type, exc_obj, exc_tb = sys.exc_info()
                fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
                Domoticz.Error(f"{exc_type}, {fname}, Line: {exc_tb.tb_lineno}")
            self.messageQueue.task_done()

        Domoticz.Debug("handleMessage: Exiting notification handler")

    def discoveryCallback(self, googleDevice):
        try:
            if not is_audio_device(googleDevice.model_name):
                Domoticz.Debug(f"Ignoring non-audio device: '{googleDevice.name}' (model: '{googleDevice.model_name}')")
                return

            uuid = str(googleDevice.uuid)
            if uuid in self.googleDevices:
                self.googleDevices[uuid].GoogleDevice.disconnect()
                self.googleDevices[uuid].GoogleDevice = None
                del self.googleDevices[uuid]

            self.googleDevices[uuid] = GoogleDevice(googleDevice)

            createDomoticzDevice = True
            maxUnitNo = 1
            for Device in Devices:
                if Devices[Device].Unit > maxUnitNo:
                    maxUnitNo = Devices[Device].Unit
                if Devices[Device].DeviceID.find(uuid) >= 0:
                    createDomoticzDevice = False
                    if self.googleDevices[uuid].Name not in Devices[Device].Name:
                        Domoticz.Log(f"Device name mismatch: '{self.googleDevices[uuid].Name}' vs '{Devices[Device].Name}'")

            if createDomoticzDevice:
                logoType = Parameters['Key'] + 'HomeMini'
                Domoticz.Log(f"Creating devices for '{googleDevice.name}' of type '{googleDevice.model_name}' in Domoticz, look in Devices tab.")
                Domoticz.Device(Name=self.googleDevices[uuid].Name + " Status", Unit=maxUnitNo + 1, Type=17, Switchtype=17, Image=Images[logoType].ID, DeviceID=uuid + DEV_STATUS, Description=googleDevice.model_name, Used=1).Create()
                Domoticz.Device(Name=self.googleDevices[uuid].Name + " Volume", Unit=maxUnitNo + 2, Type=244, Subtype=73, Switchtype=7, Image=8, DeviceID=uuid + DEV_VOLUME, Description=googleDevice.model_name, Used=1).Create()
                Domoticz.Device(Name=self.googleDevices[uuid].Name + " Playing", Unit=maxUnitNo + 3, Type=244, Subtype=73, Switchtype=7, Image=12, DeviceID=uuid + DEV_PLAYING, Description=googleDevice.model_name, Used=1).Create()

        except Exception as err:
            Domoticz.Error(f"discoveryCallback: {err}")
            exc_type, exc_obj, exc_tb = sys.exc_info()
            fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
            Domoticz.Error(f"{exc_type}: {fname} at {exc_tb.tb_lineno}")

    def onStart(self):
        global _domoticz_port

        if Parameters["Mode6"] != "0":
            Domoticz.Debugging(int(Parameters["Mode6"]))
            DumpConfigToLog()

        audioApp = Parameters.get("Mode5", "Spotify").strip()
        self.appPrefs = {"Audio": audioApp}

        if Parameters['Key'] + 'HomeMini' not in Images:
            Domoticz.Image('GoogleHomeMini.zip').Create()

        for Device in Devices:
            UpdateDevice(Device, Devices[Device].nValue, Devices[Device].sValue, 1)

        Domoticz.Notifier("Google_Devices")

        self.castBrowser = pychromecast.get_chromecasts(callback=self.discoveryCallback, blocking=False)

        if voiceEnabled:
            self.messageThread.start()
        else:
            Domoticz.Error(f"'gtts' module import error: {voiceError}: Voice notifications will not be enabled")

        domoticz_http_port = get_domoticz_http_port()
        if domoticz_http_port is not None:
            Domoticz.Log(f"Domoticz detected HTTP Port: {domoticz_http_port}")
            _domoticz_port = domoticz_http_port
        else:
            Domoticz.Error("Failed to detect Domoticz HTTP Port")

    def onMessage(self, Connection, Data):
        if Connection.Name == "DomoticzPlanHTTP":
            self.planMgr.on_message(Data)
            return

        connectionOkay = False
        try:
            if Connection.Parent == self.messageServer:
                connectionOkay = True
        except AttributeError:
            Domoticz.Error("Please upgrade to the latest beta!")
            connectionOkay = True

        if connectionOkay:
            messageFile = None
            try:
                headerCode = "200 OK"
                if 'Verb' not in Data:
                    Domoticz.Error("Invalid web request received, no Verb present")
                    headerCode = "400 Bad Request"
                elif Data['Verb'] != 'GET':
                    Domoticz.Error(f"Invalid web request received, only GET requests allowed ({Data['Verb']})")
                    headerCode = "405 Method Not Allowed"
                elif 'URL' not in Data:
                    Domoticz.Error("Invalid web request received, no URL present")
                    headerCode = "400 Bad Request"
                elif 'Headers' not in Data:
                    Domoticz.Error("Invalid web request received, no Headers present")
                    headerCode = "400 Bad Request"
                else:
                    messagesDir = os.path.join(Parameters['HomeFolder'], 'Messages')
                    urlPath = Data['URL'].split('?')[0]
                    filePath = os.path.join(messagesDir, urlPath.lstrip('/'))
                    if not os.path.exists(filePath):
                        Domoticz.Error(f"Invalid web request received, file '{filePath}' does not exist")
                        headerCode = "404 File Not Found"

                if headerCode != "200 OK":
                    DumpHTTPResponseToLog(Data)
                    Connection.Send({"Status": headerCode})
                else:
                    messageFileSize = os.path.getsize(filePath)
                    noCacheHeaders = {
                        "Content-Type": "audio/mpeg",
                        "Accept-Ranges": "bytes",
                        "Cache-Control": "no-store, no-cache, must-revalidate",
                        "Pragma": "no-cache",
                        "Expires": "0",
                    }
                    hasRange = 'Headers' in Data and 'Range' in Data.get('Headers', {})
                    if hasRange:
                        rangeHeader = Data['Headers']['Range']
                        rangeSpec = rangeHeader[rangeHeader.find('=') + 1:]
                        parts = rangeSpec.split('-', 1)
                        fileStartPosition = int(parts[0]) if parts[0] else 0
                        if parts[1]:
                            fileEndPosition = min(int(parts[1]), messageFileSize - 1)
                        else:
                            fileEndPosition = min(fileStartPosition + KB_TO_XMIT - 1, messageFileSize - 1)
                        chunkSize = fileEndPosition - fileStartPosition + 1
                        messageFile = open(filePath, mode='rb')
                        messageFile.seek(fileStartPosition)
                        fileContent = messageFile.read(chunkSize)
                        Domoticz.Debug(f"{Connection.Address}:{Connection.Port} Sent 'GET' request file '{urlPath}' range {fileStartPosition}-{fileEndPosition}/{messageFileSize}, {len(fileContent)} bytes")
                        noCacheHeaders["Content-Range"] = f"bytes {fileStartPosition}-{fileEndPosition}/{messageFileSize}"
                        noCacheHeaders["Content-Length"] = str(len(fileContent))
                        Connection.Send({"Status": "206 Partial Content", "Headers": noCacheHeaders, "Data": fileContent})
                    else:
                        messageFile = open(filePath, mode='rb')
                        fileContent = messageFile.read()
                        Domoticz.Debug(f"{Connection.Address}:{Connection.Port} Sent 'GET' request file '{urlPath}' full, {messageFileSize} bytes")
                        noCacheHeaders["Content-Length"] = str(messageFileSize)
                        Connection.Send({"Status": "200 OK", "Headers": noCacheHeaders, "Data": fileContent})

            except Exception as inst:
                Domoticz.Error(f"Exception detail: '{inst}'")
                DumpHTTPResponseToLog(Data)

            if messageFile is not None:
                messageFile.close()
        else:
            Domoticz.Error(f"Message from unknown connection: {Connection}")

    def onCommand(self, Unit, Command, Level, Hue):
        global Apps
        Domoticz.Log(f"onCommand called for Unit {Unit}: Parameter '{Command}', Level: {Level}")

        Command = Command.strip()
        action, sep, params = Command.partition(' ')
        action = action.capitalize()

        uuid = Devices[Unit].DeviceID[:-2]
        subUnit = Devices[Unit].DeviceID[-2:]
        Domoticz.Debug(f"UUID: {uuid}, sub unit: {subUnit}, Action: {action}, params: {params}")

        if action == 'On':
            if subUnit == DEV_VOLUME:
                self.googleDevices[uuid].GoogleDevice.set_volume_muted(False)
            elif subUnit == DEV_PLAYING:
                self.googleDevices[uuid].GoogleDevice.media_controller.play()
        elif action == 'Off':
            if subUnit == DEV_VOLUME:
                self.googleDevices[uuid].GoogleDevice.set_volume_muted(True)
            elif subUnit == DEV_PLAYING:
                self.googleDevices[uuid].GoogleDevice.media_controller.pause()
            elif subUnit == DEV_SOURCE:
                self.googleDevices[uuid].GoogleDevice.quit_app()
        elif action == 'Set':
            if params.capitalize() == 'Level' or Command.lower() == 'volume':
                if subUnit == DEV_VOLUME:
                    currentVolume = self.googleDevices[uuid].GoogleDevice.status.volume_level
                    newVolume = Level / 100
                    if currentVolume > newVolume:
                        self.googleDevices[uuid].GoogleDevice.volume_down(currentVolume - newVolume)
                    else:
                        self.googleDevices[uuid].GoogleDevice.volume_up(newVolume - currentVolume)
                elif subUnit == DEV_PLAYING:
                    if self.googleDevices[uuid].GoogleDevice.media_controller.status.duration is not None:
                        newPosition = self.googleDevices[uuid].GoogleDevice.media_controller.status.duration * (Level / 100)
                        self.googleDevices[uuid].GoogleDevice.media_controller.seek(newPosition)
                    else:
                        Domoticz.Log(f"[{self.googleDevices[uuid].Name}] No duration found, seeking is not possible at this time.")
                elif subUnit == DEV_SOURCE:
                    seenApps = getConfigItem("Apps", Apps)
                    for i, appName in enumerate(Devices[Unit].Options['LevelNames'].split("|")):
                        if i * 10 == Level:
                            if seenApps[appName] != '':
                                self.googleDevices[uuid].GoogleDevice.start_app(seenApps[appName])
                            break

        elif action == 'Rewind':
            self.googleDevices[uuid].GoogleDevice.media_controller.seek(0.0)
        elif action in ('Play', 'Playing'):
            self.googleDevices[uuid].GoogleDevice.media_controller.play()
        elif action in ('Pause', 'Paused'):
            self.googleDevices[uuid].GoogleDevice.media_controller.pause()
        elif action == 'Trigger':
            pass
        elif action == 'Audio':
            audioApp = self.appPrefs.get("Audio", "")
            if audioApp and self.googleDevices[uuid].GoogleDevice.app_display_name != audioApp:
                self.googleDevices[uuid].GoogleDevice.quit_app()
                seenApps = getConfigItem("Apps", Apps)
                if audioApp in seenApps:
                    self.googleDevices[uuid].GoogleDevice.start_app(seenApps[audioApp])
        elif action == 'Sendnotification':
            if self.messageQueue is not None:
                self.messageQueue.put({"Target": self.googleDevices[uuid].GoogleDevice.device.friendly_name, "Text": params})
            else:
                Domoticz.Error("Message queue not initialized, notification ignored.")
        elif action == 'Quit':
            self.googleDevices[uuid].GoogleDevice.quit_app()

    def onHeartbeat(self):
        for uuid in list(self.googleDevices):
            self.googleDevices[uuid].UpdatePlaying()

        if not hasattr(self, '_plan_triggered') and _domoticz_port:
            created_device_idxs = []
            for Unit in Devices:
                created_device_idxs.append(Devices[Unit].ID)
            room_plan_name = Parameters.get("Mode4", "Google").strip() or "Google"
            if created_device_idxs:
                self.planMgr.start(room_plan_name, _domoticz_port, created_device_idxs)
            self._plan_triggered = True

    def onNotification(self, Name, Subject, Text, Status, Priority, Sound, ImageFile):
        Domoticz.Debug(f"onNotification: {Name},{Subject},{Text},{Status},{Priority},{Sound},{ImageFile}")
        if Parameters["Mode1"] == "":
            Domoticz.Error("Voice Device/Group not configured (Mode1 is empty), notification ignored.")
            return
        if self.messageQueue is not None:
            self.messageQueue.put({"Target": Parameters['Mode1'], "Text": Text})
        else:
            Domoticz.Error("Message queue not initialized, notification ignored.")

    def onConnect(self, Connection, Status, Description):
        if Connection.Name == "DomoticzPlanHTTP":
            self.planMgr.on_connect(Status, Description)
            return
        Domoticz.Debug(f"{Connection.Address}:{Connection.Port} Connection established")

    def onDisconnect(self, Connection):
        Domoticz.Debug(f"{Connection.Address}:{Connection.Port} Connection disconnected")

    def onStop(self):
        self.stop_event.set()

        if self.messageQueue is not None:
            Domoticz.Log(f"Clearing notification queue (approximate size {self.messageQueue.qsize()} entries)...")
            self.messageQueue.put(None)

        if self.castBrowser is not None:
            Domoticz.Log("Zeroconf Discovery Stopping...")
            try:
                self.castBrowser.stop_discovery()
            except Exception as err:
                Domoticz.Error(f"onStop stop_discovery: {err}")
            self.castBrowser = None

        for uuid in list(self.googleDevices):
            try:
                Domoticz.Log(f"{self.googleDevices[uuid].Name} Disconnecting...")
                self.googleDevices[uuid].GoogleDevice.disconnect(timeout=5)
            except Exception as err:
                Domoticz.Error(f"onStop disconnect: {err}")
        self.googleDevices.clear()

        if self.messageQueue is not None:
            Domoticz.Log("Waiting for notification queue to drain...")
            self.messageQueue.join()

        if self.messageThread is not None and self.messageThread.is_alive():
            self.messageThread.join(timeout=5.0)
            if self.messageThread.is_alive():
                Domoticz.Error("GoogleNotify thread did not stop in time")
        self.messageThread = None

        plugin_threads = [t for t in _threading.enumerate()
                          if t is not _threading.current_thread() and t.name != "MainThread"]
        Domoticz.Log(f"Plugin threads still active: {len(plugin_threads)}, should be 0.")
        max_wait = 30
        waited = 0
        while waited < max_wait:
            plugin_threads = [t for t in _threading.enumerate()
                              if t is not _threading.current_thread() and t.name != "MainThread"]
            if not plugin_threads:
                break
            for thread in plugin_threads:
                Domoticz.Log(f"'{thread.name}' is still running, waiting otherwise Domoticz will abort on plugin exit.")
            time.sleep(1.0)
            waited += 1

        plugin_threads = [t for t in _threading.enumerate()
                          if t is not _threading.current_thread() and t.name != "MainThread"]
        if plugin_threads:
            for thread in plugin_threads:
                Domoticz.Error(f"'{thread.name}' still alive after {max_wait}s timeout")
        else:
            Domoticz.Log("All threads stopped.")


global _plugin
_plugin = BasePlugin()

def onStart():
    global _plugin
    _plugin.onStart()

def onStop():
    global _plugin
    _plugin.onStop()

def onMessage(Connection, Data):
    global _plugin
    _plugin.onMessage(Connection, Data)

def onCommand(Unit, Command, Level, Hue):
    global _plugin
    _plugin.onCommand(Unit, Command, Level, Hue)

def onConnect(Connection, Status, Description):
    global _plugin
    _plugin.onConnect(Connection, Status, Description)

def onDisconnect(Connection):
    global _plugin
    _plugin.onDisconnect(Connection)

def onHeartbeat():
    global _plugin
    _plugin.onHeartbeat()

def onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile):
    global _plugin
    _plugin.onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile)


def GetIP():
    import socket
    IP = ''
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 1))
        IP = s.getsockname()[0]
        Domoticz.Debug(f"IP Address is: {IP}")
    except Exception as err:
        Domoticz.Debug(f"GetIP: {err}")
    finally:
        s.close()
    return str(IP)

def getConfigItem(Key=None, Default={}):
    Value = Default
    try:
        Config = Domoticz.Configuration()
        if Key is not None:
            Value = Config[Key]
        else:
            Value = Config
    except KeyError:
        Value = Default
    except Exception as inst:
        Domoticz.Error(f"Domoticz.Configuration read failed: '{inst}'")
    return Value

def setConfigItem(Key=None, Value=None):
    Config = {}
    try:
        Config = Domoticz.Configuration()
        if Key is not None:
            Config[Key] = Value
        else:
            Config = Value
        Domoticz.Configuration(Config)
    except Exception as inst:
        Domoticz.Error(f"Domoticz.Configuration operation failed: '{inst}'")
    return Config

def stringOrBlank(input):
    if input is None:
        return ""
    return str(input)

def DumpConfigToLog():
    for x in Parameters:
        if Parameters[x] != "":
            if x == "Password":
                Domoticz.Debug(f"'{x}':'***HIDDEN***'")
            else:
                Domoticz.Debug(f"'{x}':'{Parameters[x]}'")
    Domoticz.Debug(f"Device count: {len(Devices)}")
    for x in Devices:
        Domoticz.Debug(f"Device: {x} - {Devices[x]}")
        Domoticz.Debug(f"Device ID:       '{Devices[x].ID}'")
        Domoticz.Debug(f"Device Name:     '{Devices[x].Name}'")
        Domoticz.Debug(f"Device nValue:    {Devices[x].nValue}")
        Domoticz.Debug(f"Device sValue:   '{Devices[x].sValue}'")
        Domoticz.Debug(f"Device LastLevel: {Devices[x].LastLevel}")

def DumpHTTPResponseToLog(httpDict):
    if isinstance(httpDict, dict):
        Domoticz.Log(f"HTTP Details ({len(httpDict)}):")
        for x in httpDict:
            if isinstance(httpDict[x], dict):
                Domoticz.Log(f"--->'{x} ({len(httpDict[x])}):")
                for y in httpDict[x]:
                    Domoticz.Log(f"------->{y}':'{httpDict[x][y]}'")
            else:
                Domoticz.Log(f"--->'{x}':'{httpDict[x]}'")

def UpdateDevice(Unit, nValue, sValue, TimedOut):
    if Unit in Devices:
        if str(Devices[Unit].nValue) != str(nValue) or str(Devices[Unit].sValue) != str(sValue) or str(Devices[Unit].TimedOut) != str(TimedOut):
            Domoticz.Log(f"[{Devices[Unit].Name}] Update {nValue}({Devices[Unit].nValue}):'{sValue}'({Devices[Unit].sValue}): {TimedOut}({Devices[Unit].TimedOut})")
            Devices[Unit].Update(nValue=nValue, sValue=str(sValue), TimedOut=TimedOut)

def UpdateImage(Unit, Logo):
    if Unit in Devices and Logo in Images:
        if Devices[Unit].Image != Images[Logo].ID:
            Domoticz.Log(f"Device Image update: Currently {Devices[Unit].Image}, should be {Images[Logo].ID}")
            Devices[Unit].Update(nValue=Devices[Unit].nValue, sValue=str(Devices[Unit].sValue), Image=Images[Logo].ID)
