import atexit
import math
import os
import statistics
import threading
import time

# Windows MSMF can fail to open a physical USB camera because of hardware
# transforms. OpenCV reads this variable while videoio initializes, so set it
# before importing cv2. Users can still override the value externally.
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

import cv2
import mediapipe as mp
from flask import Flask, Response, request, jsonify, render_template, send_file
from config import CFG
from diagnostics import suppress_noise
from werkzeug.serving import make_server

# A dedicated cleanup owner finishes native release even after an HTTP timeout.
_NativeCleanupThread = threading.Thread

# MediaPipe landmark constants.
LEFT_IRIS_CENTER = 473
RIGHT_IRIS_CENTER = 468
LEFT_EYE_OUTER = 362
LEFT_EYE_INNER = 263
RIGHT_EYE_OUTER = 133
RIGHT_EYE_INNER = 33
LEFT_EYE_TOP = 386
LEFT_EYE_BOTTOM = 374
RIGHT_EYE_TOP = 159
RIGHT_EYE_BOTTOM = 145
MOUTH_LEFT = 61
MOUTH_RIGHT = 291
LIP_TOP_INNER = 13
LIP_BOTTOM_INNER = 14

# Eyes narrower than this normalized width are invalid for ratio calculations.
MIN_EYE_WIDTH = 0.012

# The client receives aim coordinates normalized to the 0..1 game field.
FIELD_CENTER = 0.5

# Exponential moving average helper.
class EMA:
    def __init__(self, alpha: float):
        self.alpha = alpha
        self.value = None

    def update(self, new_value: float) -> float:
        if self.value is None:
            self.value = new_value
        else:
            self.value = self.alpha * new_value + (1 - self.alpha) * self.value
        return self.value

# Adaptive One Euro filter: stable at rest and responsive during fast motion.
# This is a good fit for hand and gaze tracking.
class OneEuro:
    def __init__(self, min_cutoff: float = 1.2, beta: float = 0.3,
                 d_cutoff: float = 1.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.x_prev = None
        self.dx_prev = 0.0
        self.t_prev = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * 3.141592653589793 * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def update(self, x: float, t: float) -> float:
        if self.x_prev is None:
            self.x_prev = x
            self.t_prev = t
            return x
        dt = t - self.t_prev
        if dt <= 0:
            dt = 1e-3
        self.t_prev = t
        dx = (x - self.x_prev) / dt
        dx = self._alpha(self.d_cutoff, dt) * dx + \
            (1.0 - self._alpha(self.d_cutoff, dt)) * self.dx_prev
        self.dx_prev = dx
        cutoff = self.min_cutoff + self.beta * abs(dx)
        a = self._alpha(cutoff, dt)
        self.x_prev = a * x + (1.0 - a) * self.x_prev
        return self.x_prev

def clamp01(value: float) -> float:
    """Clamp normalized aim coordinates to the game field."""
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)

def eye_gaze(iris_x: float, iris_y: float, outer_x: float, inner_x: float,
             top_y: float, bottom_y: float) -> tuple[float, float, float]:
    """Return one-eye gaze in units of that eye's own size.

    The iris is normalized around the eye center instead of the whole frame.
    Head translation therefore moves the eye and iris together while the ratio
    remains stable. The third return value is eye width, used to normalize mouth
    metrics so smile detection is less sensitive to camera distance.
    """
    eye_w = max(abs(outer_x - inner_x), MIN_EYE_WIDTH)
    eye_h = max(abs(top_y - bottom_y), MIN_EYE_WIDTH)
    center_x = (outer_x + inner_x) / 2.0
    center_y = (top_y + bottom_y) / 2.0
    return (iris_x - center_x) / eye_w, (iris_y - center_y) / eye_h, eye_w

def apply_deadzone(value: float, deadzone: float) -> float:
    """Apply a continuous dead zone with no jump at its boundary."""
    if deadzone <= 0:
        return value
    if abs(value) <= deadzone:
        return 0.0
    sign = 1.0 if value > 0 else -1.0
    return sign * (abs(value) - deadzone) / (1.0 - deadzone)

def gaze_to_aim(gaze_x: float, gaze_y: float) -> tuple[float, float]:
    """Legacy gaze-to-aim mapping; current gameplay uses hand position."""
    dx = apply_deadzone(gaze_x - CFG.gaze_neutral_x, CFG.gaze_deadzone)
    dy = apply_deadzone(gaze_y - CFG.gaze_neutral_y, CFG.gaze_deadzone)
    offset_x = dx * CFG.iris_gain
    offset_y = dy * CFG.iris_gain * CFG.iris_gain_y
    return clamp01(FIELD_CENTER + offset_x), clamp01(FIELD_CENTER + offset_y)

