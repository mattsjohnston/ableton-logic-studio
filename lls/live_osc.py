"""Tiny AbletonOSC client (rehearsal-time only; the stage set never depends on this).

AbletonOSC (MIT, github.com/ideoforms/AbletonOSC) is a Live Remote Script that listens on UDP 11000 and replies to
11001. Install: copy it to ~/Music/Ableton/User Library/Remote Scripts/AbletonOSC, then select it in
Live > Settings > Tempo & MIDI > Control Surface.

Ports: LLS_OSC_PORT (Live's listen port, default 11000) and LLS_OSC_REPLY_PORT (where we listen, default 11001) override
the defaults; tests point them at a fake responder. AbletonOSC itself always replies to 11001, so only one process on the
machine can talk to it at a time (binding the reply port fails with LiveBusy otherwise).
"""
import errno, os, socket, threading, time
from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.osc_message import OscMessage

LIVE_PORT, REPLY_PORT = 11000, 11001


def ports():
    """(Live's port, our reply port), from LLS_OSC_PORT / LLS_OSC_REPLY_PORT or the AbletonOSC defaults."""
    return int(os.environ.get("LLS_OSC_PORT") or LIVE_PORT), int(os.environ.get("LLS_OSC_REPLY_PORT") or REPLY_PORT)


class LiveBusy(OSError):
    """The reply port is already bound by another process (another lls ui server or OSC client)."""


class Live:
    def __init__(self, host="127.0.0.1", timeout=1.0, port=None, reply_port=None):
        dport, dreply = ports()
        self.host, self.timeout = host, timeout
        self.port, self.reply_port = port or dport, reply_port or dreply
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind(("127.0.0.1", self.reply_port))
        except OSError as e:
            self.sock.close()
            if e.errno == errno.EADDRINUSE:
                raise LiveBusy(e.errno, f"UDP port {self.reply_port} (AbletonOSC's reply port) is already in use by another "
                                        "process, probably another lls ui server or OSC tool. Close it and try again.") from None
            raise
        self.sock.settimeout(timeout)
        self.lock = threading.Lock()

    def close(self):
        self.sock.close()

    def send(self, address, *args):
        b = OscMessageBuilder(address=address)
        for a in args: b.add_arg(a)
        self.sock.sendto(b.build().dgram, (self.host, self.port))

    def _drain(self):
        """Drop replies that arrived after an earlier query timed out (they would answer the wrong question)."""
        self.sock.setblocking(False)
        try:
            while True: self.sock.recvfrom(65536)
        except (BlockingIOError, OSError):
            pass
        finally:
            self.sock.settimeout(self.timeout)

    def query(self, address, *args, timeout=None):
        """Send and wait for the reply on the same address (and, for indexed getters, echoing the same leading args);
        returns its params (tuple) or None on timeout."""
        with self.lock:
            self._drain()
            self.send(address, *args)
            end = time.time() + (self.timeout if timeout is None else timeout)
            while True:
                left = end - time.time()
                if left <= 0: return None
                self.sock.settimeout(left)
                try:
                    data, _ = self.sock.recvfrom(65536)
                except (socket.timeout, OSError):
                    return None
                finally:
                    self.sock.settimeout(self.timeout)
                try:
                    m = OscMessage(data)
                except Exception:  # noqa: BLE001 — not OSC
                    continue
                if m.address == address and tuple(m.params[:len(args)]) == tuple(args):
                    return tuple(m.params)

    def multi(self, queries, timeout=None):
        """Pipelined queries: [(address, *args), ...] -> [params | None, ...]. AbletonOSC answers on Live's UI tick
        (~100 ms per message when sent one by one), so sending everything first costs about one tick in total."""
        queries = [(q[0], tuple(q[1:])) for q in queries]
        out = [None] * len(queries)
        with self.lock:
            self._drain()
            for addr, args in queries: self.send(addr, *args)
            end = time.time() + (self.timeout if timeout is None else timeout)
            while any(o is None for o in out):
                left = end - time.time()
                if left <= 0: break
                self.sock.settimeout(left)
                try:
                    data, _ = self.sock.recvfrom(65536)
                except (socket.timeout, OSError):
                    break
                finally:
                    self.sock.settimeout(self.timeout)
                try:
                    m = OscMessage(data)
                except Exception:  # noqa: BLE001 — not OSC
                    continue
                for i, (addr, args) in enumerate(queries):
                    if out[i] is None and m.address == addr and tuple(m.params[:len(args)]) == args:
                        out[i] = tuple(m.params); break
        return out

    # --- convenience -------------------------------------------------------
    def ping(self, timeout=None):
        """/live/test (Live also flashes a confirmation in its status bar, so don't poll this)."""
        return self.query("/live/test", timeout=timeout) is not None

    def tempo(self):
        r = self.query("/live/song/get/tempo"); return r[0] if r else None

    def is_playing(self):
        r = self.query("/live/song/get/is_playing"); return bool(r[0]) if r else None

    def beat_time(self):
        r = self.query("/live/song/get/current_song_time"); return r[0] if r else None

    def scene_names(self):
        r = self.query("/live/song/get/scenes/name")       # bulk getter (AbletonOSC >= 2023)
        if r is not None: return [str(x) for x in r]
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
        r = self.query("/live/song/get/track_names")       # bulk getter
        if r is not None: return [str(x) for x in r]
        n = self.query("/live/song/get/num_tracks")
        if not n: return []
        return [(self.query("/live/track/get/name", i) or (i, ""))[-1] for i in range(int(n[0]))]

    def playing_slot(self, track_index):
        """Scene index of the clip playing on a track; -1 none, -2 the stop button; None if Live didn't answer."""
        r = self.query("/live/track/get/playing_slot_index", int(track_index)); return int(r[-1]) if r else None

    def fired_slot(self, track_index):
        """Scene index of a clip launched but not yet playing (waiting for quantization); -1 none."""
        r = self.query("/live/track/get/fired_slot_index", int(track_index)); return int(r[-1]) if r else None

    def clip_position(self, track_index, slot):
        r = self.query("/live/clip/get/playing_position", int(track_index), int(slot)); return r[-1] if r else None

    def clip_loop_start(self, track_index, slot):
        r = self.query("/live/clip/get/loop_start", int(track_index), int(slot)); return r[-1] if r else None


if __name__ == "__main__":
    live = Live()
    print("connected:", live.ping(), "tempo:", live.tempo(), "playing:", live.is_playing())
    print("scenes:", live.scene_names()[:20])
