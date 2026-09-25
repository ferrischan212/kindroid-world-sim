"""Checks for the persistent simulation. Nothing here reaches DeepSeek or Kindroid.

Run with: python -m unittest test_simulation -v
"""

from __future__ import annotations

import copy
import ctypes
import hashlib
import json
import logging
import re
import shutil
import tempfile
import time
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import simulation
import update_scene as us
import wand

ROOT = Path(__file__).resolve().parent
LA = us.LA
REAL_SWITCH = us.switch_profile
START = "lora lies on the rug by the fireplace, Mochi curled at her side, moonlight through the window and one last log glowing low."


def at(hour: int, minute: int = 0, day: int = 24) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=LA)


def plan(location: str, setting: str, **extra) -> dict:
    data = {
        "location": location,
        "activity": setting.split(",")[0],
        "mochi_activity": "close by",
        "objects": [],
        "weather": "clear",
        "lights": "",
        "goal_step_done": False,
        "new_goal": None,
        "event": setting,
        "memory": None,
        "bridge": "lora finished what she was doing and walked over with Mochi.",
        "setting": setting,
    }
    data.update(extra)
    return data


class Lines(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record) -> None:
        self.lines.append(record.getMessage())


class Fakes:
    """Stand-ins for DeepSeek and Kindroid."""

    def __init__(self) -> None:
        self.plans: list = []
        self.prompts: list[str] = []
        self.fits = True
        self.legacy = RuntimeError("DeepSeek is down")
        self.sent: list[tuple[str, str]] = []
        self.profiles: list[str] = []
        self.fail_tell = False
        self.chat: list[dict] = []

    def ask_json(self, key, prompt, system, **kwargs):
        self.prompts.append(prompt)
        item = self.plans.pop(0) if self.plans else ValueError("no plan queued")
        if isinstance(item, Exception):
            raise item
        return copy.deepcopy(item)

    def ask(self, key, prompt, system=None, max_tokens=120, temperature=1.1, paragraph=False):
        if paragraph:
            return "yes" if self.fits else "no"
        if isinstance(self.legacy, Exception):
            raise self.legacy
        return self.legacy

    def switch(self, key, profile):
        self.profiles.append(str(profile.get("user_name")))

    def save(self, key, ai_id, scene):
        self.sent.append(("setting", scene))

    def tell(self, key, ai_id, message):
        if self.fail_tell:
            raise RuntimeError("https://api.kindroid.ai/v1/send-message returned 500: busy")
        self.sent.append(("message", message))
        return "ok"

    def fetch(self, after, group_id=None):
        return self.chat, 0


