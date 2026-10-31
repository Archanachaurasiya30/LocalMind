"""Small, reproducible model/prompt ablation; not a product quality benchmark.

Calls an already-running local Ollama. No model downloads, training, personal
conversations or external services. Save generated reports under artifacts/.
Both arms use identical fixed histories, seeds and generation settings. The
LocalMind arm exercises the real policy and context packer, not the UI, search,
retrieval, action routing or streaming transport.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "ai"))
from app.chat_policy import answer_policy  # noqa: E402
from app.context_budget import pack_context  # noqa: E402

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen2.5:1.5b")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=[0, 1])
    args = parser.parse_args()
    opener = build_opener(ProxyHandler({}))

    def request(path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        req = Request("http://127.0.0.1:11434" + path, body, {"Content-Type": "application/json"})
        with opener.open(req, timeout=240) as response:
            return json.load(response)

    metadata = request("/api/show", {"model": args.model})
    installed = next((model for model in request("/api/tags")["models"]
                      if model.get("name") == args.model), {})
    today = datetime.now(timezone.utc).date().isoformat()
    report = {
        "scope": "Direct Ollama prompt ablation. Not an end-to-end app or retrieval test.",
        "model": args.model, "model_details": metadata.get("details"),
        "model_digest": installed.get("digest"), "ollama_version": request("/api/version"),
        "model_template": metadata.get("template"), "model_system": metadata.get("system"),
        "model_parameters": metadata.get("parameters"), "date": today,
        "options": {"temperature": 0.2, "num_ctx": 16384, "num_predict": 2048, "top_p": 0.9, "top_k": 40},
        "results": [],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        for seed in args.seeds:
            for arm in ("minimal", "localmind"):
                policy = "You are a helpful assistant. Follow the user's latest instructions." if arm == "minimal" else answer_policy(today, searched=False, web_allowed=False)
                packed = pack_context(policy, case["prompt"], case["history"], [])
                started = time.monotonic()
                response = request("/api/chat", {"model": args.model, "messages": packed.messages, "stream": False, "options": {**report["options"], "seed": seed}})
                content = response.get("message", {}).get("content", "")
                result = {"case": case["id"], "seed": seed, "arm": arm, "messages": packed.messages, "packing": packed.report, "content": content, "checks": checks(case["id"], content), "seconds": round(time.monotonic() - started, 2), "done_reason": response.get("done_reason"), "eval_count": response.get("eval_count")}
                report["results"].append(result)
                args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(json.dumps({key: result[key] for key in ("case", "seed", "arm", "checks", "seconds")}), flush=True)

    # A case passes only if all its narrow checks pass. Keep full responses for
    # human review; do not present this tiny diagnostic as a benchmark score.
    report["summary"] = {
        arm: {"passed": sum(all(item["checks"].values()) for item in report["results"] if item["arm"] == arm),
              "total": sum(item["arm"] == arm for item in report["results"])}
        for arm in ("minimal", "localmind")
    }
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"]), flush=True)


if __name__ == "__main__":
    main()
