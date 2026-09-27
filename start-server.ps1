# Starts the app for real use (not for coding).
# - "fastapi run" instead of "fastapi dev": no auto-reload, steadier.
# - Listens on 127.0.0.1 only: nothing on the Wi-Fi can reach it directly.
#   The phone goes through Tailscale ("tailscale serve"), which adds HTTPS.

$root = $PSScriptRoot
Set-Location "$root\backend"
& "$root\backend\.venv\Scripts\fastapi.exe" run app/main.py --host 127.0.0.1 --port 8001
