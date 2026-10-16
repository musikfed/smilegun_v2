from __future__ import annotations

import math
import unittest
from types import SimpleNamespace

from config import Config, CFG

try:
    import app as game_app
except ModuleNotFoundError as exc:  # Allows static environments without runtime deps.
    game_app = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


class ConfigTests(unittest.TestCase):
    def test_new_round_defaults_and_caps(self):
        cfg = Config()
        self.assertLessEqual(cfg.level_duration, 50)
        self.assertEqual(cfg.round_time_cap, 50)
        self.assertEqual((cfg.smile_time_min, cfg.smile_time_max), (10, 25))
        self.assertEqual(cfg.smile_armor_seconds, 45)

    def test_config_rejects_round_over_50(self):
        cfg = Config()
        before = cfg.level_duration
        cfg.update(level_duration=75)
        self.assertEqual(cfg.level_duration, before)
        cfg.update(level_duration=50)
        self.assertEqual(cfg.level_duration, 50)

    def test_smile_range_is_normalized(self):
        cfg = Config()
        cfg.update(smile_time_min=25, smile_time_max=10)
        self.assertEqual((cfg.smile_time_min, cfg.smile_time_max), (10, 25))


@unittest.skipIf(game_app is None, f"runtime deps unavailable: {IMPORT_ERROR}")
class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = game_app.tracker
        with self.tracker.lock:
            self.tracker._reset_tracking()
            for key in ("shot_id", "fist_shot_id", "mouth_shot_id", "smile_id", "smile_shot_id", "super_id", "blink_id", "two_palms_id"):
                self.tracker.state[key] = 0
        self.old_cfg = CFG.as_dict()
        CFG.smile_threshold = 1.55
        CFG.mouth_open_threshold = 0.30
        CFG.smile_shot_cooldown = 0.0
        CFG.mouth_shot_cooldown = 0.0
        CFG.super_blink_gain = 0.25

    def tearDown(self):
        CFG.update(**self.old_cfg)
        with self.tracker.lock:
            self.tracker._reset_tracking()

    @staticmethod
    def fake_hand(v=False, open_palm=False, fist=False, x=0.5):
        pts = [SimpleNamespace(x=x, y=0.60, z=0.0) for _ in range(21)]
        pts[0] = SimpleNamespace(x=x, y=0.82, z=0.0)
        pts[9] = SimpleNamespace(x=x, y=0.52, z=0.0)
        # palm bases
        for i, dx in zip((5, 9, 13, 17), (-.08, 0, .08, .15)):
            pts[i] = SimpleNamespace(x=x + dx, y=.55, z=0.0)
        pairs = ((8, 6), (12, 10), (16, 14), (20, 18))
        for tip, pip in pairs:
            pts[pip] = SimpleNamespace(x=x, y=.43, z=0.0)
            pts[tip] = SimpleNamespace(x=x, y=.48 if fist else .18, z=0.0)
        if v:
            for tip, _ in pairs[2:]:
                pts[tip] = SimpleNamespace(x=x, y=.49, z=0.0)
        if open_palm:
            for tip, _ in pairs:
                pts[tip] = SimpleNamespace(x=x, y=.12, z=0.0)
        return SimpleNamespace(landmark=pts)

    @staticmethod
    def fake_face(*, blink=False, smile=False, mouth=False):
        pts = [SimpleNamespace(x=.5, y=.5, z=0.0) for _ in range(478)]
        # Eyes: width 0.10 each.
        pts[game_app.LEFT_EYE_OUTER] = SimpleNamespace(x=.60, y=.50, z=0.0)
        pts[game_app.LEFT_EYE_INNER] = SimpleNamespace(x=.50, y=.50, z=0.0)
        pts[game_app.RIGHT_EYE_OUTER] = SimpleNamespace(x=.50, y=.50, z=0.0)
        pts[game_app.RIGHT_EYE_INNER] = SimpleNamespace(x=.40, y=.50, z=0.0)
        gap = .006 if blink else .022
        pts[game_app.LEFT_EYE_TOP] = SimpleNamespace(x=.55, y=.50-gap/2, z=0.0)
        pts[game_app.LEFT_EYE_BOTTOM] = SimpleNamespace(x=.55, y=.50+gap/2, z=0.0)
        pts[game_app.RIGHT_EYE_TOP] = SimpleNamespace(x=.45, y=.50-gap/2, z=0.0)
        pts[game_app.RIGHT_EYE_BOTTOM] = SimpleNamespace(x=.45, y=.50+gap/2, z=0.0)
        pts[game_app.LEFT_IRIS_CENTER] = SimpleNamespace(x=.55, y=.50, z=0.0)
        pts[game_app.RIGHT_IRIS_CENTER] = SimpleNamespace(x=.45, y=.50, z=0.0)
        mw = .19 if smile else .12
        pts[game_app.MOUTH_LEFT] = SimpleNamespace(x=.5-mw/2, y=.64, z=0.0)
        pts[game_app.MOUTH_RIGHT] = SimpleNamespace(x=.5+mw/2, y=.64, z=0.0)
        mh = .05 if mouth else .01
        pts[game_app.LIP_TOP_INNER] = SimpleNamespace(x=.5, y=.64-mh/2, z=0.0)
        pts[game_app.LIP_BOTTOM_INNER] = SimpleNamespace(x=.5, y=.64+mh/2, z=0.0)
        return SimpleNamespace(landmark=pts)

    def process(self, face=None, hands=None):
        self.tracker._detect_hands = lambda frame: list(hands or [])
        self.tracker._make_mesh = lambda frame: face
        self.tracker._process_frame(SimpleNamespace())
        return dict(self.tracker.state)

    def test_v_sign_sets_ghost_state(self):
        state = self.process(face=None, hands=[self.fake_hand(v=True)])
        self.assertTrue(state["peace"])
        self.assertEqual(state["hand_count"], 1)

    def test_two_open_palms_emit_one_edge(self):
        h1, h2 = self.fake_hand(open_palm=True, x=.35), self.fake_hand(open_palm=True, x=.65)
        state = self.process(face=None, hands=[h1, h2])
        self.assertTrue(state["two_palms"])
        self.assertEqual(state["two_palms_id"], 1)
        state = self.process(face=None, hands=[h1, h2])
        self.assertEqual(state["two_palms_id"], 1)
        self.process(face=None, hands=[])
        state = self.process(face=None, hands=[h1, h2])
        self.assertEqual(state["two_palms_id"], 2)

    def test_open_mouth_is_spread_trigger_not_smile(self):
        self.process(face=self.fake_face(), hands=[])
        self.process(face=self.fake_face(mouth=True), hands=[])
        state = self.process(face=self.fake_face(mouth=True), hands=[])
        self.assertTrue(state["mouth_open"])
        self.assertEqual(state["mouth_shot_id"], 1)
        self.assertEqual(state["shot_id"], 1)
        self.assertEqual(state["smile_id"], 0)
        state = self.process(face=self.fake_face(mouth=True), hands=[])
        self.assertEqual(state["mouth_shot_id"], 1, "holding mouth open must not spam")

    def test_smile_is_bonus_event_and_only_super_when_ready(self):
        self.process(face=self.fake_face(), hands=[])
        self.process(face=self.fake_face(smile=True), hands=[])
        state = self.process(face=self.fake_face(smile=True), hands=[])
        self.assertEqual(state["smile_id"], 1)
        self.assertEqual(state["shot_id"], 0)
        self.assertEqual(state["super_id"], 0)
        # release below hysteresis, set full charge, smile again
        for _ in range(4):
            self.process(face=self.fake_face(), hands=[])
        self.tracker._charge = 1.0
        self.process(face=self.fake_face(smile=True), hands=[])
        state = self.process(face=self.fake_face(smile=True), hands=[])
        self.assertEqual(state["smile_id"], 2)
        self.assertEqual(state["super_id"], 1)
        self.assertAlmostEqual(state["super_charge"], 0.0)

    def test_blink_counts_on_closed_to_open_cycle_once(self):
        self.process(face=self.fake_face(), hands=[])
        state = self.process(face=self.fake_face(blink=True), hands=[])
        self.assertEqual(state["blink_id"], 0)
        state = self.process(face=self.fake_face(blink=False), hands=[])
        self.assertEqual(state["blink_id"], 1)
        self.assertTrue(math.isclose(state["super_charge"], .25, abs_tol=.02))
        state = self.process(face=self.fake_face(blink=False), hands=[])
        self.assertEqual(state["blink_id"], 1)

    def test_state_exposes_new_gestures(self):
        state = self.tracker.get_state()
        for key in ("mouth_shot_id", "smile_id", "peace", "two_palms", "two_palms_id", "hand_count"):
            self.assertIn(key, state)


if __name__ == "__main__":
    unittest.main(verbosity=2)
