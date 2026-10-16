from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")
import cv2

BACKENDS = [
    (cv2.CAP_DSHOW, "DSHOW"),
    (cv2.CAP_MSMF, "MSMF"),
    (cv2.CAP_ANY, "ANY"),
]

def main() -> int:
    print("SmileGun camera probe")
    print("OpenCV:", cv2.__version__)
    print("Python:", sys.version.split()[0])
    print("Ищу камеры на индексах 0..5...\n")
    found = []

    for index in range(6):
        for backend, name in BACKENDS:
            cap = None
            try:
                cap = cv2.VideoCapture(index, backend)
                opened = bool(cap.isOpened())
                got_frame = False
                shape = None
                if opened:
                    # Retry a few times because some USB cameras deliver the first frame late.
                    for _ in range(4):
                        ok, frame = cap.read()
                        if ok and frame is not None:
                            got_frame = True
                            shape = tuple(frame.shape[:2])
                            break
                        time.sleep(0.08)
                status = "OK" if opened and got_frame else ("OPEN/NO FRAME" if opened else "—")
                print(f"  camera {index} / {name:5s}: {status}", end="")
                if shape:
                    print(f"  frame={shape[1]}x{shape[0]}", end="")
                print()
                if opened and got_frame:
                    found.append((index, name))
            except Exception as exc:
                print(f"  camera {index} / {name:5s}: ERROR {type(exc).__name__}: {exc}")
            finally:
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass

    print()
    if found:
        print("Найдены рабочие варианты:")
        for index, name in found:
            print(f"  camera_index={index}, backend={name}")
        print("\nSmileGun теперь ищет эти индексы автоматически.")
        return 0

    print("Ни один вариант не дал кадр.")
    print("1) Полностью закрой Camera / Teams / Zoom / OBS / Discord и браузерные вкладки с камерой.")
    print("2) Windows → Параметры → Конфиденциальность и безопасность → Камера:")
    print("   включи доступ к камере и доступ для классических приложений.")
    print("3) Проверь камеру в стандартном приложении Windows «Камера».")
    print("4) Если там тоже нет изображения — переподключи USB/драйвер камеры.")
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
