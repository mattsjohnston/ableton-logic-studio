"""Ready-to-use effect chains for the live audio input tracks (vocal mic, bass DI, guitar DI).

Every device is a deepcopy of a Live-SAVED device: an .adv preset from the Core Library (gzipped XML, the device element
sits directly under the <Ableton> wrapper) or, as a fallback when a preset is missing, a device from the Live demo set.
Nothing is hand-written XML; only <Manual Value=...> fields are changed. Global pointee IDs are renumbered via
als.Ids in insert_chain(), which also assigns the list-local device Ids.

Reverb / delay for vocals live on the return tracks (sends), not here.
"""
import copy, os

from . import als

FX = f"{als.CORE}/Devices/Audio Effects"
FX_MODES = ("default", "none")

# (preset path under FX, {xpath: Manual value}) per device
VOCAL = [("EQ Eight/Filter/LowCut.adv", {"Bands.0/ParameterA/Freq/Manual": 100}),          # low cut @ 100 Hz
         ("Compressor/De-esser.adv", {}),                                                  # sidechain-EQ de-esser, 3:1
         ("Compressor/Sustained Lead Vocal.adv", {"Ratio/Manual": 3, "Gain/Manual": 0})]   # ~3:1 levelling
BASS = [("Amp/Bass Roundup.adv", {}), ("Cabinet/4x10 Bass Cab.adv", {})]
GUITAR = [("Amp/Clean.adv", {}), ("Cabinet/1x12 Cab.adv", {}), ("Delay/Clean Delay/Slight Slap.adv", {})]
# demo-set device used when a preset file is missing (Amp/Cabinet fall back to a Saturator)
FALLBACK_TAG = {"EQ Eight": "Eq8", "Compressor": "Compressor2", "Amp": "Saturator", "Delay": "Delay"}

LABEL = {"Eq8": "EQ Eight", "Compressor2": "Compressor", "Amp": "Amp", "Cabinet": "Cabinet", "Delay": "Delay",
         "Saturator": "Saturator"}


def load_preset(rel):
    """Device element from a Core Library .adv preset, or None if the file is missing."""
    p = os.path.join(FX, rel)
    if not os.path.exists(p): return None
    return copy.deepcopy(als.load_gz(p)[0])


def _demo_device(protos, tag):
    cache = protos.__dict__.setdefault("_demo_devices", {})
    if tag not in cache:
        cache[tag] = next(als.load_gz(als.DEMO).iter(tag), None)
    return copy.deepcopy(cache[tag]) if cache[tag] is not None else None


def _device(protos, rel, params):
    d = load_preset(rel)
    if d is None:  # preset missing: same kind of device from the demo set, its saved settings
        tag = FALLBACK_TAG.get(rel.split("/")[0])
        d = _demo_device(protos, tag) if tag else None
        if d is None: return None
        params = {}
    for path, v in params.items():
        als.setv(d, path, v)
    return d


def _build(protos, spec):
    return [d for d in (_device(protos, rel, p) for rel, p in spec) if d is not None]


def vocal_chain(protos): return _build(protos, VOCAL)
def bass_chain(protos): return _build(protos, BASS)
def guitar_chain(protos): return _build(protos, GUITAR)


def describe(devs):
    """'EQ Eight (LowCut) → Compressor (De-esser) → ...'"""
    out = []
    for d in devs:
        n = d.find("UserName")
        n = n.get("Value") if n is not None else ""
        out.append(LABEL.get(d.tag, d.tag) + (f" ({n})" if n else ""))
    return " → ".join(out)


def insert_chain(track, devs, ids):
    """Replace the track's device list with `devs` (renumbering global IDs, assigning list-local Ids)."""
    dv = track.find("DeviceChain/DeviceChain/Devices")
    als.clear(dv)
    for i, d in enumerate(devs):
        d.set("Id", str(i)); ids.renumber(d); dv.append(d)
