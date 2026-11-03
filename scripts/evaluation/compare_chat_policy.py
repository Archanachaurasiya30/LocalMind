"""Small, reproducible model/prompt ablation; not a product quality benchmark.

Calls an already-running local Ollama. No model downloads, training, personal
conversations or external services. Save generated reports under artifacts/.
Both arms use identical fixed histories, seeds and generation settings. The
LocalMind arm exercises the real policy and context packer, not the UI, search,
retrieval, action routing or streaming transport.
"""

import argparse
import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
import statistics
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "ai"))
from app.chat_policy import answer_policy  # noqa: E402
from app.context_budget import pack_context  # noqa: E402
from app.response_diagnostics import response_diagnostics  # noqa: E402

CASES = [
    {
        "id": "exact-correction",
        "history": [
            {"role": "user", "content": "Write a friendly 3-sentence invitation to a Saturday board game night. It starts at 6 pm. Ask friends to bring a snack."},
            {"role": "assistant", "content": "Join us for a fun-filled Saturday night at 6 pm! Board games await, and we'll provide snacks to share. Can't wait to see you then!"},
        ],
        "prompt": "Make it more casual. Replace the snacks sentence with: Bring your favorite snack to share.",
    },
    {
        "id": "three-bullets",
        "history": [
            {"role": "user", "content": "I have 45 minutes to prepare a short talk on local AI. Give me a practical 3-step plan, with time for a demo."},
            {"role": "assistant", "content": "1. Outline community AI use cases for 15 minutes. 2. Research for 15 minutes. 3. Prepare slides for 20 minutes. 4. Rehearse for 10 minutes."},
        ],
        "prompt": "By local AI I mean a model running on my laptop. Simplify this to exactly 3 bullets: 15 minutes to outline, 20 to prepare a chat demo, 10 to rehearse. Keep it under 80 words.",
    },
    {
        "id": "invitation",
        "history": [],
        "prompt": "Write a friendly 3-sentence invitation to a Saturday board game night. It starts at 6 pm. Ask friends to bring a snack.",
    },
    {
        "id": "reasoning",
        "history": [],
        "prompt": "A meeting starts at 2:40 pm and lasts 35 minutes. I need a 10-minute break afterward, then a 25-minute call. At what time does the call end? Explain briefly.",
    },
]


def checks(case_id: str, text: str) -> dict[str, bool]:
    """Narrow mechanical checks. Passing these does not prove overall quality."""
    plain = text.replace("**", "").lower()
    if case_id == "exact-correction":
        return {
            "exact_requested_sentence": "Bring your favorite snack to share." in text,
            "removed_host_provides_snacks": not re.search(r"(?:we(?:'ll| will)|i(?:'ll| will)) provide", plain),
            "kept_time": bool(re.search(r"6\s*(?:pm|p\.m\.)", plain)),
        }
    if case_id == "three-bullets":
        bullets = re.findall(r"^\s*(?:[-*•]|\d+[.)])\s+(.+)$", plain, re.M)
        return {
            "exactly_three_bullets": len(bullets) == 3,
            "under_80_words": len(text.split()) < 80,
            "requested_times_in_order": len(bullets) == 3 and all(
                re.search(rf"\b{minutes}\b", bullet) for minutes, bullet in zip((15, 20, 10), bullets)
            ),
            "corrected_topic": "community" not in plain and bool(re.search(r"laptop|on.device|locally|local model|local ai", plain)),
        }
    if case_id == "invitation":
        return {
            "asks_to_bring_snack": bool(re.search(r"bring\b[^.!?]*\bsnack", plain)),
            "kept_saturday_and_time": "saturday" in plain and bool(re.search(r"6\s*(?:pm|p\.m\.)", plain)),
            "three_sentences": len(re.findall(r"[.!?]+(?:\s|$)", text.strip())) == 3,
        }
    return {"correct_end_time": bool(re.search(r"\b3:50\s*(?:pm|p\.m\.)", plain))}


def load_cases(path):
    if path is None:
        return CASES
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases", [])
    if data.get("schema_version") != 1 or not isinstance(cases, list) or not 1 <= len(cases) <= 200:
        raise ValueError("Expected a version 1 diagnostic suite with 1–200 cases")
    ids = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or case["id"] in ids:
            raise ValueError("Cases need unique string IDs")
        ids.add(case["id"])
        if not isinstance(case.get("prompt"), str) or not case["prompt"].strip():
            raise ValueError("Each case needs a nonempty prompt")
        history = case.get("history")
        if not isinstance(history, list) or any(not isinstance(turn, dict) or turn.get("role") not in ("user", "assistant") or not isinstance(turn.get("content"), str) for turn in history):
            raise ValueError("History must contain user/assistant text turns")
        constraints = case.get("constraints")
        if not isinstance(constraints, dict) or not constraints or set(constraints) - {"contains", "absent", "exact", "max_words", "bullets"}:
            raise ValueError("Use supported mechanical constraints")
        for key, value in constraints.items():
            if key in ("contains", "absent") and (not isinstance(value, list) or not value or any(not isinstance(text, str) or not text for text in value)):
                raise ValueError("Text constraints must contain nonempty strings")
            if key == "exact" and (not isinstance(value, str) or not value):
                raise ValueError("Exact output must be nonempty text")
            if key in ("max_words", "bullets") and (type(value) is not int or value < 1):
                raise ValueError("Count constraints must be positive integers")
    return cases


