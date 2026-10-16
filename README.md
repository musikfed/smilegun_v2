# SMILE·GUN v2

Camera-controlled browser arcade game built with **Python 3.12, Flask, MediaPipe, OpenCV and Canvas**. The player is a glowing square controlled by hand position; gestures and facial expressions trigger weapons, defense and time bonuses.

**UI languages:** Русский · English · 中文 · Қазақша · 한국어. The language selector is available directly in the main menu and the choice is stored in the browser.

Localized quick guides: [Русский](docs/README.ru.md) · [中文](docs/README.zh-CN.md) · [Қазақша](docs/README.kk.md) · [한국어](docs/README.ko.md)

## Screenshots

### Main menu
![SMILE·GUN menu](docs/screenshots/menu-ru.png)

| Shooting range | Galaxy |
|---|---|
| ![Shooting range](docs/screenshots/level-1-targets.png) | ![Galaxy](docs/screenshots/level-2-galaxy.png) |

| Dodge | Side flight |
|---|---|
| ![Dodge](docs/screenshots/level-3-dodge.png) | ![Side flight](docs/screenshots/level-4-side.png) |

## Controls

| Input | Action |
|---|---|
| Hand position | Move the player square / aim |
| Fist | Single shot from the square |
| Open mouth | Spread shot; blink charge increases pellet count |
| Blink | Charge SUPER; one complete open → closed → open cycle counts once |
| Smile | Random +10…25 s, +1 life and 45 s armor; remaining round time is capped at 50 s |
| SUPER 100% + new smile | Clear current targets/enemies/projectiles/obstacles |
| ✌ two fingers | Ghost mode while held; hostile attacks pass through the player |
| 👐 two open palms | Toggle pause/resume |
| Esc / bottom ✕ button | Return to the menu |

## Campaign

1. **Shooting Range** — moving targets.
2. **Galaxy** — hostile ships and enemy fire.
3. **Dodge** — falling hazards that can be dodged or destroyed.
4. **Side Flight** — horizontal scrolling, walls and enemies entering from the right.

Difficulty 1–5 changes density, speed and pressure. Base round duration is configurable from 10 to 50 seconds.

## Windows quick start

Requirements: Windows, `uv`, Python 3.12 and a webcam.

```powershell
cd D:\smilegun
uv run --python 3.12 python app.py
```

or:

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

Open `http://127.0.0.1:5000` and allow camera access.

## Phone / front camera over Wi-Fi

Browsers require a secure context for `getUserMedia` on a phone opened by LAN IP. Use:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_mobile.ps1
```

The script creates a local CA/server certificate under `.smilegun-certs`, starts HTTPS on port `5443`, and prints a URL such as `https://192.168.1.25:5443`. Install/trust `.smilegun-certs\smilegun-mobile-ca.crt` on the phone once, then open the printed HTTPS URL and allow the front camera.

## Architecture

The preferred camera path is:

```text
Browser getUserMedia
        ↓ JPEG frames (~13 fps)
Flask /camera/browser/frame
        ↓
MediaPipe face + hands
        ↓
/state event counters + normalized aim
        ↓
Canvas game loop
```

OpenCV `VideoCapture` remains a Windows fallback. Flask runs with `use_reloader=False` so a second process cannot steal the camera.

Source comments and implementation notes are primarily **English**. Localized user-facing text is isolated in `static/i18n.js`; this keeps gameplay code readable instead of duplicating it five times.

## Important files

- `app.py` — Flask server, MediaPipe tracking, gestures, camera lifecycle, API and shutdown.
- `config.py` / `config.json` — validated game/tracker settings and persisted score/calibration.
- `templates/index.html` — responsive UI shell.
- `static/game.js` — language-neutral Canvas gameplay and browser camera client.
- `static/i18n.js` — RU/EN/ZH/KK/KO UI dictionaries.
- `static/*.mp3` — local generated sound effects.
- `run.ps1` — desktop start.
- `run_mobile.ps1` + `mobile_tls.py` — HTTPS mobile start.
- `camera_probe.py` / `diagnostics.py` — fallback diagnostics.
- `test_final.py` / `test_frontend.cjs` — tracker/config and frontend regression tests.

## Tests

```powershell
uv run --python 3.12 python -m unittest -v test_final
node test_frontend.cjs
```

The frontend test checks JavaScript syntax, responsive layout, language packs, camera paths and the core gesture/game mechanics. MediaPipe tracker tests require the runtime Python dependencies but do not require a real webcam.

## Privacy

Camera frames are processed by the locally running Flask application. The project does not upload camera frames to a remote service.

## Development notes

Keep gameplay code and code comments in English. Add UI text through translation keys in `static/i18n.js`; do not duplicate the entire HTML/JavaScript per language. Brief localized documentation belongs under `docs/`.