class Base(unittest.TestCase):
    """A temp copy of the state, with DeepSeek and Kindroid replaced."""

    @classmethod
    def setUpClass(cls) -> None:
        source = ROOT / "state.json"
        real = json.loads(source.read_text(encoding="utf-8")) if source.exists() else {}
        prefs = real.get("prefs") or {}
        if len(str(prefs.get("environment") or "")) < 1000:
            raise unittest.SkipTest("The full Environment list is not saved in state.json yet.")
        cls.prefs = dict(prefs)
        cls.lines = Lines()
        simulation.log.handlers[:] = [cls.lines]
        simulation.log.setLevel(logging.INFO)
        simulation.log.propagate = False

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.now = at(14)
        self.fake = Fakes()
        prefs = dict(self.prefs, last_greet_date=self.now.date().isoformat(), heads_up="0")
        state = {"history": [START], "prefs": prefs, "updated_at": at(13, 50).replace(tzinfo=None).isoformat()}
        (self.tmp / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        shutil.copyfile(ROOT / "world_map.json", self.tmp / "world_map.json")
        self.patches = [
            mock.patch.object(us, "STATE_PATH", self.tmp / "state.json"),
            mock.patch.object(us, "BACKUP_PATH", self.tmp / "state.backup.json"),
            mock.patch.object(us, "load_env", lambda path: None),
            mock.patch.object(us, "require_keys", lambda: ("deepseek", "kindroid", "ai")),
            mock.patch.object(us, "ask_deepseek_json", self.fake.ask_json),
            mock.patch.object(us, "ask_deepseek", self.fake.ask),
            mock.patch.object(us, "switch_profile", self.fake.switch),
            mock.patch.object(us, "save_kindroid", self.fake.save),
            mock.patch.object(us, "tell_lora", self.fake.tell),
            mock.patch.object(us, "fetch_messages", self.fake.fetch),
            mock.patch.object(us, "la_now", lambda: self.now),
            mock.patch.object(simulation, "WORLD_MAP_PATH", self.tmp / "world_map.json"),
        ]
        for patch in self.patches:
            patch.start()
        self.lines.lines.clear()

    def tearDown(self) -> None:
        for patch in reversed(self.patches):
            patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def state(self) -> dict:
        return json.loads((self.tmp / "state.json").read_text(encoding="utf-8"))

    def digest(self) -> str:
        return hashlib.sha256((self.tmp / "state.json").read_bytes()).hexdigest()

    def kitchen_step(self) -> dict:
        self.fake.plans = [
            plan(
                "kitchen-area",
                "lora slices fruit at the kitchen table, Mochi begging by the toaster, afternoon sun on the plates",
                objects=[{"id": "fruit bowl", "state": "half empty"}, {"id": "spaceship engine", "state": "on"}],
                memory={"text": "Lora shared apple slices with Mochi.", "importance": 2},
                new_goal={"title": "Bake cookies", "steps": ["gather ingredients", "mix dough", "bake", "share with Mochi"]},
                lights="ceiling lights",
            )
        ]
        return simulation.advance_simulation()



class SimulationTest(Base):
    def test_step_from_existing_setting(self) -> None:
        result = self.kitchen_step()
        world = self.state()["world"]
        self.assertEqual(world["journal"][-1]["from"], "fireplace")
        self.assertEqual(world["lora"]["location"], "kitchen-area")
        self.assertEqual(world["mochi"]["location"], "kitchen-area")
        self.assertIn("kitchen-area", simulation.catalog_for(self.prefs["environment"])["places"])
        self.assertLessEqual(len(result["setting"]), us.SCENE_LIMIT)
        self.assertIn("Mochi", result["setting"])
        self.assertEqual(result["message"], us.narrator_message(START, result["setting"], bridge=result["bridge"]))
        self.assertEqual(self.fake.sent, [("setting", result["setting"]), ("message", result["message"])])
        self.assertEqual(self.fake.profiles[0], "Narrator")
        self.assertIn(self.fake.profiles[-1], {"Master", "Mochi"})
        self.assertEqual(self.state()["history"][-1], result["setting"])
        self.assertIn("kitchen-area:fruit-bowl", world["objects"])
        self.assertFalse(any("spaceship" in key for key in world["objects"]))
        self.assertTrue(any(item["text"] == "Lora shared apple slices with Mochi." for item in world["memories"]))
        self.assertEqual(world["goal"]["title"], "Bake cookies")
        self.assertFalse(result["pending"])

    def test_invented_place_and_teleport_are_rejected(self) -> None:
        self.kitchen_step()
        self.now = at(14, 10)
        self.fake.prompts.clear()
        self.fake.plans = [
            plan("spaceship", "lora floats in the spaceship with Mochi"),
            plan("greenhouse", "lora waters seed trays in the greenhouse, Mochi sniffing the pots"),
            plan("living-room", "lora folds blankets on the living room sofa, Mochi chewing a cushion tassel"),
        ]
        result = simulation.advance_simulation()
        self.assertEqual(self.state()["world"]["lora"]["location"], "living-room")
        self.assertIn("is not a place in her world", self.fake.prompts[1])
        self.assertIn("no known way", self.fake.prompts[2])
        self.assertIn("living room", result["setting"])

    def test_move_check_saying_no_every_time_does_not_get_stuck(self) -> None:
        """At 6 PM the move check said no to every try, and nothing changed for 17 minutes."""
        self.now = at(17, 51)
        self.kitchen_step()
        self.now = at(18, 16)
        self.fake.fits = False
        self.fake.plans = [
            plan("living-room", "lora folds blankets on the living room sofa, Mochi chewing a cushion tassel"),
            plan("living-room", "lora stacks cushions on the living room sofa, Mochi chewing a cushion tassel"),
            plan("living-room", "lora dims the living room lamp by the sofa, Mochi chewing a cushion tassel"),
            plan("living-room", "lora sorts a basket on the living room sofa, Mochi chewing a cushion tassel"),
        ]
        result = simulation.advance_simulation()
        self.assertEqual(self.state()["world"]["lora"]["location"], "living-room")
        self.assertIn("folds blankets", result["setting"])
        self.assertTrue(any("move check said no on every try" in line for line in self.lines.lines))

    def test_fallback_writer_keeps_a_line_the_move_check_refused(self) -> None:
        self.now = at(17, 51)
        self.kitchen_step()
        self.now = at(18, 16)
        self.fake.fits = False
        self.fake.plans = []
        self.fake.legacy = "lora carries the plates to the sink in the kitchen area, Mochi trotting behind, evening light"
        result = simulation.advance_simulation()
        self.assertEqual(result["setting"], self.fake.legacy)
        self.assertEqual(result["bridge"], "")

    def test_fallback_line_gets_a_bridge_and_real_length(self) -> None:
        self.kitchen_step()
        self.now = at(14, 20)
        self.fake.plans = [ValueError("bad json")] * simulation.PLAN_TRIES + [
            {"bridge": "The blankets were still in a heap, so lora carried them over.", "minutes_reason": "folding a few blankets", "minutes": 8}
        ]
        self.fake.legacy = "lora folds blankets on the living room sofa, Mochi chewing a cushion tassel, afternoon sun"
        result = simulation.advance_simulation(schedule={"every": 30, "times": [], "ai": True})
        self.assertEqual(result["bridge"], "the blankets were still in a heap, so lora carried them over.")
        self.assertEqual(result["minutes"], 8)
        self.assertEqual(result["delay"], 8 * 60)
        self.assertIn("then, the blankets were still in a heap", self.fake.sent[-1][1])

    def test_late_night_stays_in_bedroom_and_winds_down(self) -> None:
        self.kitchen_step()
        self.now = at(23, 30)
        self.fake.prompts.clear()
        self.fake.plans = [
            plan("kitchen-area", "lora rinses cups at the kitchen sink, Mochi yawning by the fridge"),
            plan("floor-rug", "lora curls up on the floor rug with Mochi, curtains drawn, the room quiet and dark"),
        ]
        simulation.advance_simulation()
        world = self.state()["world"]
        self.assertEqual(world["lora"]["location"], "floor-rug")
        self.assertIn("late at night", self.fake.prompts[1])
        self.assertEqual(world["goal"]["title"], "Wind down for the night")
        self.assertEqual(world["goals_paused"][-1]["title"], "Bake cookies")

    def test_hours_later_she_wakes_up(self) -> None:
        self.kitchen_step()
        self.now = at(23, 59)
        self.fake.plans = [plan("floor-rug", "lora curls up on the floor rug with Mochi, curtains drawn, the room quiet and dark")]
        simulation.advance_simulation()
        self.now = at(8, 0, day=25)
        self.fake.plans = [plan("window", "lora opens the window curtains for Mochi, morning sun across the room")]
        simulation.advance_simulation()
        world = self.state()["world"]
        self.assertIn("About 8 hours passed", self.fake.prompts[-1])
        self.assertIn("Morning", self.fake.prompts[-1])
        self.assertEqual(world["goals_done"][-1]["title"], "Wind down for the night")
        self.assertEqual(world["goal"]["title"], "Start the day")
        self.assertTrue(any("slept through the night" in item["text"] for item in world["memories"]))

    def test_failed_deepseek_keeps_state(self) -> None:
        self.kitchen_step()
        before = self.digest()
        self.now = at(14, 20)
        self.fake.plans = [RuntimeError("DeepSeek returned 503")] * simulation.PLAN_TRIES
        with self.assertRaises(RuntimeError):
            simulation.advance_simulation()
        self.assertEqual(self.digest(), before)
        self.assertEqual(len(self.fake.sent), 2)

    def test_broken_json_is_rejected_then_fallback_writer_used(self) -> None:
        self.kitchen_step()
        self.now = at(14, 20)
        self.fake.prompts.clear()
        self.fake.plans = [ValueError("DeepSeek sent broken JSON.")] * simulation.PLAN_TRIES
        self.fake.legacy = "lora reads on the living room sofa, Mochi chewing a cushion, afternoon light"
        result = simulation.advance_simulation()
        self.assertEqual(result["source"], "fallback")
        self.assertEqual(self.state()["world"]["lora"]["location"], "living-room")
        self.assertIn("not a JSON object", self.fake.prompts[1])

    def test_failed_kindroid_keeps_local_state_and_retries(self) -> None:
        self.fake.fail_tell = True
        result = self.kitchen_step()
        world = self.state()["world"]
        self.assertTrue(result["pending"])
        self.assertEqual(world["setting"], result["setting"])
        self.assertEqual(world["pending"]["parts"], {"setting": True, "message": False})
        self.assertEqual(self.state()["history"][-1], result["setting"])
        self.fake.fail_tell = False
        self.fake.sent.clear()
        retry = simulation.advance_simulation()
        self.assertTrue(retry["delivered"])
        self.assertEqual(self.fake.sent, [("message", result["message"])])
        self.assertIsNone(self.state()["world"]["pending"])

    def test_schedule_and_restart(self) -> None:
        self.kitchen_step()
        next_at = self.now + timedelta(minutes=10)
        simulation.set_schedule(True, next_at)
        info = simulation.summary()
        self.assertTrue(info["running"])
        self.assertEqual(info["next_at"], next_at)
        self.assertEqual(info["place"], "Kitchen area (indoor)")
        self.assertEqual(info["goal"], "Bake cookies")
        simulation.set_schedule(False)
        self.assertFalse(simulation.schedule()["running"])
        self.assertEqual(simulation.parse_every("2h"), 120)
        self.assertEqual(simulation.parse_every("1h30m"), 90)
        self.assertEqual(simulation.parse_every("off"), 0)
        self.assertEqual(simulation.parse_times("8:00 AM, noon, night"), [480, 720, 1320])
        self.assertEqual(simulation.seconds_until_next(at(11, 59), [720]), 60)

    def test_backup_and_recovery(self) -> None:
        self.kitchen_step()
        before = (self.tmp / "state.json").read_text(encoding="utf-8")
        us.remember("lora waves at Mochi from the porch")
        self.assertEqual((self.tmp / "state.backup.json").read_text(encoding="utf-8"), before)
        (self.tmp / "state.json").write_text("{ broken", encoding="utf-8")
        self.assertEqual(us.read_state()["history"], json.loads(before)["history"])
        good = (self.tmp / "state.backup.json").read_bytes()

        def reject(state: dict) -> None:
            raise RuntimeError("invalid")

        with self.assertRaises(RuntimeError):
            us.update_state(lambda state: state.update(x=1), validate=reject)
        self.assertEqual((self.tmp / "state.backup.json").read_bytes(), good)

    def test_cli_dry_run_and_prior(self) -> None:
        self.fake.plans = [plan("kitchen-area", "lora rinses berries at the kitchen sink, Mochi watching the oven light")]
        before = self.digest()
        lines = us.run_update(dry_run=True)
        self.assertEqual(self.digest(), before)
        self.assertEqual(self.fake.sent, [])
        self.assertTrue(any(line.startswith("Preview only") for line in lines))
        prior = "lora reads in the reading nook armchair with Mochi on the blanket"
        self.fake.plans = [plan("library", "lora climbs the rolling ladder in the library, Mochi waiting at the reading table")]
        lines = us.run_update(prior_override=prior)
        self.assertEqual(lines[0], f"Prior: {prior}")
        self.assertEqual(self.state()["world"]["journal"][-1]["from"], "reading-nook")
        self.assertEqual(self.state()["world"]["lora"]["location"], "library")

    def test_possessive_mochi_counts(self) -> None:
        self.assertTrue(us.has_mochi("lora ties Mochi's bow by the shoerack"))
        self.fake.plans = [plan("kitchen-area", "lora fills Mochi's water bowl at the kitchen sink, afternoon sun on the plates")]
        result = simulation.advance_simulation()
        self.assertEqual(result["source"], "planner")
        self.assertEqual(len(self.fake.prompts), 1)

    def test_listed_time_close_by_is_not_skipped(self) -> None:
        self.assertEqual(simulation.seconds_until_next(at(11, 59).replace(second=45), [720]), 15)
        just_fired = at(12, 0).replace(microsecond=500_000)
        self.assertGreater(simulation.seconds_until_next(just_fired, [720]), 23 * 3600)

    def test_partial_profile_switch_is_switched_back(self) -> None:
        personas: list[str] = []

        def post(url, payload, headers, timeout, required=False):
            if "active_persona_id" in payload:
                personas.append(payload["active_persona_id"])
            elif payload.get("user_name") == "Narrator":
                raise RuntimeError("update-info returned 429: slow down")
            return {}

        config = us.load_json(us.CONFIG_PATH, {})
        with mock.patch.object(us, "switch_profile", REAL_SWITCH), mock.patch.object(us, "post_json", post):
            with self.assertRaises(us.PublishError):
                us.publish_change("before line", "after line")
        self.assertEqual(personas[0], config["narrator_profile"]["id"])
        self.assertEqual(personas[-1], config["master_profile"]["id"])

    def test_newest_master_message_beats_an_older_mochi_page(self) -> None:
        calls = {"n": 0}

        def fetch(after, group_id=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return (
                    [
                        {"timestamp": after + 1 + i, "sender": "user", "display_name": "Mochi", "message": "woof"}
                        for i in range(20)
                    ],
                    0,
                )
            return ([{"timestamp": after + 5, "sender": "user", "display_name": "Master", "message": "hi"}], 0)

        with mock.patch.object(us, "fetch_messages", fetch):
            profile = us.chatter_profile(us.load_json(us.CONFIG_PATH, {}))
        self.assertEqual(profile["user_name"], "Master")
        self.assertEqual(calls["n"], 2)

    def test_request_goes_through_and_is_remembered(self) -> None:
        self.now = at(23, 0)
        self.fake.plans = [plan("kitchen-area", "lora warms milk at the kitchen stove for Master, Mochi at her heels")]
        simulation.advance_simulation(request="take her to the kitchen for warm milk", requested_by="Master")
        world = self.state()["world"]
        self.assertEqual(world["lora"]["location"], "kitchen-area")
        self.assertTrue(any(item["kind"] == "request" and "Master" in item["who"] for item in world["memories"]))

    def test_lora_narrator_request_picks_that_place(self) -> None:
        self.fake.plans = [
            plan(
                "garden-vegetable-patch",
                "lora waters the garden vegetable patch, Mochi sniffing tomatoes, morning sun on the carrots",
            )
        ]
        simulation.advance_simulation()
        self.now = at(14, 10)
        stamp = int(self.now.timestamp() * 1000)
        old = [
            {"timestamp": stamp - 30_000 - i, "sender": "ai", "display_name": "Lora", "message": f"chat {i}"}
            for i in range(20)
        ]
        ask = {
            "timestamp": stamp,
            "sender": "ai",
            "display_name": "Lora",
            "message": "Hey @narrator, I want to go to the treehouse with the telescope.",
        }
        pages = iter([(old, 0), ([ask], 0)])
        self.fake.fetch = lambda after, group_id=None: next(pages, ([ask], 0))
        us.fetch_messages = self.fake.fetch
        self.fake.plans = [
            plan(
                "walking-path",
                "lora stretches her arms on the walking path, Mochi sniffing around, morning sun on the flower beds",
            ),
            plan(
                "treehouse",
                "lora climbs into the treehouse with Mochi, telescope by the tiny window, morning sun on the cushions",
            ),
        ]
        result = simulation.advance_simulation()
        self.assertEqual(self.state()["world"]["lora"]["location"], "treehouse")
        self.assertIn("treehouse", result["setting"])

    def test_deepseek_picks_how_long_the_action_lasts(self) -> None:
        self.assertEqual(simulation.activity_minutes(300), 120)
        self.assertEqual(simulation.activity_minutes(2), 5)
        self.assertEqual(simulation.activity_minutes("25"), 25)
        self.assertIsNone(simulation.activity_minutes("soon"))
        self.assertIsNone(simulation.activity_minutes(True))
        self.fake.plans = [
            plan("kitchen-area", "lora slices fruit at the kitchen table, Mochi begging by the toaster", minutes=25)
        ]
        result = simulation.advance_simulation()
        self.assertEqual(result["minutes"], 25)
        self.assertEqual(self.state()["world"]["activity_minutes"], 25)
        self.assertIn('"minutes"', self.fake.prompts[0])

    def test_narrator_announces_time_until_next_change(self) -> None:
        self.assertEqual(us.duration_text(60), "1 minute")
        self.assertEqual(us.duration_text(1200), "20 minutes")
        self.assertEqual(us.duration_text(5400), "1 hour 30 minutes")
        self.assertEqual(us.duration_text(7200), "2 hours")
        self.fake.plans = [
            plan(
                "kitchen-area",
                "lora slices fruit at the kitchen table, Mochi begging by the toaster",
                minutes=12,
                minutes_reason="slicing one bowl of fruit, about 12 minutes",
                bridge="Her stomach growled as the log burned down, so lora got up from the rug and padded to the kitchen, Mochi right behind her.",
            )
        ]
        result = simulation.advance_simulation(schedule={"every": 10, "times": [], "ai": True})
        message = self.fake.sent[-1][1]
        lines = message.splitlines()
        self.assertEqual(lines[0], f"*before, {START}")
        self.assertEqual(
            lines[1],
            "then, her stomach growled as the log burned down, so lora got up from the rug and padded to the kitchen, Mochi right behind her.",
        )
        self.assertEqual(lines[2], f"now, {result['setting']}*")
        self.assertEqual(lines[3], "time until next change: 12 minutes")
        self.assertEqual(len(lines), 4)
        self.assertNotIn("conversate", message)
        self.assertEqual(result["delay"], 12 * 60)
        self.assertTrue(any(line == "length: 12 minutes, slicing one bowl of fruit, about 12 minutes" for line in self.lines.lines))
        world = self.state()["world"]
        self.assertEqual(world["journal"][-1]["bridge"], result["bridge"])

    def test_bridge_is_required_and_carried_into_the_next_step(self) -> None:
        self.fake.plans = [
            plan("kitchen-area", "lora slices fruit at the kitchen table, Mochi begging by the toaster", bridge=""),
            plan("kitchen-area", "lora slices fruit at the kitchen table, Mochi begging by the toaster", bridge="Hungry, lora walked to the kitchen with Mochi."),
        ]
        result = simulation.advance_simulation()
        self.assertIn('"bridge" was empty', self.fake.prompts[1])
        self.assertEqual(result["bridge"], "hungry, lora walked to the kitchen with Mochi.")
        prompt = self.fake.prompts[0]
        self.assertNotIn('"minutes": 20', prompt)
        self.assertIn("hang one load of laundry 10 to 15", prompt)
        self.assertIn('"minutes_reason"', prompt)
        self.now = at(14, 20)
        self.fake.prompts.clear()
        self.fake.plans = [plan("living-room", "lora folds blankets on the living room sofa, Mochi chewing a cushion tassel")]
        simulation.advance_simulation()
        self.assertIn("How she got here: hungry, lora walked to the kitchen with Mochi.", self.fake.prompts[0])

    def test_clean_bridge(self) -> None:
        self.assertEqual(us.clean_bridge("Then, *The sun set* and lora went in."), "the sun set and lora went in.")
        self.assertEqual(us.clean_bridge("Mochi tugged her sleeve toward the porch."), "Mochi tugged her sleeve toward the porch.")
        self.assertEqual(us.clean_bridge(None), "")
        self.assertEqual(us.narrator_message("a", "b"), "*before, a\nnow, b*")
        self.assertEqual(us.extension_message("5 minutes"), "*lora stays where she is a little longer*\ntime until next change: 5 minutes")

    def test_extend_command_is_found_but_narrator_lines_are_skipped(self) -> None:
        messages = [
            {"timestamp": 1, "sender": "user", "display_name": "Narrator", "message": us.narrator_message("a", "b", "5 minutes")},
            {"timestamp": 2, "sender": "ai", "display_name": "Lora", "message": "[yawns] Narrator extend time please"},
        ]
        self.assertEqual(us.narrator_command_from(messages), ("extend", "Lora", "", 2))
        messages.append({"timestamp": 3, "sender": "ai", "display_name": "Lora", "message": "hey @narrator the library"})
        self.assertEqual(us.narrator_command_from(messages)[:3], ("go", "Lora", "the library"))

    def test_extend_keeps_her_there_once(self) -> None:
        schedule = {"every": 10, "times": [], "ai": True}
        self.fake.plans = [
            plan("kitchen-area", "lora slices fruit at the kitchen table, Mochi begging by the toaster", minutes=20)
        ]
        first = simulation.advance_simulation(schedule=schedule)
        self.now = at(14, 20)
        self.fake.sent.clear()
        self.fake.plans = []
        stamp = int(self.now.timestamp() * 1000)
        result = simulation.advance_simulation(schedule=schedule, extend_at=stamp, requested_by="Lora")
        world = self.state()["world"]
        self.assertTrue(result["extended"])
        self.assertEqual(world["lora"]["location"], "kitchen-area")
        self.assertEqual(world["setting"], first["setting"])
        self.assertEqual(world["last_extend_at"], stamp)
        self.assertEqual(self.fake.sent[0][0], "message")
        self.assertIn("time until next change: 20 minutes", self.fake.sent[0][1])
        self.assertEqual(result["delay"], 20 * 60)
        self.now = at(14, 40)
        self.fake.plans = [plan("living-room", "lora folds blankets on the living room sofa, Mochi chewing a cushion tassel")]
        again = simulation.advance_simulation(schedule=schedule, extend_at=stamp, requested_by="Lora")
        self.assertFalse(again["extended"])
        self.assertEqual(self.state()["world"]["lora"]["location"], "living-room")

    def test_request_naming_two_places_uses_the_later_one(self) -> None:
        catalog = simulation.catalog_for(self.prefs["environment"])
        self.assertEqual(simulation.requested_place(catalog, "leave the kitchen and go to the library"), "library")
        self.assertEqual(simulation.requested_place(catalog, "I want to go to the treehouse with the telescope"), "treehouse")

    def test_carried_out_request_is_not_applied_again(self) -> None:
        self.fake.plans = [plan("treehouse", "lora climbs into the treehouse with Mochi, telescope by the tiny window")]
        simulation.advance_simulation(request="take me to the treehouse", requested_by="Lora")
        self.assertEqual(self.state()["world"]["lora"]["location"], "treehouse")
        self.now = at(14, 10)
        self.fake.prompts.clear()
        self.fake.plans = [plan("playground", "lora swings on the playground swing set, Mochi chasing the seesaw shadow")]
        simulation.advance_simulation(request="take me to the treehouse", requested_by="Lora")
        self.assertNotIn("Request from the chat", self.fake.prompts[0])
        self.assertEqual(self.state()["world"]["lora"]["location"], "playground")


CONSERVATORY = {
    "name": "Hidden glass conservatory",
    "type": "indoor",
    "description": "A small glass room hidden behind the backyard fence, warm and full of ferns.",
    "objects": ["ferns", "wicker chair", "glass roof"],
    "connection_from": "backyard",
    "via": "a gap in the backyard fence",
}
INTO_CONSERVATORY = "lora slips into the hidden glass conservatory with Mochi, ferns brushing the wicker chair"


def discover(**changes) -> dict:
    return plan("new", INTO_CONSERVATORY, new_location=dict(CONSERVATORY, **changes))


class ChangeCommandTest(Base):
    def test_weather_command_changes_weather_and_she_stays(self) -> None:
        self.kitchen_step()
        self.now = at(14, 20)
        self.fake.plans = [plan("kitchen-area", "lora slices fruit at the kitchen table, Mochi by the toaster, rain on the window", weather="clear")]
        result = simulation.advance_simulation(request="rain", requested_by="lora", check_chat=False, weather="rain")
        world = self.state()["world"]
        self.assertEqual(world["lora"]["location"], "kitchen-area")
        self.assertEqual(world["weather"], "rain")
        prompt = self.fake.prompts[-1]
        self.assertIn('"location" must be "kitchen-area"', prompt)
        self.assertIn("The weather is now rain.", prompt)
        self.assertIn("lora stays where she is", prompt)
        self.assertIn("rain on the window", result["setting"])

    def test_change_command_keeps_her_in_place(self) -> None:
        self.kitchen_step()
        self.now = at(14, 20)
        self.fake.plans = [plan("kitchen-area", "lora slices fruit at the kitchen table under dimmed lights, Mochi by the toaster")]
        simulation.advance_simulation(request="turn the lights down", requested_by="lora", check_chat=False, stay_here=True)
        self.assertEqual(self.state()["world"]["lora"]["location"], "kitchen-area")
        self.assertIn("turn the lights down. lora stays where she is", self.fake.prompts[-1])

    def test_same_change_asked_again_is_still_done(self) -> None:
        self.kitchen_step()
        for minute in (20, 40):
            self.now = at(14, minute)
            self.fake.plans = [plan("kitchen-area", f"lora at the kitchen table under dimmed lights at 2:{minute}, Mochi by the toaster")]
            result = simulation.advance_simulation(request="turn the lights down", requested_by="lora", check_chat=False, stay_here=True)
            self.assertIn("dimmed lights", result["setting"])


class ExactRequestTest(Base):
    def test_a_request_is_narrated_exactly(self) -> None:
        self.kitchen_step()
        self.now = at(14, 20)
        words = "I'm going to the living room to fold the blankets, I can't sit still"
        self.fake.plans = [plan("living-room", "lora folds blankets on the living room sofa, Mochi with her")]
        simulation.advance_simulation(request=words, requested_by="lora", check_chat=False)
        prompt = self.fake.prompts[-1]
        self.assertIn(f'lora asked for this, in these words: "{words}"', prompt)
        self.assertIn("Narrate exactly what was asked.", prompt)
        self.assertIn("Do not add new actions for her", prompt)

    def test_an_automatic_move_has_no_request_rules(self) -> None:
        self.kitchen_step()
        self.now = at(14, 20)
        self.fake.plans = [plan("living-room", "lora folds blankets on the living room sofa, Mochi with her")]
        simulation.advance_simulation(check_chat=False)
        self.assertNotIn("Narrate exactly what was asked", self.fake.prompts[-1])

    def test_weather_request_words_are_the_weather(self) -> None:
        self.kitchen_step()
        self.now = at(14, 20)
        self.fake.plans = [plan("kitchen-area", "lora slices fruit at the kitchen table, Mochi by the toaster, rain on the window")]
        simulation.advance_simulation(request="rain", requested_by="lora", check_chat=False, weather="rain")
        self.assertIn('lora asked for this, in these words: "change the weather to rain"', self.fake.prompts[-1])


class HeadsUpTest(Base):
    def test_heads_up_message(self) -> None:
        message = us.heads_up_message("1 minute")
        self.assertEqual(
            message,
            "*heads up, lora: your surroundings will change automatically in 1 minute.*\n"
            'if you want to stay where you are a little longer, say "Narrator extend time please". '
            'if you would like to do something else, say "hey @narrator" and what you want to do or where you want to go. '
            'if you want to change the weather, please say "narrator change weather to per your request" basically, '
            "if you want me to change anything around you or your world. please tell me. "
            "if you dont like where you are at please tell me at the heads up when i ask you and ill move you to your specified area. "
            "I will check back every so often and ask you the last minute. please dont conversate with me.",
        )

    def test_narrator_understands_her_commands(self) -> None:
        def one(text, sender="ai", name=""):
            return us.narrator_command_from([{"timestamp": 1, "sender": sender, "display_name": name, "message": text}])[:3]

        self.assertEqual(one("Narrator extend time please. [looks back at Master]")[0], "extend")
        self.assertEqual(one("hey @narrator I want to go to the pond")[:1], ("go",))
        self.assertEqual(one("narrator change weather to light rain please"), ("weather", "Lora", "light rain"))
        self.assertEqual(one("[looks up] ...Narrator, change the weather to a thunderstorm."), ("weather", "Lora", "a thunderstorm"))
        self.assertEqual(one("narrator can you make it night"), ("change", "Lora", "make it night"))
        self.assertEqual(one("Narrator, turn the lights down a little."), ("change", "Lora", "turn the lights down a little"))
        self.assertEqual(one("narrator please add a hammock between the trees"), ("change", "Lora", "add a hammock between the trees"))
        for chatter in (
            "the narrator made it rain earlier",
            "i told the narrator about you",
            "Narrator... [sighs] you're annoying",
            "narrator change weather to per your request",
        ):
            self.assertEqual(one(chatter)[0], "", chatter)
        self.assertEqual(one(us.heads_up_message("1 minute"), "user", "Narrator")[0], "")

    def test_narrator_says_switches_back_to_whoever_was_chatting(self) -> None:
        self.fake.chat = [{"sender": "user", "display_name": "Mochi", "message": "hi lora", "timestamp": int(time.time() * 1000)}]
        reply, problem = us.narrator_says(us.heads_up_message("1 minute"))
        self.assertEqual(self.fake.profiles, ["Narrator", "Mochi"])
        self.assertEqual(self.fake.sent[-1][0], "message")
        self.assertIn("heads up, lora", self.fake.sent[-1][1])
        self.assertEqual(problem, "")

    def test_her_extend_reply_to_the_heads_up_is_found(self) -> None:
        messages = [
            {"timestamp": 1, "sender": "user", "display_name": "Narrator", "message": us.heads_up_message("1 minute")},
            {"timestamp": 2, "sender": "ai", "display_name": "", "message": "[looks up] ...Narrator extend time please."},
        ]
        self.assertEqual(us.narrator_command_from(messages)[0], "extend")
        self.assertEqual(us.narrator_command_from(messages[:1])[0], "")


class DiscoveryTest(Base):
    """DeepSeek may propose a new place. Python checks it, saves it, and keeps it connected."""

    def to_backyard(self) -> None:
        self.fake.plans = [plan("backyard", "lora hangs the bird feeder in the backyard by the fence, Mochi sniffing the garden hose")]
        simulation.advance_simulation()
        self.assertEqual(self.state()["world"]["lora"]["location"], "backyard")
        self.now = at(14, 10)
        self.fake.prompts.clear()

    def discover_conservatory(self) -> dict:
        self.to_backyard()
        self.fake.plans = [discover()]
        return simulation.advance_simulation()

    def test_existing_place_needs_no_discovery(self) -> None:
        self.fake.plans = [plan("kitchen-area", "lora rinses berries at the kitchen sink, Mochi watching the oven light", new_location=None)]
        simulation.advance_simulation()
        world = self.state()["world"]
        self.assertEqual(world["lora"]["location"], "kitchen-area")
        self.assertEqual(world["discovered"], {})
        self.assertIn('"new_location": null', self.fake.prompts[0])

    def test_valid_new_place_is_accepted_and_saved(self) -> None:
        result = self.discover_conservatory()
        world = self.state()["world"]
        place = world["discovered"]["hidden-glass-conservatory"]
        self.assertEqual(world["lora"]["location"], "hidden-glass-conservatory")
        self.assertEqual(world["mochi"]["location"], "hidden-glass-conservatory")
        self.assertEqual((place["zone"], place["from"]), ("indoor", "backyard"))
        self.assertEqual(place["items"], ["ferns", "wicker chair", "glass roof"])
        self.assertEqual(result["discovered"], "Hidden glass conservatory")
        self.assertEqual(result["location"], "Hidden glass conservatory")
        self.assertEqual(world["journal"][-1]["from"], "backyard")
        self.assertTrue(any(item["kind"] == "discovery" and item["importance"] == 3 for item in world["memories"]))
        self.assertEqual(self.state()["prefs"]["environment"], self.prefs["environment"])
        self.assertIn(("message", result["message"]), self.fake.sent)

    def test_discovered_place_survives_restart(self) -> None:
        self.discover_conservatory()
        simulation._catalog_cache.clear()
        info = simulation.summary()
        self.assertEqual(info["place"], "Hidden glass conservatory (indoor, discovered)")
        self.assertEqual(info["discovered"], ["Hidden glass conservatory"])
        saved = simulation.load_world(us.read_state())
        catalog = simulation.catalog_for(self.prefs["environment"], saved["discovered"])
        self.assertEqual(catalog["places"]["hidden-glass-conservatory"]["origin"], "discovered")
        self.assertNotIn("hidden-glass-conservatory", simulation.catalog_for(self.prefs["environment"])["places"])
        self.now = at(14, 20)
        self.fake.plans = [plan("backyard", "lora carries a fern pot out to the backyard picnic table, Mochi trotting behind")]
        simulation.advance_simulation()
        self.assertEqual(self.state()["world"]["lora"]["location"], "backyard")

    def test_invalid_new_places_are_rejected(self) -> None:
        self.to_backyard()
        self.fake.plans = [
            discover(name="X!"),
            discover(type="space"),
            discover(name="Glowing portal room", description="A room with a portal to another planet."),
            discover(description="glass"),
            discover(objects="ferns"),
            plan("tool-shed", "lora sorts garden gloves in the tool shed, Mochi sniffing a bucket"),
        ]
        with mock.patch.object(simulation, "PLAN_TRIES", 6):
            simulation.advance_simulation()
        reasons = "\n".join(self.fake.prompts[1:])
        for reason in ("plain name", "indoor or outdoor", "does not fit her world", "one-sentence description", "list of up to 6"):
            self.assertIn(reason, reasons)
        self.assertEqual(self.state()["world"]["discovered"], {})

    def test_duplicate_new_place_is_rejected(self) -> None:
        self.discover_conservatory()
        self.now = at(14, 20)
        self.fake.plans = [plan("backyard", "lora carries a fern pot out to the backyard picnic table, Mochi trotting behind")]
        simulation.advance_simulation()
        self.now = at(14, 30)
        self.fake.prompts.clear()
        self.fake.plans = [
            discover(name="Greenhouse", type="outdoor"),
            discover(),
            plan("tool-shed", "lora sorts garden gloves in the tool shed, Mochi sniffing a bucket"),
        ]
        simulation.advance_simulation()
        self.assertIn("already exists in her world as the greenhouse", self.fake.prompts[1])
        self.assertIn("already exists in her world as the hidden glass conservatory", self.fake.prompts[2])
        self.assertEqual(list(self.state()["world"]["discovered"]), ["hidden-glass-conservatory"])

    def test_impossible_connection_is_rejected(self) -> None:
        self.to_backyard()
        self.fake.plans = [
            discover(connection_from="kitchen-area"),
            discover(connection_from="moon-base"),
            plan("tool-shed", "lora sorts garden gloves in the tool shed, Mochi sniffing a bucket"),
        ]
        simulation.advance_simulation()
        self.assertIn('has to connect to where she is now, "backyard"', self.fake.prompts[1])
        self.assertIn('"connection_from" has to be a place in her world', self.fake.prompts[2])
        self.assertEqual(self.state()["world"]["discovered"], {})

    def test_no_teleport_to_or_from_discovered_place(self) -> None:
        self.discover_conservatory()
        world = simulation.load_world(us.read_state())
        catalog = simulation.catalog_for(self.prefs["environment"], world["discovered"])
        links = simulation.load_map()
        self.assertTrue(simulation.can_move(catalog, links, "backyard", "hidden-glass-conservatory")[0])
        self.assertFalse(simulation.can_move(catalog, links, "fireplace", "hidden-glass-conservatory")[0])
        self.assertFalse(simulation.can_move(catalog, links, "kitchen-area", "hidden-glass-conservatory")[0])
        self.assertFalse(simulation.can_move(catalog, links, "hidden-glass-conservatory", "fireplace")[0])
        self.now = at(14, 20)
        self.fake.prompts.clear()
        self.fake.plans = [
            plan("kitchen-area", "lora washes the fern pot at the kitchen sink, Mochi by the toaster"),
            plan("backyard", "lora carries a fern pot out to the backyard picnic table, Mochi trotting behind"),
        ]
        simulation.advance_simulation()
        self.assertIn("she can only go back to the backyard", self.fake.prompts[1])
        self.assertEqual(self.state()["world"]["lora"]["location"], "backyard")

    def test_no_discovery_late_at_night_or_past_the_daily_limit(self) -> None:
        self.now = at(23, 0)
        self.fake.plans = [
            discover(connection_from="fireplace"),
            plan("floor-rug", "lora curls up on the floor rug with Mochi, curtains drawn, the room quiet and dark"),
        ]
        simulation.advance_simulation()
        self.assertIn('"new_location" is null.', self.fake.prompts[0])
        self.assertIn("late at night", self.fake.prompts[1])
        world = {"discovered": {f"place-{n}": {"discovered_at": simulation.iso(at(9 + n))} for n in range(3)}}
        self.assertIn("already found 3 new places today", simulation.discovery_block(world, at(14), False, ""))

    def test_failed_deepseek_keeps_discovered_world(self) -> None:
        self.discover_conservatory()
        before = self.digest()
        self.now = at(14, 20)
        self.fake.plans = [RuntimeError("DeepSeek returned 503")] * simulation.PLAN_TRIES
        with self.assertRaises(RuntimeError):
            simulation.advance_simulation()
        self.assertEqual(self.digest(), before)
        self.assertIn("hidden-glass-conservatory", self.state()["world"]["discovered"])

    def test_failed_kindroid_keeps_discovery_and_retries(self) -> None:
        self.to_backyard()
        self.fake.fail_tell = True
        self.fake.plans = [discover()]
        result = simulation.advance_simulation()
        world = self.state()["world"]
        self.assertTrue(result["pending"])
        self.assertIn("hidden-glass-conservatory", world["discovered"])
        self.assertEqual(world["lora"]["location"], "hidden-glass-conservatory")
        self.fake.fail_tell = False
        self.fake.sent.clear()
        retry = simulation.advance_simulation()
        self.assertTrue(retry["delivered"])
        self.assertEqual(self.fake.sent, [("message", result["message"])])
        self.assertIn("hidden-glass-conservatory", self.state()["world"]["discovered"])

    def test_request_cannot_jump_to_an_unreachable_discovered_place(self) -> None:
        self.discover_conservatory()
        self.now = at(14, 20)
        self.fake.plans = [plan("backyard", "lora carries a fern pot out to the backyard picnic table, Mochi trotting behind")]
        simulation.advance_simulation()
        self.now = at(14, 30)
        self.fake.plans = [plan("fireplace", "lora sets the fern pot by the fireplace tools, Mochi sniffing the wood logs")]
        simulation.advance_simulation()
        self.now = at(14, 40)
        self.fake.prompts.clear()
        self.fake.plans = [
            plan("hidden-glass-conservatory", INTO_CONSERVATORY),
            plan("backyard", "lora heads back out to the backyard birdhouse, Mochi racing across the grass"),
        ]
        simulation.advance_simulation(request="take me to the hidden glass conservatory", requested_by="Lora")
        self.assertIn("can only be reached from the backyard", self.fake.prompts[1])
        self.assertEqual(self.state()["world"]["lora"]["location"], "backyard")

    def test_old_saved_world_without_discovered_still_loads(self) -> None:
        self.fake.plans = [plan("kitchen-area", "lora rinses berries at the kitchen sink, Mochi watching the oven light")]
        simulation.advance_simulation()
        state = self.state()
        del state["world"]["discovered"]
        (self.tmp / "state.json").write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(simulation.summary()["discovered"], [])
        self.now = at(14, 10)
        self.fake.plans = [plan("living-room", "lora folds blankets on the living room sofa, Mochi chewing a cushion tassel")]
        simulation.advance_simulation()
        self.assertEqual(self.state()["world"]["discovered"], {})


class InlineThread:
    """Tk callbacks need the main loop; without it, run the worker on the test thread."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self) -> None:
        self.target(*self.args, **self.kwargs)


class GuiTest(Base):
    """Change action runs a step, Stop keeps it stopped, and a restart resumes the timer."""

    def setUp(self) -> None:
        super().setUp()
        import gui

        self.gui = gui
        self.calls: list[dict] = []
        slides = self.tmp / "slides"
        slides.mkdir()
        self.patches += [
            mock.patch.object(gui, "SLIDES_DIR", slides),
            mock.patch.object(simulation, "advance_simulation", self.fake_step),
            mock.patch.object(gui, "threading", types.SimpleNamespace(Thread=InlineThread)),
        ]
        for patch in self.patches[-3:]:
            patch.start()

    def fake_step(
        self,
        prior="",
        environment="",
        backstory="",
        morning=False,
        request="",
        requested_by="",
        dry_run=False,
        check_chat=True,
        **_kwargs,
    ):
        self.calls.append(
            {
                "prior": prior,
                "morning": morning,
                "extend_at": _kwargs.get("extend_at", 0),
                "request": request,
                "weather": _kwargs.get("weather", ""),
                "stay_here": _kwargs.get("stay_here", False),
            }
        )
        return {"setting": "lora plays tug with Mochi in the open field", "pending": False, "error": "", "delivered": False}

    def pump(self, app, until) -> None:
        for _ in range(200):
            app.update()
            if until():
                return
            time.sleep(0.01)

    def test_change_action_and_stop(self) -> None:
        app = self.gui.App()
        app.withdraw()
        try:
            app.start()
            self.pump(app, lambda: app.phase == "waiting")
            self.assertEqual(len(self.calls), 1)
            self.assertTrue(simulation.schedule()["running"])
            self.assertEqual(app.prior.get("1.0", "end").strip(), "lora plays tug with Mochi in the open field")
            app.stop()
            self.assertEqual(app.phase, "idle")
            self.assertFalse(simulation.schedule()["running"])
            app.deadline = time.monotonic() - 1
            app.clock()
            app.update()
            self.assertEqual(len(self.calls), 1)
        finally:
            app.destroy()

    def test_picture_has_no_boxes_behind_labels(self) -> None:
        from PIL import Image

        source = self.tmp / "slides" / "001.png"
        Image.new("RGB", (400, 300), (200, 100, 50)).save(source)
        app = self.gui.App()
        app.withdraw()
        try:
            app._ensure_plate()
            app.opacity_value = 0
            app._draw_plate(source, 300, 200)
            photo = app._plate_photo._PhotoImage__photo
            center = tuple(int(part) for part in re.findall(r"\d+", str(photo.get(150, 100))))
            edge = tuple(int(part) for part in re.findall(r"\d+", str(photo.get(4, 4))))
            self.assertEqual(center, (200, 100, 50))
            self.assertNotEqual(edge, center)
        finally:
            app.destroy()

    def test_click_on_picture_drags_the_window(self) -> None:
        app = self.gui.App()
        app.withdraw()
        grabbed: list[int] = []
        try:
            app._ensure_plate()
            self.assertIn("_grab_from_picture", app.plate_label.bind("<ButtonPress-1>"))
            with mock.patch.object(self.gui, "drag_window", grabbed.append):
                app._grab_from_picture()
            main = self.gui.ctypes.windll.user32.GetAncestor(app.winfo_id(), 2)
            self.assertEqual(grabbed, [main])
        finally:
            app.destroy()

    def test_drag_window_hands_the_press_to_the_title_bar(self) -> None:
        calls: list[tuple] = []

        class Send:
            def __call__(self, *args):
                calls.append(("send", *args))

        def cursor(point):
            point._obj.x, point._obj.y = -20, 300

        user32 = types.SimpleNamespace(
            GetCursorPos=cursor,
            SetForegroundWindow=lambda hwnd: calls.append(("front", hwnd.value)),
            ReleaseCapture=lambda: calls.append(("release",)),
            SendMessageW=Send(),
        )
        fake = types.SimpleNamespace(windll=types.SimpleNamespace(user32=user32), byref=ctypes.byref, c_ssize_t=ctypes.c_ssize_t)
        with mock.patch.object(self.gui, "ctypes", fake):
            self.gui.drag_window(1234)
        self.assertEqual(calls, [("front", 1234), ("release",), ("send", 1234, 0x00A1, 2, (300 << 16) | 0xFFEC)])

    def test_peek_reads_every_page_since_the_last_step(self) -> None:
        app = self.gui.App()
        app.withdraw()
        try:
            step_start = int(time.time() * 1000)
            filler = [
                {"timestamp": step_start + i, "sender": "ai", "display_name": "Lora", "message": f"line {i}"}
                for i in range(20)
            ]
            ask = {
                "timestamp": step_start + 100,
                "sender": "ai",
                "display_name": "Lora",
                "message": "Hey @narrator, take me to the treehouse",
            }
            seen: list[int] = []

            def fetch(after, group_id=None):
                seen.append(after)
                return (filler, 0) if len(seen) == 1 else ([ask], 0)

            with mock.patch.object(simulation, "last_step_ms", lambda: step_start), mock.patch.object(us, "fetch_messages", fetch):
                app.arm_timer(app.read_options(), delay=600)
                app._peek_narrator()
                app.update()
            self.assertEqual(seen[0], step_start - 60_000)
            self.assertEqual(app.narrator_request, "take me to the treehouse")
        finally:
            app.destroy()

    def test_error_while_idle_shows_the_window(self) -> None:
        app = self.gui.App()
        app.withdraw()
        try:
            self.assertFalse(app.countdown.grid_info())
            app.times.delete(0, "end")
            app.times.insert(0, "25:00")
            app.start()
            app.update()
            self.assertTrue(app.countdown.grid_info())
            self.assertEqual(app.countdown.cget("text"), "Use times like 10:00 PM.")
        finally:
            app.destroy()

    def test_heads_up_one_minute_before_then_the_chat_is_read(self) -> None:
        said = []
        order = []

        def narrator_says(message):
            said.append(message)
            order.append("heads-up")
            return '[looks up] ...Narrator extend time please.', ""

        with mock.patch.object(self.gui.update_scene, "narrator_says", narrator_says), mock.patch.object(
            self.gui.update_scene, "recent_messages", lambda after, pages=3: order.append("read") or []
        ):
            app = self.gui.App()
            app.withdraw()
            try:
                app.heads_up.set(True)
                app.arm_timer(app.read_options(), delay=600)
                now = us.la_now()
                app.deadline = time.monotonic() + 120
                app.follow_schedule(now)
                self.assertEqual(said, [])
                app.deadline = time.monotonic() + 59
                app.follow_schedule(now)
                self.pump(app, lambda: not app.heads_up_busy)
                self.assertEqual(len(said), 1)
                self.assertIn("will change automatically in 1 minute.", said[0])
                self.assertIn('say "Narrator extend time please"', said[0])
                self.assertIn('say "hey @narrator"', said[0])
                self.assertIn("please dont conversate with me.", said[0])
                self.assertFalse(app.narrator_peeked)
                app.deadline = time.monotonic() + 29
                app.follow_schedule(now)
                self.pump(app, lambda: not app.narrator_peeking)
                self.assertEqual(order[:2], ["heads-up", "read"])
                app.follow_schedule(now)
                self.assertEqual(len(said), 1)
            finally:
                app.destroy()

    def test_second_extend_in_the_heads_up_answer_is_not_lost(self) -> None:
        """10:21 AM: her first extend was already used, and 40 minutes of chat hid the new one."""
        used = int(time.time() * 1000) - 20 * 60 * 1000
        old_chat = [
            {"timestamp": used - 1000 + index, "sender": "user", "display_name": "Master", "message": f"line {index}"}
            for index in range(59)
        ] + [{"timestamp": used, "sender": "ai", "display_name": "", "message": "Narrator extend time please."}]

        with mock.patch.object(
            self.gui.update_scene, "narrator_says",
            lambda message: ("Narrator extend time please. [looks back at Master] ...I don't want you to disappear.", ""),
        ), mock.patch.object(self.gui.update_scene, "recent_messages", lambda after, pages=3: list(old_chat)), mock.patch.object(
            simulation, "last_extend_ms", lambda: used
        ):
            app = self.gui.App()
            app.withdraw()
            try:
                app.heads_up.set(True)
                app.arm_timer(app.read_options(), delay=600)
                now = us.la_now()
                app.deadline = time.monotonic() + 59
                app.follow_schedule(now)
                self.pump(app, lambda: not app.heads_up_busy)
                self.assertGreater(app.narrator_extend_at, used)
                app.deadline = time.monotonic() + 29
                app.follow_schedule(now)
                self.pump(app, lambda: not app.narrator_peeking)
                self.assertGreater(app.narrator_extend_at, used)
                app.deadline = time.monotonic() - 1
                app.follow_schedule(now)
                self.pump(app, lambda: len(self.calls) == 1)
                self.assertEqual(len(self.calls), 1)
                self.assertGreater(self.calls[0]["extend_at"], used)
            finally:
                app.destroy()

    def test_an_extend_that_was_already_used_is_not_taken_again(self) -> None:
        used = int(time.time() * 1000) - 60_000
        with mock.patch.object(simulation, "last_extend_ms", lambda: used):
            app = self.gui.App()
            app.withdraw()
            try:
                app.arm_timer(app.read_options(), delay=600)
                app._take_command(("extend", "lora", "", used))
                self.assertEqual(app.narrator_extend_at, 0)
                app._take_command(("go", "lora", "the pond", used + 5))
                app._take_command(("extend", "lora", "", used + 1))
                self.assertEqual(app.narrator_request, "the pond")
                self.assertEqual(app.narrator_extend_at, 0)
            finally:
                app.destroy()

    def test_mid_wait_check_does_her_weather_command_right_away(self) -> None:
        stamp = int(time.time() * 1000)
        chat = [{"timestamp": stamp, "sender": "ai", "display_name": "", "message": "[looks at the sky] narrator change weather to light rain"}]
        with mock.patch.object(self.gui.update_scene, "recent_messages", lambda after, pages=3: list(chat)), mock.patch.object(
            simulation, "last_extend_ms", lambda: 0
        ):
            app = self.gui.App()
            app.withdraw()
            try:
                app.arm_timer(app.read_options(), delay=20 * 60)
                app.deadline = time.monotonic() + 15 * 60
                app.follow_schedule(us.la_now())
                self.assertEqual(self.calls, [])
                app.next_mid_check = time.monotonic() - 1
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: len(self.calls) == 1)
                self.assertEqual(self.calls[0]["weather"], "light rain")
            finally:
                app.destroy()

    def test_mid_wait_extend_waits_for_the_end(self) -> None:
        stamp = int(time.time() * 1000)
        chat = [{"timestamp": stamp, "sender": "ai", "display_name": "", "message": "Narrator extend time please."}]
        with mock.patch.object(self.gui.update_scene, "recent_messages", lambda after, pages=3: list(chat)), mock.patch.object(
            simulation, "last_extend_ms", lambda: 0
        ):
            app = self.gui.App()
            app.withdraw()
            try:
                app.heads_up.set(False)
                app.arm_timer(app.read_options(), delay=20 * 60)
                app.deadline = time.monotonic() + 15 * 60
                app.next_mid_check = time.monotonic() - 1
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: not app.narrator_peeking)
                self.assertEqual(self.calls, [])
                self.assertEqual(app.narrator_extend_at, stamp)
                app.deadline = time.monotonic() + 50
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: not app.narrator_peeking)
                app.deadline = time.monotonic() - 1
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: len(self.calls) == 1)
                self.assertEqual(self.calls[0]["extend_at"], stamp)
            finally:
                app.destroy()

    def test_change_action_gives_the_heads_up_first_then_follows_her_request(self) -> None:
        said = []

        def narrator_says(message):
            said.append(message)
            return "hey @narrator I want to go sit on the porch swing with Mochi, my arms are tired", ""

        with mock.patch.object(self.gui.update_scene, "narrator_says", narrator_says), mock.patch.object(
            self.gui.update_scene, "recent_messages", lambda after, pages=3: []
        ), mock.patch.object(simulation, "last_extend_ms", lambda: 0):
            app = self.gui.App()
            app.withdraw()
            try:
                app.heads_up.set(True)
                app.start()
                self.assertEqual(self.calls, [])
                self.assertEqual(app.phase, "waiting")
                self.assertAlmostEqual(app.deadline - time.monotonic(), 60, delta=3)
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: not app.heads_up_busy)
                self.assertEqual(len(said), 1)
                self.assertIn("will change automatically in 1 minute.", said[0])
                self.assertEqual(self.calls, [])
                app.deadline = time.monotonic() + 29
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: not app.narrator_peeking)
                app.deadline = time.monotonic() - 1
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: len(self.calls) == 1)
                self.assertEqual(self.calls[0]["request"], "I want to go sit on the porch swing with Mochi, my arms are tired")
            finally:
                app.destroy()

    def test_change_action_is_instant_when_the_heads_up_is_off(self) -> None:
        app = self.gui.App()
        app.withdraw()
        try:
            app.heads_up.set(False)
            app.start()
            self.pump(app, lambda: len(self.calls) == 1)
            self.assertEqual(len(self.calls), 1)
        finally:
            app.destroy()

    def test_no_heads_up_when_switched_off_or_the_action_is_short(self) -> None:
        said = []
        with mock.patch.object(self.gui.update_scene, "narrator_says", lambda message: said.append(message) or ("", "")):
            app = self.gui.App()
            app.withdraw()
            try:
                app.heads_up.set(False)
                app.arm_timer(app.read_options(), delay=600)
                app.deadline = time.monotonic() + 50
                app.follow_schedule(us.la_now())
                app.heads_up.set(True)
                app.arm_timer(app.read_options(), delay=90)
                app.deadline = time.monotonic() + 50
                app.follow_schedule(us.la_now())
                self.assertEqual(said, [])
            finally:
                app.destroy()

    def test_timer_uses_deepseek_length_when_checked(self) -> None:
        app = self.gui.App()
        app.withdraw()
        try:
            app.minutes.delete(0, "end")
            app.minutes.insert(0, "10")
            done = {"setting": "lora reads in the library with Mochi", "pending": False, "error": "", "minutes": 25}
            app.ai_minutes.set(True)
            app._shift_done(done, "", app.read_options())
            self.assertAlmostEqual(app.deadline - time.monotonic(), 25 * 60, delta=5)
            app.ai_minutes.set(False)
            app._shift_done(done, "", app.read_options())
            self.assertAlmostEqual(app.deadline - time.monotonic(), 10 * 60, delta=5)
        finally:
            app.destroy()

    def test_restart_resumes_timer(self) -> None:
        simulation.set_schedule(True, self.now + timedelta(minutes=7))
        app = self.gui.App()
        app.withdraw()
        try:
            app._resume_schedule()
            self.assertEqual(app.phase, "waiting")
            self.assertAlmostEqual(app.deadline - time.monotonic(), 7 * 60, delta=5)
        finally:
            app.destroy()

    def test_wand_timer_stops_with_the_app(self) -> None:
        app = self.gui.App()
        app.withdraw()
        try:
            app.wand_on.set(True)
            app.start()
            self.pump(app, lambda: app.phase == "waiting")
            self.assertIsNotNone(app.wand_id)
            app.stop()
            self.assertIsNone(app.wand_id)
            self.assertEqual(app.phase, "idle")
        finally:
            app.destroy()


class WandTest(unittest.TestCase):
    """The wand timer and the tap. No browser."""

    def test_talk_minutes_stay_in_range(self) -> None:
        import wand

        self.assertEqual(wand.talk_minutes("10"), 10)
        with self.assertRaises(RuntimeError):
            wand.talk_minutes("0")
        with self.assertRaises(RuntimeError):
            wand.talk_minutes("soon")

    def test_tap_sends_the_suggestion_as_mochi(self) -> None:
        with (
            mock.patch.object(us, "load_env"),
            mock.patch.object(us, "require_keys", return_value=("deepseek", "kindroid", "ai")),
            mock.patch.object(us, "activate_named_profile", return_value="Mochi") as profile,
            mock.patch.object(us, "request_body", return_value=" *Mochi nudges her hand.* ") as asked,
            mock.patch.object(us, "tell_lora", return_value="she laughs") as sent,
        ):
            text = wand.tap()
        self.assertEqual(text, "*Mochi nudges her hand.*")
        profile.assert_called_once_with("mochi_profile")
        self.assertIn("suggest-user-message", asked.call_args.args[0])
        sent.assert_called_once_with("kindroid", "ai", "*Mochi nudges her hand.*")


if __name__ == "__main__":
    unittest.main()