# Camera and MediaPipe tracking state.
class CameraTracker:
    def __init__(self):
        self.cap = None
        self._capture_backend = None
        self._capture_index = None
        self._capture_cleanup_thread = None
        self._capture_cleanup_error = None
        # Browser fallback: the browser owns the webcam via getUserMedia and
        # sends compressed frames to Flask. This bypasses broken DSHOW/MSMF
        # drivers/permissions while keeping MediaPipe processing in Python.
        self.browser_mode = False
        self.running = False
        self.lock = threading.Lock()
        self.lifecycle_lock = threading.RLock()
        self._stop_event = threading.Event()
        self.thread = None
        self._closed = False
        self._shutdown_requested = False
        self._resources_closed = False
        self.frame = None
        self.state = {
            "camera": False,
            "aim_x": 0.5,
            "aim_y": 0.5,
            "iris_x": 0.0,
            "iris_y": 0.0,
            "gaze_dx": 0.0,
            "gaze_dy": 0.0,
            "neutral_x": CFG.gaze_neutral_x,
            "neutral_y": CFG.gaze_neutral_y,
            "smile_score": 0.0,
            "mouth_open_score": 0.0,
            "smile": False,
            "mouth_open": False,
            "calibrating": False,
            "shot_id": 0,
            "smile_shot_id": 0,  # compatibility: now counts smile events
            "smile_id": 0,
            "mouth_shot_id": 0,
            "fist_shot_id": 0,
            "hand_present": False,
            "hand_x": 0.5,
            "hand_y": 0.5,
            "fist": False,
            "hand_count": 0,
            "peace": False,
            "two_palms": False,
            "two_palms_id": 0,
            "super_charge": 0.0,
            "super_ready": False,
            "super_id": 0,
            "blink_id": 0,
            "blink_count": 0,
            "charging": False,
            # Camera diagnostics distinguish no device, no detected face, and
            # a busy device while frames are otherwise arriving.
            "frames_read": 0,
            "face": False,
            "fps": 0.0,
            "camera_open": False,
            "camera_index": CFG.camera_index,
            "camera_backend": None,
            "browser_camera": False,
            "error": None,
        }
        self._calib_samples = None
        self._prev_smile = False
        self._last_smile_shot_at = -float("inf")
        self._prev_mouth_open = False
        self._last_mouth_shot_at = -float("inf")
        self._prev_two_palms = False
        self._prev_fist = False
        self._fist_held = False
        self._prev_blink = False
        self._charge = 0.0
        self._charge_updated_at = time.monotonic()
        self._last_blink_time = None
        self._charging_until = 0.0
        self._held = {"smile": False, "mouth_open": False}
        self._fps_window_start = time.monotonic()
        self._fps_frames = 0
        self.face_mesh = mp.solutions.face_mesh.FaceMesh(
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
            refine_landmarks=True,
            max_num_faces=1
        )
        self.hands = mp.solutions.hands.Hands(
            static_image_mode=False,
            max_num_hands=2,
            min_detection_confidence=0.6,
            min_tracking_confidence=0.5,
        )
        # One Euro filtering reduces hand/gaze jitter better than a plain EMA.
        self.gaze_flt_x = OneEuro(min_cutoff=1.2, beta=0.3)
        self.gaze_flt_y = OneEuro(min_cutoff=1.2, beta=0.3)
        self.hand_flt_x = OneEuro(min_cutoff=1.0, beta=0.2)
        self.hand_flt_y = OneEuro(min_cutoff=1.0, beta=0.2)
        self.smile_ema = EMA(CFG.smoothing_alpha)
        self.mouth_ema = EMA(CFG.smoothing_alpha)

    CALIB_FRAMES = 25
    BLINK_RATIO = 0.15          # eye openness below this threshold means closed
    MAX_READ_FAILURES = 60      # roughly two seconds without frames
    REOPEN_COOLDOWN = 5.0       # seconds between camera reopen attempts
    STOP_TIMEOUT = 3.0          # avoid blocking HTTP if a driver hangs in read()
    CHARGE_PULSE_SECONDS = 0.2
    CAPTURE_BACKENDS = (
        (cv2.CAP_DSHOW, "DSHOW"),
        (cv2.CAP_MSMF, "MSMF"),
        (cv2.CAP_ANY, "ANY"),
    )
    CAMERA_SCAN_MAX_INDEX = 5  # preferred camera plus indexes 0..5

    def _update_charge(self, now: float) -> None:
        """Integrate charge decay once, independently of frame/polling rate."""
        if self._last_blink_time is not None:
            decay_from = max(self._charge_updated_at,
                             self._last_blink_time + CFG.super_decay_delay)
            elapsed = max(0.0, now - decay_from)
            self._charge = max(0.0, self._charge - elapsed * CFG.super_decay_rate)
        self._charge_updated_at = now
        self.state.update({
            "super_charge": self._charge,
            "super_ready": self._charge >= 1.0 or math.isclose(
                self._charge, 1.0, abs_tol=1e-12, rel_tol=0.0),
            "charging": now < self._charging_until,
        })

    def _reset_tracking(self, preserve_progress: bool = False) -> None:
        """Drop stale frame/gesture state while keeping short-lived progress."""
        now = time.monotonic()
        self.frame = None
        if not preserve_progress:
            self._calib_samples = None
            self._charge = 0.0
            self._last_blink_time = None
            self._charge_updated_at = now
            self._charging_until = 0.0
            self.state["camera_backend"] = None
            self.state["camera_index"] = CFG.camera_index
            self._capture_index = None
            self._last_smile_shot_at = -float("inf")
            self._last_mouth_shot_at = -float("inf")
        else:
            self._update_charge(now)
        self._prev_fist = False
        self._prev_smile = False
        self._prev_mouth_open = False
        self._prev_two_palms = False
        self._held = {"smile": False, "mouth_open": False}
        self._fist_held = False
        self._prev_blink = False
        self._fps_window_start = time.monotonic()
        self._fps_frames = 0
        self.gaze_flt_x = OneEuro(min_cutoff=1.2, beta=0.3)
        self.gaze_flt_y = OneEuro(min_cutoff=1.2, beta=0.3)
        self.hand_flt_x = OneEuro(min_cutoff=1.0, beta=0.2)
        self.hand_flt_y = OneEuro(min_cutoff=1.0, beta=0.2)
        self.smile_ema = EMA(CFG.smoothing_alpha)
        self.mouth_ema = EMA(CFG.smoothing_alpha)
        self.state.update({
            "camera": False, "camera_open": False, "face": False,
            "hand_present": False, "fist": False, "hand_count": 0,
            "peace": False, "two_palms": False, "fps": 0.0,
            "aim_x": FIELD_CENTER, "aim_y": FIELD_CENTER,
            "hand_x": FIELD_CENTER, "hand_y": FIELD_CENTER,
            "iris_x": 0.0, "iris_y": 0.0, "gaze_dx": 0.0, "gaze_dy": 0.0,
            "smile_score": 0.0, "mouth_open_score": 0.0,
            "smile": False, "mouth_open": False,
            "calibrating": self._calib_samples is not None,
            "super_charge": self._charge,
            "super_ready": self._charge >= 1.0 or math.isclose(
                self._charge, 1.0, abs_tol=1e-12, rel_tol=0.0),
            "blink_count": 0, "charging": now < self._charging_until,
        })

    def start_calibration(self) -> None:
        """Capture neutral gaze from the next CALIB_FRAMES valid samples."""
        with self.lock:
            self._calib_samples = []
            self.state["calibrating"] = True

    @staticmethod
    def _robust_center(values: list[float]) -> float:
        """Robust median with MAD-based outlier rejection.

        A plain mean lets a blink or iris spike pull the neutral point away.
        Median filtering keeps calibration near the real gaze center.
        """
        med = statistics.median(values)
        mad = statistics.median([abs(v - med) for v in values])
        if mad == 0:
            return med
        tol = 3.0 * 1.4826 * mad
        keep = [v for v in values if abs(v - med) <= tol]
        return sum(keep) / len(keep) if keep else med

    def _collect_calibration(self, gaze_x: float, gaze_y: float,
                             blink: bool = False) -> None:
        if self._calib_samples is None:
            return
        if blink:
            # Ignore blink frames because missing/occluded iris points corrupt calibration.
            return
        self._calib_samples.append((gaze_x, gaze_y))
        if len(self._calib_samples) < self.CALIB_FRAMES:
            return
        xs = [sample[0] for sample in self._calib_samples]
        ys = [sample[1] for sample in self._calib_samples]
        CFG.gaze_neutral_x = self._robust_center(xs)
        CFG.gaze_neutral_y = self._robust_center(ys)
        self._calib_samples = None
        CFG.save()

    def _threshold(self, key: str, value: float, threshold: float) -> bool:
        """Apply threshold hysteresis for stable gesture edges.

        A state activates above threshold and releases below a lower ratio so
        values oscillating near the boundary do not emit repeated events.
        """
        if key == "smile":
            ratio = CFG.smile_release_ratio
        elif key == "mouth_open":
            ratio = CFG.mouth_open_release_ratio
        else:
            ratio = CFG.smile_hysteresis
        release = threshold * ratio
        if self._held[key]:
            if value < release:
                self._held[key] = False
        elif value > threshold:
            self._held[key] = True
        return self._held[key]

    def _make_mesh(self, frame):
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self.face_mesh.process(frame_rgb)
        if results.multi_face_landmarks:
            return results.multi_face_landmarks[0]
        return None

    HAND_PALM = (0, 5, 9, 13, 17)   # wrist + finger bases define palm center
    HAND_TIPS = (8, 12, 16, 20)     # fingertip landmarks

    def _detect_hands(self, frame):
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self.hands.process(frame_rgb)
        return list(results.multi_hand_landmarks or [])

    @staticmethod
    def _hand_info(hand_lm):
        """Return palm centre, openness and simple finger gestures.

        The V-sign is deliberately conservative: index + middle extended,
        ring + pinky folded. Two open palms are handled separately.
        """
        lm = hand_lm.landmark
        cx = sum(lm[i].x for i in CameraTracker.HAND_PALM) / len(CameraTracker.HAND_PALM)
        cy = sum(lm[i].y for i in CameraTracker.HAND_PALM) / len(CameraTracker.HAND_PALM)
        scale = math.hypot(lm[0].x - lm[9].x, lm[0].y - lm[9].y) + 1e-6
        d = sum(math.hypot(lm[i].x - cx, lm[i].y - cy) for i in CameraTracker.HAND_TIPS) / len(CameraTracker.HAND_TIPS)
        openness = d / scale

        # MediaPipe finger tips/PIPs: 8/6, 12/10, 16/14, 20/18.
        margin = 0.035 * max(0.55, min(1.8, scale / 0.15))
        extended = [lm[tip].y < lm[pip].y - margin for tip, pip in ((8, 6), (12, 10), (16, 14), (20, 18))]
        v_sign = extended[0] and extended[1] and not extended[2] and not extended[3]
        open_palm = openness > CFG.two_palms_threshold and sum(extended) >= 3
        return {
            "lm": hand_lm, "cx": cx, "cy": cy, "openness": openness,
            "v_sign": v_sign, "open_palm": open_palm,
        }

    def _update_hands(self, hand_landmarks) -> None:
        infos = [self._hand_info(item) for item in hand_landmarks]
        self.state["hand_count"] = len(infos)
        self.state["peace"] = any(info["v_sign"] for info in infos)
        self.state["two_palms"] = len(infos) >= 2 and all(info["open_palm"] for info in infos[:2])

        if not infos:
            self.state["hand_present"] = False
            self.state["fist"] = False
            self._fist_held = False
            self.hand_flt_x = OneEuro(min_cutoff=1.0, beta=0.2)
            self.hand_flt_y = OneEuro(min_cutoff=1.0, beta=0.2)
            return

        # Keep the aiming hand stable when a second hand appears: use the hand
        # closest to the previous tracked palm.
        old_x = float(self.state.get("hand_x", 0.5))
        old_y = float(self.state.get("hand_y", 0.5))
        info = min(infos, key=lambda x: (x["cx"] - old_x) ** 2 + (x["cy"] - old_y) ** 2)
        openness = info["openness"]

        # Two-palms pause gesture takes priority and must never look like a shot.
        if self.state["two_palms"]:
            self._fist_held = False
        elif self._fist_held:
            if openness > CFG.fist_release:
                self._fist_held = False
        elif openness < CFG.fist_threshold:
            self._fist_held = True

        now = time.monotonic()
        hx = self.hand_flt_x.update(info["cx"], now)
        hy = self.hand_flt_y.update(info["cy"], now)
        self.state["hand_present"] = True
        self.state["hand_x"] = hx
        self.state["hand_y"] = hy
        self.state["fist"] = self._fist_held

    def _aim_from_hand(self):
        """Map palm position directly to normalized game coordinates.

        X is mirrored because selfie camera coordinates move opposite to the
        player's intuitive on-screen direction.
        """
        hx = 1.0 - self.state["hand_x"]
        hy = self.state["hand_y"]
        ax = clamp01(FIELD_CENTER + (hx - FIELD_CENTER) * CFG.hand_gain)
        ay = clamp01(FIELD_CENTER + (hy - FIELD_CENTER) * CFG.hand_gain)
        return ax, ay

    def _camera_indices(self) -> list[int]:
        """Return camera indexes with the preferred one first, then 0..max.

        SMILEGUN_CAMERA_INDEX=N can temporarily override the preferred index on
        Windows systems with OBS/virtual cameras.
        """
        preferred = CFG.camera_index
        env_index = os.environ.get("SMILEGUN_CAMERA_INDEX")
        if env_index is not None:
            try:
                preferred = max(0, int(env_index))
            except (TypeError, ValueError):
                pass
        indices = [preferred]
        # Reuse the last working index first on reconnect instead of scanning from zero.
        if self._capture_index is not None and self._capture_index not in indices:
            indices.insert(0, self._capture_index)
        for index in range(self.CAMERA_SCAN_MAX_INDEX + 1):
            if index not in indices:
                indices.append(index)
        return indices

    def _open_capture(self, advance_backend: bool = False) -> None:
        """Find a physical camera and a working Windows capture backend.

        Scan the preferred index and then 0..5, trying DSHOW, MSMF and ANY for
        each candidate. This handles systems where virtual devices occupy index 0.
        """
        if self.cap is not None:
            self.cap.release()
            self.cap = None

        backends = list(self.CAPTURE_BACKENDS)
        if advance_backend:
            for position, (backend, _) in enumerate(backends):
                if backend == self._capture_backend:
                    offset = (position + 1) % len(backends)
                    backends = backends[offset:] + backends[:offset]
                    break

        indices = self._camera_indices()
        failures = []
        with self.lock:
            self.state["camera_backend"] = None
            self.state["camera_open"] = False

        for index in indices:
            for backend, name in backends:
                if self._stop_event.is_set() or self._shutdown_requested:
                    break
                capture = None
                try:
                    capture = cv2.VideoCapture(index, backend)
                    if capture.isOpened():
                        # A minimal capture buffer reduces gesture latency; unsupported drivers may ignore it.
                        try:
                            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                        except Exception:
                            pass
                        self.cap = capture
                        self._capture_backend = backend
                        self._capture_index = index
                        selected_name = name
                        try:
                            actual_name = capture.getBackendName()
                            if isinstance(actual_name, str) and actual_name:
                                selected_name = actual_name
                        except Exception:
                            pass
                        with self.lock:
                            self.state["camera_index"] = index
                            self.state["camera_backend"] = selected_name
                            self.state["camera_open"] = True
                        return
                    failures.append(f"№{index}/{name}: не открылась")
                except Exception as exc:
                    detail = " ".join(str(exc).split())[:120]
                    failures.append(f"№{index}/{name}: {type(exc).__name__}: {detail}")
                finally:
                    if capture is not None and capture is not self.cap:
                        try:
                            capture.release()
                        except Exception:
                            pass
            if self._stop_event.is_set() or self._shutdown_requested:
                break

        # Do not keep a stale camera index after a failed scan.
        self._capture_index = None
        with self.lock:
            self.state["camera_index"] = CFG.camera_index

        details = "; ".join(failures[-18:])
        if self._stop_event.is_set() or self._shutdown_requested:
            raise RuntimeError("Запуск камеры отменён: запрошена остановка")
        tried = ", ".join(map(str, indices))
        raise RuntimeError(
            f"Не удалось открыть веб-камеру. Проверены индексы [{tried}] и "
            f"DSHOW/MSMF/ANY ({details}). Закройте Camera/Teams/Zoom/OBS и "
            "вкладки с камерой. В Windows включите «Параметры → "
            "Конфиденциальность и безопасность → Камера → Разрешить классическим "
            "приложениям доступ к камере». Для ручного выбора можно запустить "
            "$env:SMILEGUN_CAMERA_INDEX='1'; uv run python app.py"
        )

    def start_browser(self) -> None:
        """Start browser-fed camera mode.

        The webcam itself is opened by navigator.mediaDevices.getUserMedia().
        Frames arrive through /camera/browser/frame.  This avoids OpenCV
        VideoCapture completely, which is important on Windows machines where
        DSHOW/MSMF cannot open the device even though the browser can.
        """
        with self.lifecycle_lock:
            if self._closed or self._shutdown_requested:
                raise RuntimeError("Трекер камеры уже закрыт")
            if self.thread is not None and self.thread.is_alive():
                raise RuntimeError("Сначала остановите нативную камеру")
            if self.cap is not None:
                try:
                    self.cap.release()
                finally:
                    self.cap = None
            with self.lock:
                self._stop_event.clear()
                self._reset_tracking()
                self.browser_mode = True
                self.running = True
                self.state.update({
                    "camera_open": True,
                    "camera_backend": "BROWSER",
                    "camera_index": -1,
                    "browser_camera": True,
                    "error": None,
                })
                self._fps_window_start = time.monotonic()
                self._fps_frames = 0

    def submit_browser_frame(self, frame) -> None:
        """Process one frame captured by the browser."""
        if frame is None or getattr(frame, "size", 0) == 0:
            raise ValueError("Пустой кадр")
        with self.lock:
            if self._shutdown_requested or not self.browser_mode or not self.running:
                raise RuntimeError("Браузерная камера не запущена")
            self.frame = frame
            self.state["camera_open"] = True
            self.state["camera_backend"] = "BROWSER"
            self.state["camera_index"] = -1
            self.state["browser_camera"] = True
            self.state["error"] = None
            self.state["frames_read"] += 1
            self._fps_frames += 1
            now = time.monotonic()
            if now - self._fps_window_start >= 1.0:
                self.state["fps"] = self._fps_frames / (now - self._fps_window_start)
                self._fps_window_start = now
                self._fps_frames = 0
            try:
                self._process_frame(frame)
            except Exception as exc:
                self._reset_tracking(preserve_progress=True)
                self.browser_mode = True
                self.running = True
                self.state["camera_open"] = True
                self.state["camera_backend"] = "BROWSER"
                self.state["camera_index"] = -1
                self.state["browser_camera"] = True
                self.state["error"] = f"{type(exc).__name__}: {exc}"
                raise

    def _stop_browser(self) -> None:
        with self.lock:
            self.browser_mode = False
            self.running = False
            self._stop_event.set()
            self._reset_tracking()
            self.state["browser_camera"] = False
            self.state["error"] = None

    def start(self):
        with self.lifecycle_lock:
            if self._closed or self._shutdown_requested:
                raise RuntimeError("Трекер камеры уже закрыт")
            if self._capture_cleanup_thread is not None and self._capture_cleanup_thread.is_alive():
                raise RuntimeError("Предыдущая камера ещё освобождается")
            if self.thread is not None and self.thread.is_alive():
                if self.running:
                    return
                raise RuntimeError("Предыдущий поток камеры ещё останавливается")
            with self.lock:
                if self._shutdown_requested:
                    raise RuntimeError("Приложение завершает работу")
                self._stop_event.clear()
                self._reset_tracking()
                self.browser_mode = False
                self.state["browser_camera"] = False
                self.state["error"] = None
            try:
                self._open_capture()
                if self._stop_event.is_set() or self._shutdown_requested:
                    raise RuntimeError("Запуск камеры отменён: запрошена остановка")
            except Exception as exc:
                if self.cap is not None:
                    self.cap.release()
                    self.cap = None
                with self.lock:
                    self.state["error"] = str(exc)
                raise
            with self.lock:
                self.state["camera_open"] = True
                self.running = True
                self.thread = threading.Thread(target=self._loop, daemon=True)
            try:
                self.thread.start()
            except Exception as exc:
                self.cap.release()
                self.cap = None
                self.thread = None
                with self.lock:
                    self.running = False
                    self._reset_tracking()
                    self.state["error"] = str(exc)
                raise

    def stop(self):
        # Browser-fed mode has no VideoCapture/read worker to join.
        if self.browser_mode:
            with self.lifecycle_lock:
                self._stop_browser()
            return

        deadline = time.monotonic() + self.STOP_TIMEOUT
        self.running = False
        self._stop_event.set()

        def remaining():
            return max(0.0, deadline - time.monotonic())

        def fail(message):
            # Native inference may hold state.lock while stalled; error responses must not wait for it.
            if self.lock.acquire(blocking=False):
                try:
                    self.state["error"] = message
                finally:
                    self.lock.release()
            raise RuntimeError(message)

        if not self.lifecycle_lock.acquire(timeout=remaining()):
            fail("Камера ещё останавливается: операция запуска не завершена")
        try:
            thread = self.thread
            # The capture thread releases the camera itself; calling release() during read() can crash OpenCV.
            if thread is not None:
                thread.join(timeout=remaining())
                if thread.is_alive():
                    fail("Камера ещё останавливается: чтение или распознавание кадра не завершено")
            if self.cap is not None:
                if self._capture_cleanup_thread is None or not self._capture_cleanup_thread.is_alive():
                    capture = self.cap
                    self._capture_cleanup_error = None

                    def release_capture():
                        try:
                            capture.release()
                        except Exception as exc:
                            self._capture_cleanup_error = str(exc)
                        else:
                            if self.cap is capture:
                                self.cap = None

                    self._capture_cleanup_thread = _NativeCleanupThread(
                        target=release_capture, name="SmileGunCaptureCleanup", daemon=True)
                    self._capture_cleanup_thread.start()
                self._capture_cleanup_thread.join(timeout=remaining())
                if self._capture_cleanup_thread.is_alive():
                    fail("Камера ещё останавливается: устройство не завершило освобождение")
                if self._capture_cleanup_error is not None:
                    fail("Не удалось освободить камеру: " + self._capture_cleanup_error)
                self._capture_cleanup_thread = None
            if not self.lock.acquire(timeout=remaining()):
                fail("Камера ещё останавливается: обработка состояния не завершена")
            try:
                self._reset_tracking()
                self.state["error"] = None
            finally:
                self.lock.release()
            self.thread = None
        finally:
            self.lifecycle_lock.release()

    def close(self):
        """Close camera resources and MediaPipe graphs during server shutdown."""
        if self._closed:
            return self._resources_closed
        self._closed = True
        try:
            self.stop()
        except RuntimeError:
            # The external shutdown watchdog covers native cleanup stalls.
            return False
        self.face_mesh.close()
        self.hands.close()
        self._resources_closed = True
        return True

    def request_shutdown(self):
        """Reject new camera starts while HTTP shutdown proceeds without native waits."""
        self._shutdown_requested = True
        self.browser_mode = False
        self.running = False
        self._stop_event.set()

    def _loop(self):
        read_failures = 0
        last_reopen = 0.0
        try:
            while not self._stop_event.is_set():
                ret, frame = self.cap.read() if self.cap is not None else (False, None)
                if self._stop_event.is_set():
                    break
                if not ret:
                    read_failures += 1
                    with self.lock:
                        if read_failures == 1:
                            self._reset_tracking(preserve_progress=True)
                        self.state["camera_open"] = self.cap is not None and self.cap.isOpened()
                    if read_failures >= self.MAX_READ_FAILURES:
                        now = time.monotonic()
                        if now - last_reopen >= self.REOPEN_COOLDOWN:
                            last_reopen = now
                            try:
                                self._open_capture(advance_backend=True)
                            except RuntimeError as exc:
                                if self._stop_event.is_set():
                                    break
                                with self.lock:
                                    self.state["error"] = str(exc)
                            else:
                                if self._stop_event.is_set():
                                    break
                                with self.lock:
                                    self.state["error"] = (
                                        "Камера открыта, но не отдаёт кадры. "
                                        "Пробуем другой способ подключения."
                                    )
                        with self.lock:
                            if not self.state["error"]:
                                self.state["error"] = "Камера не отдаёт кадры."
                    self._stop_event.wait(0.02)
                    continue
                read_failures = 0
                with self.lock:
                    if self._stop_event.is_set():
                        break
                    self.frame = frame
                    self.state["error"] = None
                    self.state["frames_read"] += 1
                    self._fps_frames += 1
                    now = time.monotonic()
                    if now - self._fps_window_start >= 1.0:
                        self.state["fps"] = self._fps_frames / (now - self._fps_window_start)
                        self._fps_window_start = now
                        self._fps_frames = 0
                    try:
                        self._process_frame(frame)
                    except Exception as exc:
                        self._reset_tracking(preserve_progress=True)
                        self.state["camera_open"] = self.cap.isOpened()
                        self.state["error"] = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            with self.lock:
                self.state["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if self.cap is not None:
                self.cap.release()
                self.cap = None
            with self.lock:
                self.running = False
                self.browser_mode = False
                self._reset_tracking()
                self.state["browser_camera"] = False

    def _process_frame(self, frame):
        hand_landmarks = self._detect_hands(frame)
        self._update_hands(hand_landmarks)
        two_palms_now = bool(self.state.get("two_palms"))
        if two_palms_now and not self._prev_two_palms:
            self.state["two_palms_id"] += 1
        self._prev_two_palms = two_palms_now

        mesh = self._make_mesh(frame)
        self.state["face"] = bool(mesh)

        now = time.monotonic()
        self._update_charge(now)

        # A fist edge emits the regular single-shot event.
        shots = 0
        fist_now = self.state["hand_present"] and self.state["fist"]
        if fist_now and not self._prev_fist:
            shots = 1
            self.state["fist_shot_id"] += 1
        self._prev_fist = fist_now

        if not mesh:
            # Without a face there is no blink/smile input, but hand aiming still works.
            if self.state["hand_present"]:
                ax, ay = self._aim_from_hand()
            else:
                ax, ay = self.state["aim_x"], self.state["aim_y"]
            # Losing face tracking must not count as a completed blink when it returns.
            self._prev_blink = False
            self._prev_smile = False
            self._prev_mouth_open = False
            self._held["smile"] = False
            self._held["mouth_open"] = False
            self.smile_ema = EMA(CFG.smoothing_alpha)
            self.mouth_ema = EMA(CFG.smoothing_alpha)
            self.gaze_flt_x = OneEuro(min_cutoff=1.2, beta=0.3)
            self.gaze_flt_y = OneEuro(min_cutoff=1.2, beta=0.3)
            self.state.update({
                "camera": True,
                "aim_x": ax,
                "aim_y": ay,
                "smile_score": 0.0,
                "smile": False,
                "mouth_open_score": 0.0,
                "mouth_open": False,
                "iris_x": 0.0,
                "iris_y": 0.0,
                "gaze_dx": 0.0,
                "gaze_dy": 0.0,
                "calibrating": self._calib_samples is not None,
                "shot_id": self.state["shot_id"] + shots,
            })
            return

        lm = mesh.landmark

        # Gaze is measured as iris offset from each eye center in eye-width units.
        gaze_lx, gaze_ly, eye_lw = eye_gaze(lm[LEFT_IRIS_CENTER].x, lm[LEFT_IRIS_CENTER].y,
                                            lm[LEFT_EYE_OUTER].x, lm[LEFT_EYE_INNER].x,
                                            lm[LEFT_EYE_TOP].y, lm[LEFT_EYE_BOTTOM].y)
        gaze_rx, gaze_ry, eye_rw = eye_gaze(lm[RIGHT_IRIS_CENTER].x, lm[RIGHT_IRIS_CENTER].y,
                                            lm[RIGHT_EYE_OUTER].x, lm[RIGHT_EYE_INNER].x,
                                            lm[RIGHT_EYE_TOP].y, lm[RIGHT_EYE_BOTTOM].y)
        raw_gx = (gaze_lx + gaze_rx) / 2.0
        raw_gy = (gaze_ly + gaze_ry) / 2.0
        gaze_x = self.gaze_flt_x.update(raw_gx, now)
        gaze_y = self.gaze_flt_y.update(raw_gy, now)
        eye_w = (eye_lw + eye_rw) / 2.0     # face scale used for normalized mouth metrics

        # Blink state uses normalized eye openness below the closed-eye threshold.
        left_open = abs(lm[LEFT_EYE_TOP].y - lm[LEFT_EYE_BOTTOM].y)
        right_open = abs(lm[RIGHT_EYE_TOP].y - lm[RIGHT_EYE_BOTTOM].y)
        eye_open = (left_open + right_open) / 2.0
        blink = (eye_open / eye_w) < self.BLINK_RATIO

        # Closed -> open emits one charge event; holding eyes closed never spams charge.
        completed_blink = not blink and self._prev_blink
        if completed_blink:
            self._charge = min(1.0, self._charge + CFG.super_blink_gain)
            self._last_blink_time = now
            self._charge_updated_at = now
            self._charging_until = now + self.CHARGE_PULSE_SECONDS
            self.state["blink_id"] += 1
        self._prev_blink = blink

        # Reaching 100% never fires SUPER automatically; a new smile releases it.
        super_shot = 0

        # Smile is now a bonus event. At 100% charge the same NEW smile also
        # releases the super. Holding a smile cannot retrigger either action.
        mouth_w = abs(lm[MOUTH_LEFT].x - lm[MOUTH_RIGHT].x)
        smile_score = self.smile_ema.update(mouth_w / eye_w)
        smile_now = self._threshold("smile", smile_score, CFG.smile_threshold)
        smile_edge = smile_now and not self._prev_smile
        self._prev_smile = smile_now
        if smile_edge and now - self._last_smile_shot_at >= CFG.smile_shot_cooldown:
            self.state["smile_id"] += 1
            self.state["smile_shot_id"] += 1
            if self._charge >= 1.0 or math.isclose(self._charge, 1.0, abs_tol=1e-12, rel_tol=0.0):
                super_shot = 1
                self._charge = 0.0
                self._charge_updated_at = now
                self._charging_until = 0.0
            self._last_smile_shot_at = now

        # Open mouth = one spread-shot trigger (front edge + hysteresis + cooldown).
        mouth_h = abs(lm[LIP_TOP_INNER].y - lm[LIP_BOTTOM_INNER].y)
        mouth_open_score = self.mouth_ema.update(mouth_h / eye_w)
        mouth_now = self._threshold("mouth_open", mouth_open_score, CFG.mouth_open_threshold)
        mouth_edge = mouth_now and not self._prev_mouth_open
        self._prev_mouth_open = mouth_now
        if mouth_edge and now - self._last_mouth_shot_at >= CFG.mouth_shot_cooldown:
            shots += 1
            self.state["mouth_shot_id"] += 1
            self._last_mouth_shot_at = now

        self._collect_calibration(raw_gx, raw_gy, blink)

        # Gameplay aiming is controlled by the hand only.
        if self.state["hand_present"]:
            aim_x, aim_y = self._aim_from_hand()
        else:
            aim_x, aim_y = self.state["aim_x"], self.state["aim_y"]

        # Publish the latest tracking state.
        self.state.update({
            "camera": True,
            "aim_x": aim_x,
            "aim_y": aim_y,
            "iris_x": gaze_x,
            "iris_y": gaze_y,
            "gaze_dx": gaze_x - CFG.gaze_neutral_x,
            "gaze_dy": gaze_y - CFG.gaze_neutral_y,
            "neutral_x": CFG.gaze_neutral_x,
            "neutral_y": CFG.gaze_neutral_y,
            "smile_score": smile_score,
            "mouth_open_score": mouth_open_score,
            "smile": smile_now,
            "mouth_open": mouth_now,
            "fist": fist_now,
            "super_charge": self._charge,
            "super_ready": self._charge >= 1.0 or math.isclose(
                self._charge, 1.0, abs_tol=1e-12, rel_tol=0.0),
            "blink_count": 0,
            "charging": now < self._charging_until,
            "calibrating": self._calib_samples is not None,
            "shot_id": self.state["shot_id"] + shots,
            "super_id": self.state["super_id"] + super_shot,
        })

    def get_frame(self):
        with self.lock:
            return self.frame

    def get_state(self):
        with self.lock:
            self._update_charge(time.monotonic())
            # Return a copy so jsonify can serialize outside the lock while tracking continues.
            state = dict(self.state)
            state["camera_running"] = self.running
            return state

# Shared tracker instance.
tracker = CameraTracker()
atexit.register(tracker.close)

# Flask application.
app = Flask(__name__)
app.config["SHUTDOWN_CALLBACK"] = None
app.config["SHUTDOWN_REQUESTED"] = False
_shutdown_request_lock = threading.Lock()

# MJPEG stream helper.
def mjpeg_stream():
    while True:
        frame = tracker.get_frame()
        if frame is None:
            if not tracker.running:
                return
            # Yield briefly so the MJPEG loop does not monopolize the GIL and starve /state.
            time.sleep(0.02)
            continue
        ret, buffer = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        frame = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        time.sleep(1.0 / 30.0)

# Main game page.
@app.route('/')
def index():
    # The game page is rendered from templates/index.html.
    return render_template('index.html')

# MJPEG preview route for the OpenCV fallback.


@app.route('/mobile-ca.crt')
def mobile_ca_certificate():
    """Download the local CA used by run_mobile.ps1 (when generated)."""
    try:
        from mobile_tls import CA_CERT
    except Exception:
        return jsonify({"error": "Mobile TLS helper unavailable"}), 404
    if not CA_CERT.exists():
        return jsonify({"error": "Mobile CA has not been generated. Run run_mobile.ps1 first."}), 404
    return send_file(CA_CERT, mimetype="application/x-x509-ca-cert", as_attachment=True, download_name="smilegun-mobile-ca.crt")

@app.route('/video_feed')
def video_feed():
    return Response(mjpeg_stream(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

# Tracking/game-input state API.
@app.route('/state')
def state():
    return jsonify(tracker.get_state())

# Configuration read/update API.
@app.route('/config', methods=['GET', 'POST'])
def config():
    if request.method == 'POST':
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Ожидается JSON-объект настроек"}), 400
        bounds = {
            "field_width": (480, 1600, int),
            "field_height": (360, 1200, int),
            "level_duration": (10, 50, int),
            "level_difficulty": (1, 5, int),
            "super_blink_count": (2, 10, int),
            "super_blink_gain": (0.05, 1.0, float),
            "super_decay_delay": (0.5, 15.0, float),
            "super_decay_rate": (0.0, 0.5, float),
            "smile_threshold": (1.0, 3.0, float),
            "smile_release_ratio": (0.5, 0.95, float),
            "smile_shot_cooldown": (0.1, 3.0, float),
            "mouth_open_threshold": (0.12, 0.8, float),
            "mouth_open_release_ratio": (0.4, 0.95, float),
            "mouth_shot_cooldown": (0.15, 3.0, float),
            "hand_gain": (0.5, 3.0, float),
            "bullet_speed": (4.0, 30.0, float),
            "round_time_cap": (20, 50, int),
            "smile_time_min": (1, 25, int),
            "smile_time_max": (1, 25, int),
            "smile_armor_seconds": (5, 60, int),
            "max_lives": (1, 9, int),
        }
        for key, (minimum, maximum, number_type) in bounds.items():
            if key not in data:
                continue
            try:
                value = data[key]
                converted = number_type(value)
                if isinstance(value, bool) or not math.isfinite(converted) or not minimum <= converted <= maximum:
                    raise ValueError
                if number_type is int and float(value) != converted:
                    raise ValueError
            except (TypeError, ValueError, OverflowError):
                kind = "целое число" if number_type is int else "число"
                return jsonify({"error": f"{key}: {kind} от {minimum} до {maximum}"}), 400
        with tracker.lock:
            # Integrate elapsed decay using the old rate before changing the setting.
            tracker._update_charge(time.monotonic())
            CFG.update(**data)
            CFG.save()
            return jsonify(CFG.as_dict())
    return jsonify(CFG.as_dict())

# Capture current gaze as the neutral calibration point.
@app.route('/calibrate', methods=['POST'])
def calibrate():
    tracker.start_calibration()
    return jsonify({"ok": True, "frames": tracker.CALIB_FRAMES})

# Reset gaze calibration to the default neutral point.
@app.route('/calibrate/reset', methods=['POST'])
def calibrate_reset():
    with tracker.lock:
        tracker._calib_samples = None
        tracker.state["calibrating"] = False
        CFG.gaze_neutral_x = 0.0
        CFG.gaze_neutral_y = 0.0
        tracker.state["neutral_x"] = 0.0
        tracker.state["neutral_y"] = 0.0
        CFG.save()
    return jsonify({"ok": True})

# Persist the best score to config.json.
@app.route('/score', methods=['POST'])
def score():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Ожидается JSON-объект со счётом"}), 400
    try:
        s = int(data.get("score", 0))
    except (TypeError, ValueError, OverflowError):
        s = 0
    with tracker.lock:
        if s > CFG.best_score:
            CFG.best_score = s
            CFG.save()
        return jsonify({"best_score": CFG.best_score})

# Browser camera mode: getUserMedia owns the device and sends JPEG frames to
# Python for MediaPipe. This is the preferred path when native OpenCV capture fails.
@app.route('/camera/browser/start', methods=['POST'])
def camera_browser_start():
    if app.config["SHUTDOWN_REQUESTED"] or tracker._shutdown_requested:
        return jsonify({"ok": False, "error": "Приложение завершает работу"}), 409
    try:
        tracker.start_browser()
        return jsonify({"ok": True, "mode": "browser"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route('/camera/browser/frame', methods=['POST'])
def camera_browser_frame():
    if app.config["SHUTDOWN_REQUESTED"] or tracker._shutdown_requested:
        return jsonify({"ok": False, "error": "Приложение завершает работу"}), 409
    payload = request.get_data(cache=False)
    if not payload:
        return jsonify({"ok": False, "error": "Пустой кадр"}), 400
    if len(payload) > 2_500_000:
        return jsonify({"ok": False, "error": "Кадр слишком большой"}), 413
    try:
        import numpy as np
        encoded = np.frombuffer(payload, dtype=np.uint8)
        frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if frame is None:
            return jsonify({"ok": False, "error": "Не удалось декодировать JPEG"}), 400
        tracker.submit_browser_frame(frame)
        return jsonify({"ok": True})
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


# Start native camera fallback.
@app.route('/camera/start', methods=['POST'])
def camera_start():
    if app.config["SHUTDOWN_REQUESTED"] or tracker._shutdown_requested:
        return jsonify({"ok": False, "error": "Приложение завершает работу"}), 409
    try:
        tracker.start()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# Stop camera capture.
@app.route('/camera/stop', methods=['POST'])
def camera_stop():
    try:
        tracker.stop()
        return jsonify({"ok": True})
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503


@app.route('/shutdown', methods=['POST'])
def shutdown():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get("confirm") is not True:
        return jsonify({"ok": False, "error": "Для завершения требуется JSON {confirm: true}"}), 400
    with _shutdown_request_lock:
        callback = app.config.get("SHUTDOWN_CALLBACK")
        if not callable(callback):
            return jsonify({"ok": False, "error": "Полное завершение доступно при запуске через run.ps1 или python app.py"}), 503
        if not app.config["SHUTDOWN_REQUESTED"]:
            try:
                callback()
            except Exception as exc:
                return jsonify({"ok": False, "error": str(exc)}), 503
            app.config["SHUTDOWN_REQUESTED"] = True
    return jsonify({"ok": True}), 202

# Application entry point.
if __name__ == "__main__":
    suppress_noise()
    # Camera capture starts only after an explicit endpoint call. A single process owns tracker state.
    watchdog_lock = threading.Lock()
    watchdog_started = False
    server = None

    def start_shutdown_watchdog():
        """Guarantee process exit even if OpenCV/MediaPipe stalls in native code."""
        global watchdog_started
        with watchdog_lock:
            if watchdog_started:
                return

            def force_exit_after_grace():
                # A daemon watchdog does not block clean shutdown. If native/non-daemon code stalls,
                # force exit after 4.5 seconds so the port and camera are actually released.
                time.sleep(4.5)
                print("[!] SmileGun: принудительное завершение зависшего процесса.", flush=True)
                os._exit(0)

            threading.Thread(target=force_exit_after_grace,
                             name="SmileGunShutdownWatchdog", daemon=True).start()
            watchdog_started = True

    def schedule_shutdown():
        start_shutdown_watchdog()
        tracker.request_shutdown()

        def stop_http_after_response():
            # Let the HTTP handler send its acknowledgement before closing the socket.
            time.sleep(0.15)
            server.shutdown()

        threading.Thread(target=stop_http_after_response,
                         name="SmileGunHttpShutdown", daemon=True).start()

    try:
        host = os.environ.get("SMILEGUN_HOST", "0.0.0.0")
        port = int(os.environ.get("SMILEGUN_PORT", "5000"))
        ssl_context = None
        if os.environ.get("SMILEGUN_HTTPS", "").strip().lower() in {"1", "true", "yes", "on"}:
            from mobile_tls import ensure_mobile_cert
            cert_path, key_path, ca_path, ips = ensure_mobile_cert()
            ssl_context = (str(cert_path), str(key_path))
            print(f"SmileGun HTTPS: https://127.0.0.1:{port}", flush=True)
            for ip in ips:
                if not ip.startswith("127."):
                    print(f"SmileGun PHONE: https://{ip}:{port}", flush=True)
            print(f"Mobile CA: {ca_path}", flush=True)
        server = make_server(host, port, app, threaded=True, ssl_context=ssl_context)
        app.config["SHUTDOWN_CALLBACK"] = schedule_shutdown
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        # Ctrl+C receives the same guaranteed shutdown path as the UI button.
        start_shutdown_watchdog()
        if server is not None:
            server.server_close()
        tracker.close()
