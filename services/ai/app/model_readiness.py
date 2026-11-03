"""Inspect the selected local chat model without running inference or downloading it."""
import re
import httpx
from app.model_profiles import resolve_profile


def quality_notice(model: str) -> str | None:
    # A size tag is a warning signal, never a quality score or hardware guarantee.
    match = re.search(r":(\d+(?:\.\d+)?)b(?:-|$)", model.lower())
    if match and float(match[1]) < 4:
        return "This compact model can miss instructions and corrections. Try a larger installed model for demanding work; compare its answers before relying on it."
    return None


async def readiness(model: str, preset: str) -> dict:
    try:
        profile = await resolve_profile(model, preset)
    except ValueError as exc:
        return {"model": model, "preset": preset, "ready": False,
                "state": "needs_setup", "message": str(exc)}
    except httpx.HTTPError:
        return {"model": model, "preset": preset, "ready": False,
                "state": "offline", "message": "Could not inspect this model. Check Ollama and retry."}
    return {"model": model, "preset": preset, "ready": True, "state": "ready",
            "message": "Selected model is available for local chat.",
            "digest": profile["digest"], "context_window": profile["context_window"],
            "response_tokens": profile["response_tokens"],
            "notice": quality_notice(model), "settings_notice": profile.get("settings_notice")}