def case_checks(case, content):
    if "constraints" not in case:
        return checks(case["id"], content)
    result = {}
    for key, value in case["constraints"].items():
        if key == "contains":
            result[key] = all(text.casefold() in content.casefold() for text in value)
        elif key == "absent":
            result[key] = all(text.casefold() not in content.casefold() for text in value)
        elif key == "exact":
            result[key] = content.strip() == value
        elif key == "max_words":
            result[key] = len(content.split()) <= value
        elif key == "bullets":
            result[key] = len(re.findall(r"^\s*(?:[-*•]|\d+[.)])\s+\S", content, re.M)) == value
    return result


def stream_response(opener, payload):
    started = time.monotonic()
    first = None
    parts = []
    request = Request("http://127.0.0.1:11434/api/chat", json.dumps({**payload, "stream": True}).encode(), {"Content-Type": "application/json"})
    with opener.open(request, timeout=240) as response:
        for raw in response:
            if not raw.strip():
                continue
            chunk = json.loads(raw)
            if not isinstance(chunk, dict) or not isinstance(chunk.get("message", {}), dict):
                raise ValueError("Malformed Ollama stream event")
            if chunk.get("error"):
                raise ValueError("Ollama rejected this generation")
            text = chunk.get("message", {}).get("content", "")
            if not isinstance(text, str):
                raise ValueError("Non-text model response")
            if text:
                parts.append(text)
                if first is None:
                    first = time.monotonic() - started
            if chunk.get("done"):
                return "".join(parts), chunk, time.monotonic() - started, first
    raise ValueError("Ollama stream ended without a completion receipt")


def summarize(results):
    summary = {}
    for arm in ("minimal", "localmind"):
        items = [item for item in results if item["arm"] == arm]
        timings = [item["seconds"] for item in items if item["status"] == "completed"]
        summary[arm] = {"mechanical_passes": sum(item["status"] == "completed" and all(item["checks"].values()) for item in items),
                        "total": len(items), "errors": sum(item["status"] == "error" for item in items),
                        "median_seconds": statistics.median(timings) if timings else None,
                        "human_review": "pending", "default_change_authorized": False}
    return summary


def validate_local_model(metadata, installed):
    if not installed or not isinstance(installed.get("digest"), str) or not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", installed["digest"]):
        raise ValueError("Model must already be installed with a recorded fingerprint")
    if any(value.get(key) for value in (metadata, installed) for key in ("remote_model", "remote_host")):
        raise ValueError("This comparison supports local models only")
    if "completion" not in metadata.get("capabilities", []):
        raise ValueError("Selected model does not support text completion")
    capacities = [value for key, value in metadata.get("model_info", {}).items()
                  if key.endswith(".context_length") and type(value) is int and value > 0]
    if not capacities:
        raise ValueError("Model context capacity is unknown")
    return min(16384, max(capacities))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen2.5:1.5b")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=[0, 1])
    parser.add_argument("--suite", type=Path, help="Optional synthetic diagnostic cases; lexical checks are not quality approval")
    args = parser.parse_args()
    cases = load_cases(args.suite)
    opener = build_opener(ProxyHandler({}))

    def request(path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        req = Request("http://127.0.0.1:11434" + path, body, {"Content-Type": "application/json"})
        with opener.open(req, timeout=240) as response:
            return json.load(response)

    metadata = request("/api/show", {"model": args.model})
    aliases = {args.model, args.model + ":latest"} if ":" not in args.model.rsplit("/", 1)[-1] else {args.model}
    installed = next((model for model in request("/api/tags")["models"]
                      if model.get("name") in aliases), {})
    context_window = validate_local_model(metadata, installed)
    today = datetime.now(timezone.utc).date().isoformat()
    report = {
        "scope": "Direct Ollama prompt ablation. Not an end-to-end app or retrieval test.",
        "suite_sha256": hashlib.sha256(json.dumps(cases, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        "cases": cases,
        "model": args.model, "model_details": metadata.get("details"),
        "model_digest": installed.get("digest"), "ollama_version": request("/api/version"),
        "model_template": metadata.get("template"), "model_system": metadata.get("system"),
        "model_parameters": metadata.get("parameters"), "date": today,
        "options": {"temperature": 0.2, "num_ctx": context_window, "num_predict": 2048, "top_p": 0.9, "top_k": 40},
        "results": [],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    for case in cases:
        for seed in args.seeds:
            for arm in ("minimal", "localmind"):
                policy = "You are a helpful assistant. Follow the user's latest instructions." if arm == "minimal" else answer_policy(today, searched=False, web_allowed=False)
                packed = pack_context(policy, case["prompt"], case["history"], [], budget=context_window)
                started = time.monotonic()
                result = {"case": case["id"], "seed": seed, "arm": arm, "messages": packed.messages, "packing": packed.report}
                try:
                    content, terminal, elapsed, first = stream_response(opener, {"model": args.model, "messages": packed.messages, "options": {**report["options"], "seed": seed}})
                    metrics = response_diagnostics(args.model, None, packed.report, elapsed=elapsed, first_token=first, terminal=terminal)
                    passed = case_checks(case, content)
                    passed["nonempty_complete_response"] = bool(content.strip()) and terminal.get("done_reason") != "length"
                    result.update(content=content, checks=passed, status="completed", seconds=round(elapsed, 3), diagnostics=metrics)
                except (OSError, ValueError) as exc:
                    result.update(content="", checks={"request_completed": False}, status="error", error=type(exc).__name__, seconds=round(time.monotonic() - started, 3))
                report["results"].append(result)
                report["summary"] = summarize(report["results"])
                args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(json.dumps({key: result[key] for key in ("case", "seed", "arm", "checks", "seconds")}), flush=True)

    # A case passes only if all its narrow checks pass. Keep full responses for
    # human review; do not present this tiny diagnostic as a benchmark score.
    report["summary"] = summarize(report["results"])
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"]), flush=True)
    if any(item["status"] == "error" for item in report["results"]):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
