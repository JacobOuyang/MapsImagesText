#!/bin/bash
# Builds SMuSe.app (out/SMuSe.app): double-click it to start the WhatsApp bridge (if it isn't running already) and
# SMuSe, and open the kit's page. Being its own app, it is what you give Accessibility permission to, so it can press
# Return in WhatsApp for you (System Settings > Privacy & Security > Accessibility > + > SMuSe).
#
#   bash sim/make_smuse_app.sh && open out/SMuSe.app
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP="$REPO/out/SMuSe.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$REPO/out/smuse-logs"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>SMuSe</string>
  <key>CFBundleIdentifier</key><string>local.smuse</string>
  <key>CFBundleExecutable</key><string>SMuSe</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSUIElement</key><true/>
  <key>NSAppleEventsUsageDescription</key><string>SMuSe presses Return in WhatsApp to send your messages to Muse.</string>
</dict></plist>
EOF
cat > "$APP/Contents/MacOS/SMuSe" <<EOF
#!/bin/bash
cd "$REPO"
LOGS="$REPO/out/smuse-logs"
up() { curl -s -m 2 "http://127.0.0.1:\$1/" >/dev/null; }
up 8770 || nohup .venv/bin/python sim/whatsapp_server.py >> "\$LOGS/bridge.log" 2>&1 &
up 8780 || nohup .venv/bin/python sim/smuse.py >> "\$LOGS/smuse.log" 2>&1 &
for i in \$(seq 1 20); do up 8780 && break; sleep 0.5; done
open "http://127.0.0.1:8780"
# Stay alive while the kit runs, so macOS keeps treating its key presses as SMuSe's.
while up 8780; do sleep 5; done
EOF
chmod +x "$APP/Contents/MacOS/SMuSe"
echo "Built $APP"
