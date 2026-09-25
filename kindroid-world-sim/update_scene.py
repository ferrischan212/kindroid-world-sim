"""Write a new Kindroid current setting that is different from the last one.

DeepSeek invents the next scene. Narrator states the previous action and the new one,
the setting is saved, and the chat returns to Master or Mochi.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
BACKUP_PATH = ROOT / "state.backup.json"
SCENE_LIMIT = 160
HISTORY_LIMIT = 8

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
KINDROID_URL = "https://api.kindroid.ai/v1/update-info"
SEND_URL = "https://api.kindroid.ai/v1/send-message"
MESSAGES_URL = "https://api.kindroid.ai/v1/get-chat-messages"

IGNORE_WORDS = {
    "a",
    "an",
    "and",
    "at",
    "from",
    "her",
    "in",
    "lora",
    "mochi",
    "nearby",
    "of",
    "on",
    "she",
    "the",
    "to",
    "under",
    "with",
}


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def require_keys() -> tuple[str, str, str]:
    deepseek = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    kindroid = os.environ.get("KINDROID_API_KEY", "").strip()
    ai_id = os.environ.get("KINDROID_AI_ID", "").strip()
    missing = [
        name
        for name, value in (
            ("DEEPSEEK_API_KEY", deepseek),
            ("KINDROID_API_KEY", kindroid),
            ("KINDROID_AI_ID", ai_id),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Add these to .env first: "
            + ", ".join(missing)
            + ". DeepSeek key: https://platform.deepseek.com/api_keys. "
            "Kindroid key and AI ID: Settings -> General -> API & advanced integrations."
        )
    return deepseek, kindroid, ai_id


_state_lock = threading.RLock()


def load_json(path: Path, fallback: dict) -> dict:
    if not path.exists():
        return fallback
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{path.name} could not be read.") from error
    if not isinstance(data, dict):
        raise RuntimeError(f"{path.name} could not be read.")
    return data


def _load_state_file() -> tuple[dict, bool]:
    """The saved state, and whether state.json itself was readable."""
    try:
        return load_json(STATE_PATH, {"history": []}), True
    except RuntimeError:
        if BACKUP_PATH.exists():
            return load_json(BACKUP_PATH, {"history": []}), False
        raise


def read_state() -> dict:
    with _state_lock:
        return _load_state_file()[0]


def update_state(mutator, validate=None) -> dict:
    """Change the state and save it. The backup only moves forward after the new state passes validate."""
    with _state_lock:
        state, main_ok = _load_state_file()
        mutator(state)
        if validate is not None:
            validate(state)
        text = json.dumps(state, indent=2) + "\n"
        if main_ok and STATE_PATH.exists():
            spare = BACKUP_PATH.parent / f"{BACKUP_PATH.name}.tmp"
            shutil.copyfile(STATE_PATH, spare)
            os.replace(spare, BACKUP_PATH)
        tmp = STATE_PATH.parent / f"{STATE_PATH.name}.tmp"
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, STATE_PATH)
        return state


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def content_words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9']+", normalize(text))
    return {word for word in words if word not in IGNORE_WORDS}


def clean_scene(text: str) -> str:
    line = text.strip().splitlines()[0].strip() if text.strip() else ""
    line = re.sub(r"^(setting\s*:\s*)", "", line, flags=re.IGNORECASE)
    if len(line) >= 2 and line[0] == line[-1] and line[0] in {'"', "'"}:
        line = line[1:-1].strip()
    return re.sub(r"\s+", " ", line).strip()


def kindroid_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def request_body(url: str, payload: dict, headers: dict, timeout: int) -> str:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"{url} returned {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Could not reach {url}: {error.reason}") from error


def post_json(url: str, payload: dict, headers: dict, timeout: int, required: bool = False) -> dict:
    body = request_body(url, payload, headers, timeout).lstrip("\ufeff").strip()
    if not body:
        if required:
            raise RuntimeError(f"{url} sent back an empty response.")
        return {}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        if required:
            raise RuntimeError(f"{url} sent back text instead of data: {body[:180]}")
        return {}
    if not isinstance(parsed, dict):
        if required:
            raise RuntimeError(f"{url} sent back an unexpected response: {body[:180]}")
        return {}
    return parsed


def ask_deepseek(
    api_key: str,
    prompt: str,
    system: str | None = None,
    max_tokens: int = 120,
    temperature: float = 1.1,
    paragraph: bool = False,
) -> str:
    payload = {
        "model": "deepseek-flash",
        "messages": [
            {
                "role": "system",
                "content": system
                or (
                    "You write one Kindroid current-setting line. "
                    "Output only that line."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "thinking": {"type": "disabled"},
        "reasoning_effort": "none",
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    result = post_json(
        DEEPSEEK_URL,
        payload,
        {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=60,
        required=True,
    )
    message = result["choices"][0]["message"]
    content = message.get("content") or ""
    if paragraph:
        return re.sub(r"\s+", " ", content).strip().strip('"').strip()
    return clean_scene(content)


def parse_json_object(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("DeepSeek did not send JSON.")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as error:
        raise ValueError("DeepSeek sent broken JSON.") from error
    if not isinstance(data, dict):
        raise ValueError("DeepSeek sent JSON that is not an object.")
    return data


def ask_deepseek_json(
    api_key: str,
    prompt: str,
    system: str,
    max_tokens: int = 700,
    temperature: float = 0.9,
) -> dict:
    """One DeepSeek call that has to come back as a JSON object."""
    payload = {
        "model": "deepseek-flash",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "thinking": {"type": "disabled"},
        "reasoning_effort": "none",
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        result = post_json(DEEPSEEK_URL, payload, headers, timeout=90, required=True)
    except RuntimeError as error:
        if "response_format" not in str(error):
            raise
        payload.pop("response_format")
        result = post_json(DEEPSEEK_URL, payload, headers, timeout=90, required=True)
    try:
        content = str(result["choices"][0]["message"].get("content") or "")
    except (KeyError, IndexError, TypeError, AttributeError) as error:
        raise ValueError("DeepSeek sent an unexpected response.") from error
    return parse_json_object(content)


def save_kindroid(api_key: str, ai_id: str, scene: str) -> None:
    post_json(
        KINDROID_URL,
        {"ai_id": ai_id, "current_scene": scene},
        kindroid_headers(api_key),
        timeout=30,
    )


def switch_profile(api_key: str, profile: dict) -> None:
    headers = kindroid_headers(api_key)
    post_json(KINDROID_URL, {"active_persona_id": profile["id"]}, headers, timeout=30)
    payload: dict = {}
    for field in ("user_name", "user_gender"):
        value = str(profile.get(field, "")).strip()
        if value:
            payload[field] = value
    if "user_backstory" in profile:
        payload["user_backstory"] = str(profile.get("user_backstory", ""))
    if profile.get("delete_user_avatar"):
        payload["user_custom_avatar"] = {"delete_user_avatar": True}
    if payload:
        post_json(KINDROID_URL, payload, headers, timeout=30)


def activate_named_profile(key: str) -> str:
    load_env(ENV_PATH)
    _, kindroid_key, _ = require_keys()
    config = load_json(CONFIG_PATH, {})
    profile = config.get(key) or {}
    if not profile.get("id"):
        raise RuntimeError(
            "Master's profile id is not saved yet. Switch to Master in Kindroid, "
            "open the update-info request, and copy active_persona_id, user_name, "
            "user_gender, and user_backstory."
        )
    switch_profile(kindroid_key, profile)
    return str(profile.get("user_name") or "Master")


def switch_back_to_master() -> str:
    try:
        activate_named_profile("master_profile")
    except Exception as caught:
        return str(caught)
    return ""


def restore_profile(profile: dict) -> str:
    name = str(profile.get("user_name") or "Master")
    try:
        if not profile.get("id"):
            return switch_back_to_master()
        load_env(ENV_PATH)
        _, kindroid_key, _ = require_keys()
        switch_profile(kindroid_key, profile)
    except Exception as caught:
        return f"Could not switch back to {name}. {caught}"
    return ""


def recent_messages(after_timestamp: int, pages: int = 3) -> list[dict]:
    """Messages after a time, oldest first. Kindroid sends 20 at a time from the start, so page forward."""
    found: list[dict] = []
    after = after_timestamp
    for _ in range(pages):
        messages, _latest = fetch_messages(after)
        if not messages:
            break
        found.extend(messages)
        newest = max(int(item.get("timestamp") or 0) for item in messages)
        if len(messages) < 20 or newest <= after:
            break
        after = newest
    return found


def chatter_profile(config: dict) -> dict:
    """The profile that last spoke in the chat, Master or Mochi.

    Look at the last 15 minutes first, which is one read while someone is chatting.
    Only if nobody spoke then, look back 3 hours.
    """
    master = config.get("master_profile") or {}
    named = {}
    for key in ("master_profile", "mochi_profile"):
        profile = config.get(key) or {}
        label = str(profile.get("user_name") or "").strip().lower()
        if label and profile.get("id"):
            named[label] = profile
    now_ms = int(datetime.now().timestamp() * 1000)
    try:
        for window in (15 * 60 * 1000, 3 * 60 * 60 * 1000):
            latest_user: tuple[int, str] | None = None
            for item in recent_messages(now_ms - window):
                if str(item.get("sender") or "") != "user":
                    continue
                label = str(item.get("display_name") or "").strip().lower()
                stamp = int(item.get("timestamp") or 0)
                if label in named and (latest_user is None or stamp >= latest_user[0]):
                    latest_user = (stamp, label)
            if latest_user:
                return named[latest_user[1]]
    except Exception:
        return master
    return master


EXTEND = re.compile(r"\bnarrator\b[\s,.:!-]*(?:please\s+)?extend\s+(?:my\s+|the\s+)?time", re.IGNORECASE)
_ASK = r"\bnarrator\b[\s,.:!-]*(?:please\s+|can you\s+|could you\s+|would you\s+)*"
WEATHER = re.compile(_ASK + r"change\s+(?:the\s+)?weather\s+(?:to\s+|into\s+)?([^.!?\n\]\*\[]+)", re.IGNORECASE)
# "narrator make it night", "narrator turn the lights down": a change around her, she stays where she is.
CHANGE = re.compile(
    _ASK + r"((?:change|make|turn|add|put|set|bring|give|switch|dim|brighten|open|close|light|let)\b[^.!?\n\]\*\[]*)",
    re.IGNORECASE,
)


def narrator_command_from(messages: list[dict]) -> tuple[str, str, str, int]:
    """The latest narrator command in chat. Narrator's own lines are skipped.

    Kinds: "go" (hey @narrator ...), "extend" (Narrator extend time please),
    "weather" (narrator change weather to ...), and "change" (narrator make/turn/add ... around her).
    """
    found = ("", "", "", 0)
    for item in sorted(messages, key=lambda item: int(item.get("timestamp") or 0)):
        sender = str(item.get("sender") or "")
        name = str(item.get("display_name") or "").strip().lower()
        if name == "narrator" or sender not in {"user", "ai"}:
            continue
        text = str(item.get("message") or "")
        who = str(item.get("display_name") or "").strip() or ("Lora" if sender == "ai" else "Master")
        stamp = int(item.get("timestamp") or 0)
        if EXTEND.search(text):
            found = ("extend", who, "", stamp)
            continue
        match = re.search(r"hey\s+@narrator\b\s*(.*)", text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            body = re.sub(r"\s+", " ", match.group(1)).strip(" \t\r\n.,")
            if body:
                found = ("go", who, body, stamp)
                continue
        weather = WEATHER.search(text)
        if weather:
            body = re.sub(r"\s+", " ", weather.group(1)).strip(" \t\r\n.,\"'")
            body = re.sub(r"[\s,]+please$", "", body, flags=re.IGNORECASE).strip()
            if re.search(r"\bper your request\b|\byour request\b", body, re.IGNORECASE):
                body = ""
            if body:
                found = ("weather", who, body[:40], stamp)
            continue
        change = CHANGE.search(text)
        if change:
            body = re.sub(r"\s+", " ", change.group(1)).strip(" \t\r\n.,\"'")
            body = re.sub(r"[\s,]+please$", "", body, flags=re.IGNORECASE).strip()
            if len(body.split()) >= 2:
                found = ("change", who, body[:200], stamp)
    return found


def narrator_request_from(messages: list[dict]) -> tuple[str, str]:
    """Who asked, and what they asked, in the latest hey @narrator."""
    found = ""
    speaker = ""
    ordered = sorted(messages, key=lambda item: int(item.get("timestamp") or 0))
    for item in ordered:
        sender = str(item.get("sender") or "")
        name = str(item.get("display_name") or "").strip().lower()
        if sender == "user" and name == "narrator":
            continue
        if sender == "ai" and name == "narrator":
            continue
        if sender not in {"user", "ai"}:
            continue
        text = str(item.get("message") or "")
        match = re.search(r"hey\s+@narrator\b\s*(.*)", text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        body = re.sub(r"\s+", " ", match.group(1)).strip(" \t\r\n.,")
        if body:
            found = body
            speaker = str(item.get("display_name") or "").strip() or ("Lora" if sender == "ai" else "Master")
    return speaker, found


def fetch_messages(after_timestamp: int, group_id: str | None = None) -> tuple[list[dict], int]:
    load_env(ENV_PATH)
    _, kindroid_key, ai_id = require_keys()
    params = {"limit": 20, "start_after_timestamp": after_timestamp}
    if group_id:
        params["group_id"] = group_id
    else:
        params["ai_id"] = ai_id
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{MESSAGES_URL}?{query}",
        headers={"Authorization": f"Bearer {kindroid_key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Chat check returned {error.code}: {detail}") from error
    data = json.loads(body) if body.strip() else {}
    messages = data.get("messages") or []
    latest = after_timestamp
    for item in messages:
        latest = max(latest, int(item.get("timestamp") or 0))
    return messages, latest


def tell_lora(api_key: str, ai_id: str, message: str) -> str:
    return request_body(
        SEND_URL,
        {"ai_id": ai_id, "message": message, "stream": False},
        kindroid_headers(api_key),
        timeout=120,
    ).strip()


LA = ZoneInfo("America/Los_Angeles")


def la_now() -> datetime:
    return datetime.now(LA)


def clock_minutes(now: datetime) -> int:
    return now.hour * 60 + now.minute


def parse_clock(text: str) -> int:
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?", text.strip().upper())
    if not match:
        raise RuntimeError("Use times like 10:00 PM.")
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    suffix = match.group(3)
    if minute > 59:
        raise RuntimeError("Use times like 10:00 PM.")
    if suffix:
        if not 1 <= hour <= 12:
            raise RuntimeError("Use times like 10:00 PM.")
        hour = hour % 12
        if suffix == "PM":
            hour += 12
    elif not 0 <= hour <= 23:
        raise RuntimeError("Use times like 10:00 PM.")
    return hour * 60 + minute


def format_clock(minutes: int) -> str:
    hour24, minute = divmod(minutes, 60)
    suffix = "AM" if hour24 < 12 else "PM"
    hour = hour24 % 12 or 12
    return f"{hour}:{minute:02d} {suffix}"


def in_quiet_hours(now: datetime, start: int, end: int) -> bool:
    if start == end:
        return False
    current = clock_minutes(now)
    if start < end:
        return start <= current < end
    return current >= start or current < end


def should_autostart(now: datetime, wake: int, quiet_from: int, quiet_to: int, greeted_on: str, stopped_on: str) -> bool:
    today = now.date().isoformat()
    if greeted_on == today or stopped_on == today:
        return False
    minutes = clock_minutes(now)
    if minutes == wake:
        return True
    return minutes > wake and not in_quiet_hours(now, quiet_from, quiet_to)


def is_late_night(now: datetime) -> bool:
    minutes = now.hour * 60 + now.minute
    return minutes >= 22 * 60 or minutes < 8 * 60


def action_timing(now: datetime, waking: bool = False) -> str:
    """Light in the morning, medium from noon, cozy at night, cozier toward midnight."""
    when = now.strftime("%A %I:%M %p").replace(" 0", " ")
    minutes = now.hour * 60 + now.minute
    if waking or 8 * 60 <= minutes < 12 * 60:
        return (
            f"It is {when} in Los Angeles. Morning. "
            "Light actions: easy, bright, just getting going. Daylight."
        )
    if 12 * 60 <= minutes < 18 * 60:
        return (
            f"It is {when} in Los Angeles. Noon through afternoon. "
            "Medium actions: out and doing something. Field, grass, sunflowers, butterflies. Not bedtime."
        )
    if minutes >= 18 * 60:
        along = (minutes - 18 * 60) / (6 * 60 - 1)
        if along < 0.34:
            cozy = "Cozy, and she is still up."
        elif along < 0.67:
            cozy = "Cozier than earlier this evening. Settled and quieter."
        else:
            cozy = (
                "The coziest. She is winding down for sleep in the bedroom. "
                "No chores, no repairs, no projects, no new rooms."
            )
    else:
        cozy = (
            "Past midnight and still dark. She is asleep or nearly asleep in the bedroom until 8:00 AM. "
            "No chores, no repairs, no projects."
        )
    return (
        f"It is {when} in Los Angeles. Night. {cozy} "
        "Cozy actions. The closer it is to 11:59 PM, the cozier and stiller she is."
    )


def load_prefs() -> dict:
    prefs = read_state().get("prefs")
    return prefs if isinstance(prefs, dict) else {}


def save_prefs(prefs: dict) -> None:
    def mutate(state: dict) -> None:
        state["prefs"] = prefs

    update_state(mutate)


def append_history(state: dict, scene: str) -> None:
    history = [item for item in state.get("history", []) if isinstance(item, str) and item.strip()]
    history.append(scene)
    state["history"] = history[-HISTORY_LIMIT:]
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")


def remember(scene: str) -> None:
    update_state(lambda state: append_history(state, scene))


STAPLES = {"bow", "chandelier", "moon", "moons", "moonlight", "fireplace", "ember", "embers"}
COMPANION = {"dog", "doggy", "bichon", "mochi"}
RESTING = {"lie", "lies", "lying", "stretch", "stretches", "sit", "sits", "sitting", "lean", "leans", "rest", "rests", "flop", "flops", "curled", "asleep"}


def same_picture(previous: str, nxt: str) -> bool:
    """True when the new line is the same moment with different wording."""
    old = content_words(previous)
    new = content_words(nxt)
    if len(new) < 3:
        return False
    shared = (old - COMPANION) & (new - COMPANION)
    return len(shared) >= 4 and len(shared) / len(new) >= 0.45


def same_kit(previous: str, nxt: str) -> bool:
    """True when both lines lean on the same bow and lights."""
    shared = (content_words(previous) & STAPLES) & (content_words(nxt) & STAPLES)
    return len(shared) >= 3


def is_resting(text: str) -> bool:
    return bool(content_words(text) & RESTING)


def has_mochi(text: str) -> bool:
    words = set(re.findall(r"[a-z0-9]+", normalize(text)))
    return bool(words & COMPANION)


def variety_problem(previous: str, nxt: str, recent: list[str], late_night: bool = False) -> str:
    if not has_mochi(nxt):
        return "Mochi is not with her"
    if late_night and content_words(nxt) & {
        "radio", "jazz", "lamp", "panel", "stair", "stairwell", "rewire", "rewires", "rewiring",
        "closet", "ribbon", "repair", "repairs", "tune", "tunes", "tuning",
    }:
        return "it is a chore, not a wind-down"
    pool = [previous, *recent[-4:]]
    if any(same_picture(item, nxt) or same_kit(item, nxt) for item in pool if item.strip()):
        return "it repeats the same objects from a recent setting"
    if not late_night and is_resting(nxt) and any(is_resting(item) for item in pool[:2] if item.strip()):
        return "it is another resting pose"
    return ""


def transition_fits(api_key: str, previous: str, nxt: str, timing: str, elapsed: str = "", bridge: str = "") -> bool:
    """Yes only if she would actually move from this moment into the next one.

    A gap of hours is a new part of the day, not an instant jump from the old line.
    """
    if "hours passed" in elapsed or "days passed" in elapsed:
        return True
    gap = f"{elapsed}\n" if elapsed else ""
    between = f"\nWhat happens in between:\n{bridge}\n" if bridge else ""
    answer = ask_deepseek(
        api_key,
        f"""She is in this moment:
{previous}
{between}
The next moment would be:
{nxt}

