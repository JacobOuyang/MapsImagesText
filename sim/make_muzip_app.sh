#!/bin/bash
# Builds MuZip.app (out/MuZip.app): a tiny helper whose only job is pressing Return in WhatsApp, the one step macOS only
# lets an app with Accessibility permission do. It touches nothing in Documents (where the project lives, which macOS
# guards per app): it watches a private folder in /tmp for "press now" requests from the MuZip server (sim/muzip.py),
# presses Return in WhatsApp, and answers. Double-clicking it also opens MuZip's page.
#
#   bash sim/make_muzip_app.sh && open out/MuZip.app
#   then: System Settings > Privacy & Security > Accessibility > + > MuZip (after every rebuild: remove it, add it again)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP="$REPO/out/MuZip.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>MuZip</string>
  <key>CFBundleIdentifier</key><string>local.muzip</string>
  <key>CFBundleExecutable</key><string>MuZip</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>2.0</string>
  <key>LSUIElement</key><true/>
  <key>NSAppleEventsUsageDescription</key><string>MuZip presses Return in WhatsApp to send your messages to Muse.</string>
</dict></plist>
EOF
cat > "$APP/Contents/MacOS/MuZip" <<'EOF'
#!/bin/bash
# One helper at a time; requests come from sim/muzip.py through this private folder.
DIR="/tmp/muzip-$(id -u)"
mkdir -p "$DIR" && chmod 700 "$DIR"
if [ -f "$DIR/helper.pid" ] && kill -0 "$(cat "$DIR/helper.pid")" 2>/dev/null; then open "http://127.0.0.1:8780"; exit 0; fi
echo $$ > "$DIR/helper.pid"
open "http://127.0.0.1:8780"
while true; do
  date +%s > "$DIR/alive"
  if [ -f "$DIR/press" ]; then
    rm -f "$DIR/press"
    if osascript -e 'delay 1.5' \
        -e 'tell application "System Events" to set previous to name of first application process whose frontmost is true' \
        -e 'tell application "WhatsApp" to activate' -e 'delay 0.6' \
        -e 'tell application "System Events" to keystroke return' -e 'delay 0.4' \
        -e 'if previous is not "WhatsApp" then tell application previous to activate' 2> "$DIR/error"; then
      echo ok > "$DIR/done"
    else
      cp "$DIR/error" "$DIR/done"
    fi
  fi
  sleep 0.2
done
EOF
chmod +x "$APP/Contents/MacOS/MuZip"
# Signed ad hoc: macOS keeps an Accessibility grant against an app's signature.
codesign --force --sign - "$APP"
echo "Built $APP (after rebuilding, remove MuZip from Accessibility and add it again)"
