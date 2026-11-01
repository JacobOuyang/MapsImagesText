#!/bin/bash
# Builds muzip.app (out/muzip.app): double-click it to start the WhatsApp bridge (if it isn't running already) and
# muzip, and open the kit's page. Being its own app, it is what you give Accessibility permission to, so it can press
# Return in WhatsApp for you (System Settings > Privacy & Security > Accessibility > + > muzip).
#
#   bash sim/make_muzip_app.sh && open out/muzip.app
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP="$REPO/out/muzip.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$REPO/out/muzip-logs"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>muzip</string>
  <key>CFBundleIdentifier</key><string>local.muzip</string>
  <key>CFBundleExecutable</key><string>muzip</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSUIElement</key><true/>
  <key>NSAppleEventsUsageDescription</key><string>muzip presses Return in WhatsApp to send your messages to Muse.</string>
</dict></plist>
EOF
cat > "$APP/Contents/MacOS/muzip" <<EOF
#!/bin/bash
cd "$REPO"
LOGS="$REPO/out/muzip-logs"
up() { curl -s -m 2 "http://127.0.0.1:\$1/" >/dev/null; }
up 8770 || nohup .venv/bin/python sim/whatsapp_server.py >> "\$LOGS/bridge.log" 2>&1 &
up 8780 || nohup .venv/bin/python sim/muzip.py >> "\$LOGS/muzip.log" 2>&1 &
for i in \$(seq 1 20); do up 8780 && break; sleep 0.5; done
open "http://127.0.0.1:8780"
# Stay alive while the kit runs, so macOS keeps treating its key presses as muzip's.
while up 8780; do sleep 5; done
EOF
chmod +x "$APP/Contents/MacOS/muzip"
echo "Built $APP"
