# HomeSense Electron Client

Desktop client for connecting a MacBook or other laptop to the HomeSense DGX/OmniGibson live server.

The simulator, BEHAVIOR assets, and FastAPI server stay on the DGX machine. This app only renders the MJPEG stream, receives state over WebSocket, and sends camera/task/resident commands.

## Development

Start the OmniGibson live server on the DGX/server machine first:

```bash
cd /home/user/Projects/csp-2026-k/BEHAVIOR-1K
/home/user/Desktop/isaac-sim-5.1/python.sh examples/smart_home/run_live_control_scene.py \
  --scene-model Merom_0_int \
  --full \
  --cpu-dynamics \
  --robot-type R1Pro \
  --host 0.0.0.0 \
  --port 8080 \
  --video-fps 8
```

Then start the desktop client:

```bash
cd /home/user/Projects/csp-2026-k/BEHAVIOR-1K/clients/homesense-electron
npm install
npm run start
```

To start with a default server URL:

```bash
HOMESENSE_SERVER_URL=http://10.32.253.88:8080 npm run start
```

Kiosk/fullscreen mode:

```bash
npm run start:kiosk
```

## Packaging

macOS:

```bash
npm run pack:mac
```

Linux:

```bash
npm run pack:linux
```

Code signing and notarization are not configured yet. For internal demos, unsigned builds can be used with the usual macOS security exception flow.

## Notes

- The app keeps BEHAVIOR/OmniGibson execution on the DGX server. Client laptops only need the packaged Electron app and network access to the server URL.
- The renderer uses MJPEG for video, WebSocket for state updates, and HTTP command endpoints for resident movement, camera switching, reset, and task requests.
- On Linux, the app disables Chromium's sandbox during startup because the current DGX demo environment blocks Electron's default sandbox setup.
