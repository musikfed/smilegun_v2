# Architecture notes

SMILE·GUN keeps recognition and gameplay deliberately separated.

- **Python (`app.py`)** owns MediaPipe, event edge/debounce state, configuration and camera lifecycle.
- **Browser (`static/game.js`)** owns the Canvas world, collisions, round timers, sounds and responsive UI behavior.
- **Translations (`static/i18n.js`)** are data only. Game logic references stable English keys.
- **Browser camera is primary.** `getUserMedia` obtains the real webcam and sends resized JPEG frames to Flask. Native OpenCV capture is a fallback.
- Gesture events use monotonically increasing IDs (`fist_shot_id`, `mouth_shot_id`, `smile_id`, `blink_id`, `super_id`, `two_palms_id`) so polling cannot turn one held gesture into dozens of actions.

A few user-facing backend diagnostic strings remain English/Russian-compatible, while normal gameplay UI is fully localized through the five language packs.
