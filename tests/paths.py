"""Machine-independent locations for tests. Gitignored data (samples/, songs/*/stems) may be absent on another Mac:
tests that need it skip.

  LLS_SAMPLES          folder with the sample Logic project (default: <repo>/samples)
  LLS_SAMPLE_PROJECT   the sample .logicx itself (default: $LLS_SAMPLES/Thoughts For The Night.logicx)
  LIVE_APP             which Live 12 to use (see lls.liveapp)
"""
import os

from lls import liveapp

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.environ.get("LLS_SAMPLES") or os.path.join(REPO, "samples")
SAMPLE = os.environ.get("LLS_SAMPLE_PROJECT") or os.path.join(SAMPLES, "Thoughts For The Night.logicx")
HAVE_SAMPLE = os.path.isdir(SAMPLE)
SONG_DIR = os.path.join(REPO, "songs", "thoughts-for-the-night")
HAVE_STEMS = os.path.isdir(os.path.join(SONG_DIR, "stems", "export"))


def live_path(fn):
    """fn() -> a path inside the detected Live; None when there is no Live or it lacks that file."""
    try:
        p = fn()
    except (liveapp.LiveNotFound, FileNotFoundError):
        return None
    return p if p and os.path.exists(p) else None


HAVE_LIVE = liveapp.available()
