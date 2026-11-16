"""Find the Ableton Live 12 install on this Mac (so lls runs on any bandmate's machine, not just ours).

    find_live() -> LiveApp(path, name, version)   (a NamedTuple: unpacks as (app_path, app_name, version))

Order: $LIVE_APP (path to a .app, or an app name in /Applications) if set; else every "Ableton Live 12*.app" in
/Applications and ~/Applications. Suite editions win (newest CFBundleShortVersionString first, a release beating a beta
of the same number); otherwise the newest Live 12 of any edition (Standard, Intro, Lite, a plain "Beta" bundle...).

Everything we copy from Live (the 8-Track template, the Patience demo song, Core Library device presets and samples)
comes from the detected app. Only lls/templates/analoglab_au_track.xml is ours; it was saved by Live with schema
PROTO_SCHEMA, so a Live whose newest Schema file is older than that may refuse our sets (schema_warning()).
"""
import glob, os, plistlib, re
from typing import NamedTuple

PROTO_SCHEMA = "12.0_12402"            # MinorVersion of our template/prototypes
EDITIONS = ("Suite", "Standard", "Intro", "Lite")
DEMO_GLOB = "*Patience (Live 12 * Demo).als"
DEMO_PREFERENCE = ("Suite", "Standard", "Push", "Intro", "Lite")   # demo variants, best prototype source first
INSTALL_HINT = ("Install Ableton Live 12 Suite (ableton.com/en/account) into /Applications, "
                "or set LIVE_APP to the .app path (e.g. LIVE_APP=\"/Applications/Ableton Live 12 Suite.app\").")


class LiveNotFound(RuntimeError):
    pass


def search_dirs():
    return ["/Applications", os.path.expanduser("~/Applications")]


def parse_version(s):
    """'12.4.25b1 (2026-10-28_e04f...)' -> (12, 4, 25, 0, 1); '12.1.5' -> (12, 1, 5, 1, 0). Releases sort above betas."""
    m = re.match(r"\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:([abd])(\d+))?", s or "")
    if not m: return (0, 0, 0, 0, 0)
    major, minor, patch = (int(m.group(i) or 0) for i in (1, 2, 3))
    pre = m.group(4)
    return (major, minor, patch, 0 if pre else 1, int(m.group(5) or 0))


def parse_schema(name):
    """'12.0_12402' or '12.0_12402.txt' -> (12, 0, 12402); None if it isn't a schema name."""
    m = re.fullmatch(r"(\d+)\.(\d+)_(\d+)(?:\.txt)?", name or "")
    return tuple(int(g) for g in m.groups()) if m else None


