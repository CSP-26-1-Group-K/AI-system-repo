# Smart Home Live Demo

This package contains the live OmniGibson browser-control demo.

## Modules

- `runner.py`: OmniGibson scene lifecycle, command queue, sensor polling, camera control, and simulator loop.
- `server.py`: FastAPI app, WebSocket state broadcast, MJPEG streaming, and command endpoints.
- `avatar.py`: procedural resident avatar construction.
- `constants.py`: camera, ceiling, movement, and collision tuning constants.
- `media.py`: RGB frame encoding and action-space helpers.
- `static/`: current monitoring UI assets.

Legacy entry points remain in:

- `examples/smart_home/run_live_control_scene.py`
- `services/control_server/live_scene_app.py`

Those files only import this package so older commands continue to work.

