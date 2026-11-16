"""Song packages (.llsong): move a song between Macs (e.g. to the bandmate who runs the show).

    python -m lls package export <song_dir> [out.llsong]
    python -m lls package import <file.llsong> [--slug x] [--force] [--root .]

A package is a zip of the song folder: song.json, midi/, instruments/, stems/export/, stems/raw/, stems/oneshots/
(never stems/preview/ or *.bak), plus manifest.json at the root:
    {format, lls_version, git_sha, slug, title, created_at, files: [{path, size, sha256}]}
Export rewrites machine-specific paths in song.json (source.logicx / original / export_dir -> file name only);
quicksampler_files stay as they are (Logic's library path is the same on every Mac).
Import checks the manifest (format, every file's size + sha256, nothing extra, no path escapes), refuses to replace an
existing song unless force=True, and keeps lineups: on a forced import the existing song's lineups that the package
doesn't have are merged into the imported song.json (same id: the package wins).
"""
import datetime, hashlib, json, os, re, shutil, subprocess, tempfile, zipfile

FORMAT = 1
EXT = ".llsong"
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
INCLUDE_DIRS = ("midi", "instruments", "stems/export", "stems/raw", "stems/oneshots")
PATH_KEYS = ("logicx", "original", "export_dir")  # song.json "source" entries that are absolute paths on one Mac
STORED = (".wav", ".aif", ".aiff", ".mp3", ".m4a", ".flac")  # already dense: don't spend time deflating them
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PackageError(Exception):
    pass


class SongExists(PackageError):
    pass


def lls_version():
    from . import __version__
    return __version__


def git_sha():
    try:
        r = subprocess.run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()


def song_files(song_dir):
    """Relative paths (posix) of everything that goes into a package, sorted."""
    out = []
    for d in INCLUDE_DIRS:
        top = os.path.join(song_dir, *d.split("/"))
        if not os.path.isdir(top): continue
        for dp, dns, fns in os.walk(top, followlinks=True):
            dns[:] = sorted(x for x in dns if not x.startswith("."))
            for fn in fns:
                if fn.startswith(".") or fn.endswith(".bak"): continue
                full = os.path.join(dp, fn)
                if os.path.isfile(full): out.append(os.path.relpath(full, song_dir).replace(os.sep, "/"))
    return sorted(set(out))


def portable_song(song):
    """Copy of song.json with machine-specific absolute paths reduced to file names."""
    s = json.loads(json.dumps(song))
    src = s.get("source")
    if isinstance(src, dict):
        for k in PATH_KEYS:
            if isinstance(src.get(k), str) and src[k]:
                src[k] = os.path.basename(src[k].rstrip("/"))
    s.pop("_dir", None)
    return s


def default_name(song_dir, song=None):
    return os.path.basename(os.path.abspath(song_dir).rstrip("/")) + EXT


def export(song_dir, out=None, log=None):
    """Write the package; returns (path, manifest). `out` may be a file or an existing directory."""
    song_dir = os.path.abspath(song_dir)
    sp = os.path.join(song_dir, "song.json")
    if not os.path.isfile(sp): raise PackageError(f"not a song folder (no song.json): {song_dir}")
    with open(sp, encoding="utf-8") as f: song = json.load(f)
    slug = os.path.basename(song_dir)
    out = out or os.path.join(os.getcwd(), default_name(song_dir))
    if os.path.isdir(out): out = os.path.join(out, default_name(song_dir))
    song_bytes = (json.dumps(portable_song(song), indent=2, ensure_ascii=False) + "\n").encode()
    rels = song_files(song_dir)
    files = [{"path": "song.json", "size": len(song_bytes), "sha256": hashlib.sha256(song_bytes).hexdigest()}]
    for rel in rels:
        full = os.path.join(song_dir, *rel.split("/"))
        files.append({"path": rel, "size": os.path.getsize(full), "sha256": _sha256_file(full)})
    manifest = {"format": FORMAT, "lls_version": lls_version(), "git_sha": git_sha(), "slug": slug,
                "title": song.get("title") or slug,
                "created_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
                "files": files}
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    tmp = out + ".part"
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            z.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
            z.writestr("song.json", song_bytes)
            for rel in rels:
                if log: log(f"  + {rel}")
                comp = zipfile.ZIP_STORED if rel.lower().endswith(STORED) else zipfile.ZIP_DEFLATED
                z.write(os.path.join(song_dir, *rel.split("/")), rel, compress_type=comp)
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return out, manifest


def _safe_rel(name):
    if not name or name.startswith("/") or "\\" in name or re.match(r"^[A-Za-z]:", name): return False
    parts = name.split("/")
    return all(p not in ("", ".", "..") for p in parts)


