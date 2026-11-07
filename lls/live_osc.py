"""Tiny AbletonOSC client (rehearsal-time only; the stage set never depends on this).

AbletonOSC (MIT, github.com/ideoforms/AbletonOSC) is a Live Remote Script that listens on UDP 11000 and replies to
11001. Install: copy it to ~/Music/Ableton/User Library/Remote Scripts/AbletonOSC, then select it in
Live > Settings > Tempo & MIDI > Control Surface.
"""
import socket, threading, time
from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.osc_message import OscMessage

LIVE_PORT, REPLY_PORT = 11000, 11001


class Live:
    def __init__(self, host="127.0.0.1", timeout=1.0):
        self.host, self.timeout = host, timeout
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", REPLY_PORT))
        self.sock.settimeout(timeout)
        self.lock = threading.Lock()

    def close(self):
        self.sock.close()

    def send(self, address, *args):
        b = OscMessageBuilder(address=address)
        for a in args: b.add_arg(a)
        self.sock.sendto(b.build().dgram, (self.host, LIVE_PORT))

    def query(self, address, *args):
        """Send and wait for the reply on the same address; returns its params (tuple) or None on timeout."""
        with self.lock:
            self.send(address, *args)
            end = time.time() + self.timeout
            while time.time() < end:
                try:
                    data, _ = self.sock.recvfrom(65536)
                except socket.timeout:
                    return None
                m = OscMessage(data)
                if m.address == address:
                    return tuple(m.params)
            return None

    # --- convenience -------------------------------------------------------
    def ping(self):
        return self.query("/live/test") is not None

    def tempo(self):
        r = self.query("/live/song/get/tempo"); return r[0] if r else None

    def is_playing(self):
        r = self.query("/live/song/get/is_playing"); return bool(r[0]) if r else None

    def beat_time(self):
        r = self.query("/live/song/get/current_song_time"); return r[0] if r else None

    def scene_names(self):
        n = self.query("/live/song/get/num_scenes")
        if not n: return []
        return [(self.query("/live/scene/get/name", i) or (i, ""))[-1] for i in range(int(n[0]))]

    def fire_scene(self, index):
        self.send("/live/scene/fire", int(index))

    def stop_all(self):
        self.send("/live/song/stop_all_clips"); self.send("/live/song/stop_playing")

    def set_track_mute(self, track_index, mute):
        self.send("/live/track/set/mute", int(track_index), int(bool(mute)))

    def set_track_solo(self, track_index, solo):
        self.send("/live/track/set/solo", int(track_index), int(bool(solo)))

    def track_names(self):
        n = self.query("/live/song/get/num_tracks")
        if not n: return []
        return [(self.query("/live/track/get/name", i) or (i, ""))[-1] for i in range(int(n[0]))]


if __name__ == "__main__":
    live = Live()
    print("connected:", live.ping(), "tempo:", live.tempo(), "playing:", live.is_playing())
    print("scenes:", live.scene_names()[:20])