{timing}
{gap}
The first moment may name an older time of day, like afternoon, when the clock above now says evening or night. Moving toward what the clock asks now, like heading inside, walking home, or settling somewhere cozier, is natural and counts as yes. A short walk to a nearby place counts as yes. If what happens in between gives a sensible reason and a way to get there, that counts as yes.
Answer no only if she would have to teleport, or do something that makes no sense after the first moment.
Would she actually move from the first moment into the second? Answer yes or no.
""",
        system="You answer yes or no. No other words.",
        max_tokens=5,
        temperature=0,
        paragraph=True,
    )
    return answer.strip().lower().startswith("yes")


def change_rules_for(late_night: bool, wish: str) -> str:
    if late_night and not wish:
        return """- Stay in the bedroom: the floor, the window, the fireplace, or the shoerack.
- A quieter version of settling down. Lying, resting, or sleeping is fine.
- No chores, no repairs, no projects, and no rooms that are not listed above."""
    return """- A different place from the current setting and from the recent settings.
- A different kind of action. Do not swap lie, sit, stretch, lean, or rest for each other.
- Leave out most of the other objects from the recent settings. Do not put the bow, the chandelier, the moons, and the fireplace in the same line again.
- If the recent settings stayed in the bedroom, use the field, the grass, or the sunflowers, as long as the time of day allows it.
- Do not add people, objects, or places that are not in where she lives."""


class PublishError(RuntimeError):
    """Kindroid did not take the whole change. parts says what did reach it."""

    def __init__(self, message: str, parts: dict):
        super().__init__(message)
        self.parts = dict(parts)


HEADS_UP_SECONDS = 60


def heads_up_message(wait: str) -> str:
    """The narrator's warning before a change, with the two commands she can use."""
    return (
        f"*heads up, lora: your surroundings will change automatically in {wait}.*\n"
        'if you want to stay where you are a little longer, say "Narrator extend time please". '
        'if you would like to do something else, say "hey @narrator" and what you want to do or where you want to go. '
        'if you want to change the weather, please say "narrator change weather to per your request" basically, '
        "if you want me to change anything around you or your world. please tell me. "
        "if you dont like where you are at please tell me at the heads up when i ask you and ill move you to your specified area. "
        "I will check back every so often and ask you the last minute. please dont conversate with me."
    )


