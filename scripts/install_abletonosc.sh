#!/bin/bash
# Install AbletonOSC (MIT, github.com/ideoforms/AbletonOSC) as a Live Remote Script, pinned to the tested commit,
# and bind it to 127.0.0.1 so only this Mac can control Live. Rehearsal-time only: the show never depends on it.
set -e
SHA="0ca68214bd62c9b5cb641ca34006cfd70ba94430"
DEST="$HOME/Music/Ableton/User Library/Remote Scripts/AbletonOSC"
TMP="$(mktemp -d)"
echo "• Downloading AbletonOSC ($SHA)…"
curl -LsSf "https://github.com/ideoforms/AbletonOSC/archive/$SHA.tar.gz" | tar -xz -C "$TMP"
SRC="$TMP/AbletonOSC-$SHA"
mkdir -p "$(dirname "$DEST")"
rm -rf "$DEST"
mkdir -p "$DEST"
cp -R "$SRC/abletonosc" "$SRC/pythonosc" "$SRC/__init__.py" "$SRC/manager.py" "$SRC/LICENSE.md" "$SRC/README.md" "$DEST/"
sed -i '' "s/('0.0.0.0', OSC_LISTEN_PORT)/('127.0.0.1', OSC_LISTEN_PORT)/" "$DEST/abletonosc/osc_server.py"
rm -rf "$TMP"
echo "• AbletonOSC installed in: $DEST"
echo "  Next (once): restart Live, then Live → Settings → Tempo & MIDI → Control Surface 1 → AbletonOSC (Input/Output: None)."
