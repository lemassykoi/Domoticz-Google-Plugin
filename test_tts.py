#!/usr/bin/env python3
"""
Standalone TTS test for Google Home devices.
Tests MP3 generation, HTTP serving, and pychromecast playback
without the Domoticz framework.

Usage: python3 test_tts.py
"""
import os
import sys
import time
import threading
import http.server
import socketserver
from mutagen.mp3 import MP3

DEVICE_NAME = "Bureau"
TTS_TEXT = "Bonjour, ceci est un test de message long et un tout petit peu compliqué."
TTS_LANG = "fr"
VOLUME = 0.4
SERVE_PORT = 18080
MP3_DIR = "/tmp/tts_test"

def get_local_ip():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 1))
        return s.getsockname()[0]
    finally:
        s.close()

def generate_tts(text, lang, output_path):
    from gtts import gTTS
    tts = gTTS(text, lang=lang)
    tts.save(output_path)
    size = os.path.getsize(output_path)
    print(f"[TTS] Generated: {output_path} ({size} bytes)")
    return output_path

def get_mp3_info(path):
    try:
        audio = MP3(path)
        print(f"[MP3] Duration: {audio.info.length:.3f}s, Bitrate: {audio.info.bitrate}bps, Sample rate: {audio.info.sample_rate}Hz")
        return audio.info.length
    except Exception as e:
        print(f"[MP3] mutagen not available ({e}), using ffprobe...")
        import subprocess
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True
        )
        duration = float(result.stdout.strip())
        print(f"[MP3] Duration: {duration:.3f}s")
        return duration

class RangeHTTPHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler with proper Range request support."""

    def __init__(self, *args, directory=None, **kwargs):
        self.serve_directory = directory or MP3_DIR
        super().__init__(*args, directory=self.serve_directory, **kwargs)

    def do_GET(self):
        path = os.path.join(self.serve_directory, self.path.lstrip('/'))
        if not os.path.exists(path):
            self.send_error(404, "File not found")
            return

        file_size = os.path.getsize(path)
        range_header = self.headers.get('Range')

        if range_header:
            range_spec = range_header.replace('bytes=', '')
            start_str, end_str = range_spec.split('-')
            start = int(start_str) if start_str else 0
            end = int(end_str) if end_str else file_size - 1
            end = min(end, file_size - 1)
            content_length = end - start + 1

            with open(path, 'rb') as f:
                f.seek(start)
                data = f.read(content_length)

            self.send_response(206)
            self.send_header('Content-Type', 'audio/mpeg')
            self.send_header('Content-Range', f'bytes {start}-{end}/{file_size}')
            self.send_header('Content-Length', str(content_length))
            self.send_header('Accept-Ranges', 'bytes')
            self.end_headers()
            self.wfile.write(data)
            print(f"[HTTP] 206 bytes {start}-{end}/{file_size} ({content_length} bytes)")
        else:
            with open(path, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'audio/mpeg')
            self.send_header('Content-Length', str(file_size))
            self.send_header('Accept-Ranges', 'bytes')
            self.end_headers()
            self.wfile.write(data)
            print(f"[HTTP] 200 full file ({file_size} bytes)")

    def log_message(self, format, *args):
        pass

def start_http_server(port, directory):
    handler = lambda *args, **kwargs: RangeHTTPHandler(*args, directory=directory, **kwargs)
    server = socketserver.TCPServer(("", port), handler)
    server.allow_reuse_address = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"[HTTP] Server listening on port {port}")
    return server

def find_device(name):
    import pychromecast
    print(f"[CAST] Discovering devices (looking for '{name}')...")
    casts, browser = pychromecast.get_chromecasts(blocking=True, timeout=10)
    target = None
    for cast in casts:
        print(f"[CAST] Found: '{cast.name}' ({cast.model_name})")
        if cast.name == name:
            target = cast
    if target is None:
        browser.stop_discovery()
        return None, browser
    return target, browser

def play_and_monitor(cast, url, volume, expected_duration):
    cast.wait()
    print(f"[CAST] Connected to {cast.name}")

    old_volume = cast.status.volume_level
    old_muted = cast.status.volume_muted
    print(f"[CAST] Current volume: {old_volume:.0%}, muted: {old_muted}")

    cast.set_volume(volume)
    cast.set_volume_muted(False)
    print(f"[CAST] Volume set to {volume:.0%}")

    mc = cast.media_controller
    print(f"[CAST] Playing: {url}")
    mc.play_media(url, 'audio/mpeg')
    mc.block_until_active(timeout=10)

    time.sleep(1.0)
    mc.update_status()

    print(f"[CAST] Waiting for player to start...")
    wait_start = time.time()
    while mc.status.player_is_idle and time.time() - wait_start < 15:
        time.sleep(0.3)
        mc.update_status()

    print(f"[CAST] Player state: playing={mc.status.player_is_playing}, idle={mc.status.player_is_idle}")
    print(f"[CAST] Reported duration: {mc.status.duration}")

    play_start = time.time()
    last_position = 0
    while not mc.status.player_is_idle and time.time() - play_start < expected_duration + 10:
        mc.update_status()
        pos = mc.status.adjusted_current_time or 0
        dur = mc.status.duration or 0
        pct = (pos / dur * 100) if dur > 0 else 0
        print(f"[PLAY] {pos:.2f}s / {dur:.3f}s ({pct:.0f}%) state={mc.status.player_state}")
        last_position = pos
        time.sleep(0.5)

    idle_time = time.time()
    elapsed = idle_time - play_start
    print(f"[CAST] Player went idle after {elapsed:.2f}s (last reported position: {last_position:.2f}s)")
    print(f"[CAST] Expected duration: {expected_duration:.3f}s")
    print(f"[CAST] Gap: {expected_duration - last_position:.2f}s of audio not reported as played")

    print(f"[CAST] Waiting 3.0s for speaker buffer to flush...")
    time.sleep(3.0)

    cast.quit_app()
    cast.set_volume(old_volume)
    cast.set_volume_muted(old_muted)
    print(f"[CAST] Volume restored to {old_volume:.0%}, muted: {old_muted}")

    return last_position, expected_duration

def main():
    os.makedirs(MP3_DIR, exist_ok=True)
    mp3_path = os.path.join(MP3_DIR, "test.mp3")

    print("=" * 60)
    print("Google Home TTS Test")
    print("=" * 60)

    generate_tts(TTS_TEXT, TTS_LANG, mp3_path)
    expected_duration = get_mp3_info(mp3_path)

    ip = get_local_ip()
    print(f"[NET] Local IP: {ip}")

    server = start_http_server(SERVE_PORT, MP3_DIR)

    cast, browser = find_device(DEVICE_NAME)
    if cast is None:
        print(f"[ERROR] Device '{DEVICE_NAME}' not found")
        server.shutdown()
        browser.stop_discovery()
        return 1

    url = f"http://{ip}:{SERVE_PORT}/test.mp3"
    try:
        last_pos, expected = play_and_monitor(cast, url, VOLUME, expected_duration)
        print()
        print("=" * 60)
        print("RESULTS:")
        print(f"  MP3 file duration:      {expected:.3f}s")
        print(f"  Last reported position: {last_pos:.2f}s")
        print(f"  Unreported gap:         {expected - last_pos:.2f}s")
        if last_pos < expected - 0.5:
            print(f"  STATUS: Player went idle {expected - last_pos:.1f}s early")
            print(f"  The 3.0s post-idle wait should cover this gap")
        else:
            print(f"  STATUS: Playback position tracking looks correct")
        print("=" * 60)
    finally:
        cast.disconnect(timeout=5)
        browser.stop_discovery()
        server.shutdown()
        try:
            os.remove(mp3_path)
        except OSError:
            pass

    return 0

if __name__ == "__main__":
    sys.exit(main())