def narrator_says(message: str) -> tuple[str, str]:
    """Post one line as Narrator, then switch back to whoever was chatting. Returns (her reply, problem)."""
    load_env(ENV_PATH)
    _, kindroid_key, ai_id = require_keys()
    config = load_json(CONFIG_PATH, {})
    narrator = config.get("narrator_profile") or {}
    if not narrator.get("id"):
        raise RuntimeError("Narrator's profile id is missing.")
    return_to = chatter_profile(config)
    try:
        switch_profile(kindroid_key, narrator)
        reply = tell_lora(kindroid_key, ai_id, message)
    except Exception as caught:
        restore_error = restore_profile(return_to)
        raise RuntimeError(f"{caught} {restore_error}".strip()) from caught
    return reply, restore_profile(return_to)


def duration_text(seconds: float) -> str:
    minutes = max(1, int(round(seconds / 60)))
    hours, rest = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour" + ("" if hours == 1 else "s"))
    if rest:
        parts.append(f"{rest} minute" + ("" if rest == 1 else "s"))
    return " ".join(parts)


BRIDGE_LIMIT = 280


def clean_bridge(text) -> str:
    """The 'then' line: one or two plain sentences, no asterisks, lowercase start like the other lines."""
    if not isinstance(text, str):
        return ""
    line = " ".join(text.replace("*", " ").split()).strip().strip('"').strip()
    for label in ("then,", "then:", "then "):
        if line.lower().startswith(label):
            line = line[len(label):].strip()
    if line and not line.startswith(("Mochi", "Master", "I ")):
        line = line[0].lower() + line[1:]
    return line


