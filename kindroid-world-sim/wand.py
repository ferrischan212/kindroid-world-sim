"""Mochi's wand: Kindroid writes a line for Mochi and it is sent to Lora. No browser."""

from __future__ import annotations

import update_scene as us


def talk_minutes(text: str) -> int:
    """How long to wait between wand taps. Kept between 1 and 180 minutes."""
    try:
        value = int(str(text).strip())
    except ValueError as error:
        raise RuntimeError("Talk every needs a number of minutes.") from error
    if value < 1 or value > 180:
        raise RuntimeError("Talk every must be between 1 and 180 minutes.")
    return value


def tap() -> str:
    """Write the wand's line as Mochi and send it. No browser window."""
    us.load_env(us.ENV_PATH)
    _, key, ai_id = us.require_keys()
    us.activate_named_profile("mochi_profile")
    suggestion = us.request_body(
        "https://api.kindroid.ai/v1/suggest-user-message",
        {"ai_id": ai_id, "existing_message": "", "stream": False},
        us.kindroid_headers(key),
        90,
    ).strip()
    if not suggestion:
        raise RuntimeError("The wand did not write a message.")
    us.tell_lora(key, ai_id, suggestion)
    return suggestion
