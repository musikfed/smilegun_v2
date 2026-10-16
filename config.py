from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, fields
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parent / "config.json"


@dataclass
class Config:
    # Logical playfield. CSS scales it to every screen size.
    field_width: int = 960
    field_height: int = 720

    # Legacy gaze fields are kept so old config.json files migrate safely.
    iris_gain: float = 2.6
    iris_gain_y: float = 1.6
    smoothing_alpha: float = 0.30
    gaze_neutral_x: float = 0.0
    gaze_neutral_y: float = 0.0
    gaze_deadzone: float = 0.02

    # Face gestures.
    smile_threshold: float = 1.55
    smile_release_ratio: float = 0.85
    smile_shot_cooldown: float = 0.55  # compatibility name: now smile-event debounce
    smile_hysteresis: float = 0.65
    mouth_open_threshold: float = 0.30
    mouth_open_release_ratio: float = 0.68
    mouth_shot_cooldown: float = 0.55

    # Game balance.
    bullet_speed: float = 11.0
    level_duration: int = 30              # base round time; gameplay caps remaining time at 50s
    level_difficulty: int = 1
    smile_time_min: int = 10
    smile_time_max: int = 25
    round_time_cap: int = 50
    smile_armor_seconds: int = 45
    max_lives: int = 5

    # Camera.
    camera_index: int = 0

    # Hand tracking.
    hand_gain: float = 1.15
    fist_threshold: float = 1.0
    fist_release: float = 1.25
    two_palms_threshold: float = 1.42

    # Blink -> super charge.
    super_blink_gain: float = 0.20
    super_decay_delay: float = 3.0
    super_decay_rate: float = 0.06
    super_blink_count: int = 5
    super_charge_rate: float = 0.6
    super_charge_move_min: float = 0.03
    super_blink_window: float = 2.0

    best_score: int = 0

    def update(self, **kwargs):
        types = {item.name: type(item.default) for item in fields(self)}
        bounds = {
            "field_width": (480, 1600), "field_height": (360, 1200),
            "smile_threshold": (1.0, 3.0), "smile_release_ratio": (0.5, 0.95),
            "smile_shot_cooldown": (0.1, 3.0),
            "mouth_open_threshold": (0.12, 0.8), "mouth_open_release_ratio": (0.4, 0.95),
            "mouth_shot_cooldown": (0.15, 3.0),
            "bullet_speed": (4.0, 30.0), "level_duration": (10, 50),
            "level_difficulty": (1, 5), "smile_time_min": (1, 25),
            "smile_time_max": (1, 25), "round_time_cap": (20, 50),
            "smile_armor_seconds": (5, 60), "max_lives": (1, 9),
            "camera_index": (0, 20), "hand_gain": (0.5, 3.0),
            "fist_threshold": (0.5, 1.5), "fist_release": (0.8, 2.0),
            "two_palms_threshold": (1.1, 2.0),
            "super_blink_count": (2, 10), "super_blink_gain": (0.05, 1.0),
            "super_decay_delay": (0.5, 15.0), "super_decay_rate": (0.0, 0.5),
            "best_score": (0, 2_000_000_000),
        }
        for key, value in kwargs.items():
            if key not in types or isinstance(value, bool):
                continue
            try:
                converted = types[key](value)
                if isinstance(converted, (int, float)) and not math.isfinite(converted):
                    continue
                if types[key] is int and float(value) != converted:
                    continue
                if key in bounds and not (bounds[key][0] <= converted <= bounds[key][1]):
                    continue
                setattr(self, key, converted)
            except (TypeError, ValueError, OverflowError):
                continue
        if self.smile_time_min > self.smile_time_max:
            self.smile_time_min, self.smile_time_max = self.smile_time_max, self.smile_time_min
        self.level_duration = min(self.level_duration, self.round_time_cap)
        return asdict(self)

    def as_dict(self):
        return asdict(self)

    def save(self) -> None:
        try:
            CONFIG_FILE.write_text(json.dumps(self.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def load(self) -> None:
        if not CONFIG_FILE.exists():
            return
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            if "super_blink_gain" not in data and "super_blink_count" in data:
                try:
                    count = int(data["super_blink_count"])
                    if 2 <= count <= 10:
                        data["super_blink_gain"] = 1.0 / count
                except (TypeError, ValueError, OverflowError):
                    pass
            self.update(**data)


CFG = Config()
CFG.load()