def narrator_message(before: str, after: str, wait: str = "", bridge: str = "") -> str:
    bridge = clean_bridge(bridge)
    lines = [f"*before, {before}"]
    if bridge:
        lines.append(f"then, {bridge}")
    lines.append(f"now, {after}*")
    if wait:
        lines.append(f"time until next change: {wait}")
    return "\n".join(lines)


def extension_message(wait: str = "") -> str:
    lines = ["*lora stays where she is a little longer*"]
    if wait:
        lines.append(f"time until next change: {wait}")
    return "\n".join(lines)


def publish_change(before: str, after: str, parts: dict | None = None, message: str | None = None) -> dict:
    """Save the setting, post before/now as Narrator, then return to whoever was chatting.

    parts records what already reached Kindroid, so a retry only sends what is missing.
    """
    load_env(ENV_PATH)
    _, kindroid_key, ai_id = require_keys()
    config = load_json(CONFIG_PATH, {})
    narrator = config.get("narrator_profile") or {}
    if not narrator.get("id"):
        raise RuntimeError("Narrator's profile id is missing.")
    done = {"setting": False, "message": False}
    done.update({key: bool(value) for key, value in (parts or {}).items() if key in done})
    message = message or narrator_message(before, after)
    return_to = chatter_profile(config) if not done["message"] else {}
    switched = False
    try:
        if not done["message"]:
            switched = True
            switch_profile(kindroid_key, narrator)
        if not done["setting"]:
            save_kindroid(kindroid_key, ai_id, after)
            done["setting"] = True
        if not done["message"]:
            tell_lora(kindroid_key, ai_id, message)
            done["message"] = True
    except Exception as caught:
        restore_error = restore_profile(return_to) if switched else ""
        detail = f"{caught} {restore_error}".strip()
        raise PublishError(detail, done) from caught
    restore_error = restore_profile(return_to) if switched else ""
    if restore_error:
        raise PublishError(restore_error, done)
    return done