def read_manifest(pkg):
    """Manifest of a package (checks format and shape only; verify() checks the files)."""
    try:
        z = zipfile.ZipFile(pkg)
    except (OSError, zipfile.BadZipFile) as e:
        raise PackageError(f"not a song package (bad zip): {e}")
    with z:
        try:
            m = json.loads(z.read("manifest.json"))
        except KeyError:
            raise PackageError("not a song package: manifest.json missing")
        except ValueError as e:
            raise PackageError(f"manifest.json is not valid JSON: {e}")
    if not isinstance(m, dict) or not isinstance(m.get("files"), list):
        raise PackageError("manifest.json has no file list")
    if not isinstance(m.get("format"), int) or m["format"] > FORMAT:
        raise PackageError(f"package format {m.get('format')!r} is newer than this lls understands ({FORMAT}): update lls")
    for e in m["files"]:
        if not (isinstance(e, dict) and isinstance(e.get("path"), str) and isinstance(e.get("sha256"), str)
                and isinstance(e.get("size"), int)):
            raise PackageError(f"bad manifest entry {e!r}")
        if not _safe_rel(e["path"]) or e["path"] == "manifest.json":
            raise PackageError(f"unsafe path in package: {e['path']!r}")
    if "song.json" not in {e["path"] for e in m["files"]}: raise PackageError("package has no song.json")
    return m


def _extract_verified(pkg, manifest, dest):
    """Extract every manifest file into dest, checking size + sha256; refuses extra or missing entries."""
    want = {e["path"]: e for e in manifest["files"]}
    if len(want) != len(manifest["files"]): raise PackageError("duplicate paths in manifest")
    with zipfile.ZipFile(pkg) as z:
        names = [i.filename for i in z.infolist() if not i.is_dir()]
        extra = sorted(set(names) - set(want) - {"manifest.json"})
        if extra: raise PackageError(f"files not listed in the manifest: {', '.join(extra[:5])}")
        missing = sorted(set(want) - set(names))
        if missing: raise PackageError(f"files missing from the package: {', '.join(missing[:5])}")
        for name, e in want.items():
            target = os.path.join(dest, *name.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            h, n = hashlib.sha256(), 0
            with z.open(name) as src, open(target, "wb") as dst:
                for chunk in iter(lambda: src.read(1 << 20), b""):
                    h.update(chunk); n += len(chunk); dst.write(chunk)
            if n != e["size"] or h.hexdigest() != e["sha256"]:
                raise PackageError(f"{name}: contents don't match the manifest (damaged or edited package)")


def verify(pkg):
    """Full check without importing; returns the manifest or raises PackageError."""
    m = read_manifest(pkg)
    with tempfile.TemporaryDirectory(prefix="lls-verify-") as tmp:
        _extract_verified(pkg, m, tmp)
    return m


def import_package(pkg, songs_dir, slug=None, force=False, log=None):
    """Install a package as songs_dir/<slug>. Returns {slug, song_dir, title, files, replaced, kept_lineups}."""
    m = read_manifest(pkg)
    slug = slug or m.get("slug") or ""
    if not isinstance(slug, str) or not SLUG.match(slug):
        raise PackageError(f"bad song slug {slug!r}: use kebab-case (a-z, 0-9, -), e.g. --slug my-song")
    dest = os.path.join(songs_dir, slug)
    exists = os.path.exists(dest)
    if exists and not force:
        raise SongExists(f"song {slug!r} already exists; import with --force to replace it (its lineups are kept), "
                         "or pick another --slug")
    os.makedirs(songs_dir, exist_ok=True)
    stage = tempfile.mkdtemp(prefix=f".import-{slug}-", dir=songs_dir)
    old = None
    try:
        _extract_verified(pkg, m, stage)
        sp = os.path.join(stage, "song.json")
        try:
            with open(sp, encoding="utf-8") as f: song = json.load(f)
        except ValueError as e:
            raise PackageError(f"song.json in the package is not valid JSON: {e}")
        if not isinstance(song, dict) or not isinstance(song.get("parts"), list):
            raise PackageError("song.json in the package is not a song")
        kept = []
        if exists:
            try:
                with open(os.path.join(dest, "song.json"), encoding="utf-8") as f: prev = json.load(f)
            except (OSError, ValueError):
                prev = {}
            mine = song.setdefault("lineups", {})
            for lid, lu in (prev.get("lineups") or {}).items():
                if lid not in mine: mine[lid] = lu; kept.append(lid)
            if kept:
                with open(sp, "w", encoding="utf-8") as f:
                    json.dump(song, f, indent=2, ensure_ascii=False); f.write("\n")
            old = dest.rstrip("/") + f".replaced-{os.getpid()}"
            os.replace(dest, old)
        os.replace(stage, dest); stage = None
        if old: shutil.rmtree(old, ignore_errors=True); old = None
        if log: log(f"imported {m.get('title')!r} -> {dest}")
        return {"slug": slug, "song_dir": dest, "title": song.get("title") or m.get("title"), "files": len(m["files"]),
                "replaced": exists, "kept_lineups": kept, "lineups": sorted(song.get("lineups") or {}),
                "lls_version": m.get("lls_version"), "git_sha": m.get("git_sha"), "created_at": m.get("created_at")}
    finally:
        if old and not os.path.exists(dest): os.replace(old, dest)  # failed mid-swap: put the original back
        if stage: shutil.rmtree(stage, ignore_errors=True)