class LiveApp(NamedTuple):
    path: str        # /Applications/Ableton Live 12 Suite.app
    name: str        # "Ableton Live 12 Suite"  (for `open -a`)
    version: str     # CFBundleShortVersionString, e.g. "12.1.5" or "12.4.25b1 (2026-10-28_...)"

    @property
    def edition(self):
        """Suite / Standard / Intro / Lite from the bundle name, else None (e.g. "Ableton Live 12 Beta")."""
        return next((e for e in EDITIONS if re.search(rf"\b{e}\b", self.name)), None)

    @property
    def beta(self):
        return "beta" in self.name.lower() or parse_version(self.version)[3] == 0

    @property
    def short_version(self):
        return (self.version or "?").split(" ")[0]

    @property
    def resources(self): return os.path.join(self.path, "Contents", "App-Resources")

    @property
    def core_library(self): return os.path.join(self.resources, "Core Library")

    @property
    def schema_dir(self): return os.path.join(self.resources, "Schema")

    def schema(self):
        """Newest Live 12 schema name in Contents/App-Resources/Schema (e.g. '12.0_12402'), or None."""
        try:
            names = [n[:-4] for n in os.listdir(self.schema_dir) if n.endswith(".txt")]
        except OSError:
            return None
        names = [n for n in names if parse_schema(n) and parse_schema(n)[0] == 12]
        return max(names, key=parse_schema) if names else None

    def schema_ok(self):
        s = self.schema()
        return s is None or parse_schema(s) >= parse_schema(PROTO_SCHEMA)

    def schema_warning(self):
        s = self.schema()
        if s and not self.schema_ok():
            return (f"{self.name} {self.short_version} uses set schema {s}, older than {PROTO_SCHEMA} which our templates "
                    "were saved with: built sets may not open. Update Live (Help → Check for Updates, or ableton.com).")
        return None

    def base_template(self):
        return os.path.join(self.core_library, "Templates", "8-Track Template.als")

    def demo_songs(self):
        return sorted(glob.glob(os.path.join(glob.escape(os.path.join(self.core_library, "Lessons", "Demo Songs")), DEMO_GLOB)))

    def demo_song(self):
        """The Patience demo set (any edition's variant: Suite preferred), or None."""
        found = self.demo_songs()
        def rank(p):
            m = re.search(r"\(Live 12 (.+?) Demo\)", os.path.basename(p))
            ed = m.group(1) if m else ""
            return DEMO_PREFERENCE.index(ed) if ed in DEMO_PREFERENCE else len(DEMO_PREFERENCE)
        return min(found, key=rank) if found else None

    def describe(self):
        return f"{self.name} {self.short_version}" + (f" ({self.edition})" if self.edition else "")


def read_app(path):
    """LiveApp for a .app bundle, or None if it isn't a readable Live bundle."""
    try:
        with open(os.path.join(path, "Contents", "Info.plist"), "rb") as f:
            info = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    name = os.path.splitext(os.path.basename(path.rstrip("/")))[0]
    return LiveApp(path.rstrip("/"), name, str(info.get("CFBundleShortVersionString") or info.get("CFBundleVersion") or ""))


def find_all(dirs=None):
    """Every Live 12 bundle found, best first (Suite, then newest)."""
    apps = []
    for d in dirs or search_dirs():
        for p in sorted(glob.glob(os.path.join(glob.escape(d), "Ableton Live 12*.app"))):
            a = read_app(p)
            if a and parse_version(a.version)[0] in (0, 12): apps.append(a)
    return sorted(apps, key=lambda a: (a.edition == "Suite", parse_version(a.version)), reverse=True)


_cached = {}


def find_live(refresh=False, dirs=None):
    """The Live 12 to use (LiveApp). Raises LiveNotFound with an install hint."""
    env = os.environ.get("LIVE_APP")
    key = (env, tuple(dirs or ()))
    if not refresh and key in _cached: return _cached[key]
    if env:
        path = os.path.expanduser(env) if os.sep in env else os.path.join("/Applications", env)
        if not path.rstrip("/").endswith(".app"): path = path.rstrip("/") + ".app"
        a = read_app(path) if os.path.isdir(path) else None
        if a is None: raise LiveNotFound(f"LIVE_APP={env!r} is not an Ableton Live app ({path} not found). " + INSTALL_HINT)
    else:
        apps = find_all(dirs)
        if not apps:
            where = " or ".join(dirs or search_dirs())
            raise LiveNotFound(f"No Ableton Live 12 found in {where}. " + INSTALL_HINT)
        a = apps[0]
    _cached[key] = a
    return a


def available():
    try:
        find_live(); return True
    except LiveNotFound:
        return False


def core_library(): return find_live().core_library
def schema_dir(): return find_live().schema_dir
def app_name(): return find_live().name


_warned = set()


def warn_if_old(log=None):
    """Print the schema warning once per Live app (no-op when fine or when Live isn't found)."""
    try:
        a = find_live()
    except LiveNotFound:
        return None
    w = a.schema_warning()
    if w and a.path not in _warned:
        _warned.add(a.path)
        (log or (lambda s: print("WARNING: " + s)))(w)
    return w