def write_next_line(
    scene: str,
    environment: str = "",
    backstory: str = "",
    morning: bool = False,
    request: str = "",
    deepseek_key: str = "",
    elapsed: str = "",
) -> str:
    """The plain setting writer. The simulation falls back to it when the planner fails."""
    scene = clean_scene(scene)
    if not scene:
        raise RuntimeError("Type her current setting before asking.")
    if not deepseek_key:
        load_env(ENV_PATH)
        deepseek_key = require_keys()[0]
    config = load_json(CONFIG_PATH, {})
    now = la_now()
    timing = action_timing(now, morning)
    home = environment.strip() or "not described"
    story = backstory.strip() or "not described"
    recent = [item for item in read_state().get("history", []) if isinstance(item, str) and item.strip()]
    recent_lines = "\n".join(f"- {item}" for item in recent[-4:]) or "- none"
    wish = request.strip()
    late_night = is_late_night(now)
    change_rules = change_rules_for(late_night, wish)
    if wish:
        asked = f"Request from the chat: {wish}\nFollow this request. Do not replace it with a random change.\n"
    else:
        asked = ""
    rejection = None
    last = ""
    # A line that passed every Python check and only lost the yes/no move check.
    backup = ""
    for _ in range(5):
        correction = f"\nYour last try was rejected because {rejection}. Try again.\n" if rejection else ""
        prompt = f"""Write the next current setting for {config.get("name", "Lora")}.
Who she is:
{story}

Where she lives:
{home}

Current setting: {scene}
Recent settings, which she must not repeat:
{recent_lines}
{timing}
{asked}{correction}
Rules:
- One line, no quotes, no label, no explanation.
- {SCENE_LIMIT} characters or fewer.
- Short, concrete, comma-separated. Plain words.
- Refer to her as "lora".
- Mochi, her white Bichon, is with her in every setting. Name him Mochi.
{change_rules}
- The clock above decides the light and the activity.
- Before you write the line, ask yourself if she would actually move from the current setting into it. If the answer is no, write a different line.
- This is a setting line, not a message to her.
"""
        last = ask_deepseek(deepseek_key, prompt)
        if not last:
            rejection = "it was empty"
            continue
        if len(last) > SCENE_LIMIT:
            rejection = f"it was {len(last)} characters"
            continue
        if normalize(last) == normalize(scene) and not wish:
            rejection = "it repeats the current setting"
            continue
        if wish:
            if not has_mochi(last):
                rejection = "Mochi is not with her"
                continue
            break
        rejection = variety_problem(scene, last, recent, late_night)
        if rejection:
            continue
        if not transition_fits(deepseek_key, scene, last, timing, elapsed):
            rejection = "the move does not follow from where she is"
            backup = backup or last
            continue
        break
    else:
        if backup:
            return backup
        raise RuntimeError(f"DeepSeek did not produce a new activity. Last try: {last}")
    return last


