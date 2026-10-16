# smilegun/diagnostics.py
from __future__ import annotations
import sys
import os
import logging
import importlib
import traceback

# TF_CPP_MIN_LOG_LEVEL / GLOG_minloglevel / ABSL_MIN_LOG_LEVEL are set in
# app.py before MediaPipe import; TensorFlow Lite reads them only at startup.

REQUIRED = [
    ("flask", "flask"),
    ("cv2", "opencv-python"),
    ("mediapipe", "mediapipe"),
    ("numpy", "numpy"),
    ("cryptography", "cryptography"),
]

OPTIONAL = [
    ("scipy", "scipy"),
    ("lameenc", "lameenc"),
]


def check_environment(verbose: bool = True) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warns: list[str] = []

    if not ((3, 12) <= sys.version_info[:2] < (3, 13)):
        errors.append(f"Нужен Python 3.12.x, у тебя {sys.version.split()[0]}")

    for mod, pkg in REQUIRED:
        try:
            importlib.import_module(mod)
        except Exception as e:
            errors.append(f"Импорт '{mod}' провалился: {e}. Установи: uv pip install {pkg}")

    for mod, pkg in OPTIONAL:
        try:
            importlib.import_module(mod)
        except Exception:
            warns.append(f"Опциональный модуль '{mod}' отсутствует (uv pip install {pkg})")

    # MediaPipe API
    try:
        import mediapipe as mp
        version = getattr(mp, "__version__", "?")
        if not hasattr(mp, "solutions"):
            errors.append(
                f"mediapipe {version} без mp.solutions. "
                f"Откатись: uv pip install 'mediapipe==0.10.21'"
            )
    except Exception as e:
        errors.append(f"mediapipe сломан: {e}")

    # Camera probing can be skipped with SMILEGUN_SKIP_CAMERA=1 for headless tests.
    if os.environ.get("SMILEGUN_SKIP_CAMERA") == "1":
        warns.append("Проверка камеры пропущена (SMILEGUN_SKIP_CAMERA=1)")
    else:
        try:
            import cv2
            cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap = cv2.VideoCapture(0)
            ok = cap.isOpened()
            cap.release()
            if not ok:
                warns.append("OpenCV не открыл камеру 0. Это допустимо: основной режим использует browser getUserMedia.")
        except Exception as e:
            warns.append(f"Проверка камеры: {e}")

    # Audio assets.
    from pathlib import Path
    sound = Path(__file__).parent / "static" / "blaster.mp3"
    if not sound.exists():
        warns.append(f"{sound} не найден — запусти generate_sound.py (иначе выстрел без звука)")

    return errors, warns


def suppress_noise(level: str = "WARNING") -> None:
    """Suppress repetitive request noise while keeping errors visible.

    The main source is Werkzeug access logging such as
    `127.0.0.1 - - [date] "GET /state HTTP/1.1" 200 -`.
    The browser polls `/state` frequently, so those lines would otherwise flood
    the console. Set `SMILEGUN_VERBOSE=1` to restore detailed request logging.
    """
    log_level = logging.DEBUG if os.environ.get("SMILEGUN_VERBOSE") == "1" \
        else getattr(logging, level, logging.WARNING)

    # Werkzeug logs every HTTP request by default.
    for name in ("werkzeug", "werkzeug.serving", "werkzeug._internal"):
        logging.getLogger(name).setLevel(log_level)

    # Flask startup banners.
    try:
        from flask import cli
        cli.show_server_banner = lambda *a, **k: None
    except Exception:
        pass

    # Keep Flask's own logger so ERROR messages remain visible through lastResort.


def print_report():
    errors, warns = check_environment()
    lines = ["=" * 60, "  DIAGNOSTICS — smilegun", "=" * 60]
    lines += [f"  [!] {w}" for w in warns]
    lines += [f"  [X] {e}" for e in errors]
    if not errors and not warns:
        lines.append("  [OK] Всё чисто.")
    lines.append("=" * 60)
    if errors:
        lines.append("Запуск прерван из-за критических ошибок.")
    print("\n".join(lines))
    # Flush explicitly because redirected stdout may be block-buffered in files/IDEs.
    sys.stdout.flush()
    if errors:
        sys.exit(1)
    print()


if __name__ == "__main__":
    print_report()