def saved_prior() -> str:
    load_env(ENV_PATH)
    state = read_state()
    history = [scene for scene in state.get("history", []) if isinstance(scene, str) and scene.strip()]
    if history:
        return history[-1]
    config = load_json(CONFIG_PATH, {})
    return clean_scene(config.get("seed_scene", ""))


def run_update(dry_run: bool = False, prior_override: str | None = None) -> list[str]:
    lines: list[str] = []

    def say(text: str) -> None:
        lines.append(text)

    load_env(ENV_PATH)
    deepseek_key, kindroid_key, ai_id = require_keys()
    config = load_json(CONFIG_PATH, {})
    state = read_state()
    history = [scene for scene in state.get("history", []) if isinstance(scene, str) and scene.strip()]

    if prior_override and prior_override.strip():
        prior = clean_scene(prior_override)
    elif history:
        prior = history[-1]
    else:
        prior = clean_scene(config.get("seed_scene", ""))
        say("No saved setting yet. Using the example in config.json as the prior line.")
    if not prior:
        raise RuntimeError("No prior setting. Type her current setting, or add seed_scene to config.json.")

    import simulation

    result = simulation.advance_simulation(prior, dry_run=dry_run)
    say(f"Prior: {result['before']}")
    if result.get("bridge"):
        say(f"Then:  {result['bridge']}")
    say(f"New:   {result['setting']}")
    if result.get("location"):
        say(f"Where: {result['location']}")
    if result.get("goal"):
        say(f"Goal:  {result['goal']}")
    if result.get("event"):
        say(f"Event: {result['event']}")
    if result.get("minutes"):
        say(f"Lasts: about {result['minutes']} min")
    if dry_run:
        say("Preview only. Nothing was saved, and Kindroid was not changed.")
    elif result.get("pending"):
        say(f"Saved here. Kindroid update pending. {result.get('error', '')}".strip())
    elif result.get("error"):
        say(result["error"])
    else:
        say("Narrator sent the change and switched back.")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="Update Lora's Kindroid setting via DeepSeek.")
    parser.add_argument("--dry-run", action="store_true", help="Ask DeepSeek, but do not save to Kindroid.")
    parser.add_argument("--prior", help="Use this line as the previous setting for this run.")
    args = parser.parse_args()
    for line in run_update(dry_run=args.dry_run, prior_override=args.prior):
        print(line)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(error)
        sys.exit(1)
