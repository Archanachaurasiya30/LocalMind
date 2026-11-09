"""Compare synthetic tasks through direct Ollama and LocalMind's real chat route.

Prepare never contacts a service. Run requires --execute, and paid cloud arms
also require --allow-cloud. Cloud accounts, consent and limits remain managed
by LocalMind. Reports belong in an ignored artifacts directory. Human review is
separate from execution; no mechanical score claims product-quality approval.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import statistics
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SUITE = ROOT / "evaluation" / "experience-cases.json"
SCORE_FIELDS = ("correctness", "relevance", "repetition")
MAX_LINE = 262144
MAX_OUTPUT = 2097152


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Evaluation services must not redirect requests")


def load_suite(path=DEFAULT_SUITE):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or len(data.get("cases", [])) != 60:
        raise ValueError("Expected the frozen version 1 suite of 60 distinct tasks")
    cases = data["cases"]
    seen, prompts = set(), set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or case["id"] in seen:
            raise ValueError("Cases require unique IDs")
        seen.add(case["id"])
        if case.get("split") not in ("development", "held_out"):
            raise ValueError("Each case needs an explicit split")
        for field in ("category", "required", "forbidden"):
            if not isinstance(case.get(field), str) or not case[field].strip():
                raise ValueError("Every case needs a category and human-review rubric")
        turns = case.get("messages")
        if not isinstance(turns, list) or not turns or len(turns) % 2 != 1:
            raise ValueError("A task must end on a user turn")
        for index, turn in enumerate(turns):
            if (not isinstance(turn, dict) or turn.get("role") != ("user" if index % 2 == 0 else "assistant")
                    or not isinstance(turn.get("content"), str) or not turn["content"].strip()):
                raise ValueError("Task messages must alternate user and assistant")
        fingerprint = json.dumps(turns, sort_keys=True)
        if fingerprint in prompts:
            raise ValueError("Duplicate task text does not increase task coverage")
        prompts.add(fingerprint)
    if sum(len(case["messages"]) > 1 for case in cases) < 20:
        raise ValueError("At least 20 tasks must exercise a follow-up")
    if min(Counter(case["split"] for case in cases).get(split, 0)
           for split in ("development", "held_out")) < 10:
        raise ValueError("Keep at least 10 untouched held-out tasks and 10 development tasks")
    return cases


def parse_arm(text):
    parts = text.split(":", 2)
    if len(parts) != 3 or not parts[2].strip():
        raise ValueError("Use app:provider:model or direct:ollama:model")
    mode, provider, model = parts
    if mode not in ("app", "direct") or provider not in ("ollama", "openai", "anthropic", "gemini"):
        raise ValueError("Unsupported comparison arm")
    if mode == "direct" and provider != "ollama":
        raise ValueError("Cloud calls must pass through LocalMind's consent and budget controls")
    return {"mode": mode, "provider": provider, "model": model, "id": text}


def local_service_url(value):
    url = urlsplit(value)
    if (url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost", "::1")
            or url.username or url.password or url.query or url.fragment or url.path not in ("", "/")):
        raise ValueError("Use a loopback-only HTTP service URL without credentials or paths")
    if url.port is None:
        raise ValueError("Include the LocalMind service port")
    return value.rstrip("/")


def system_policy():
    service = str(ROOT / "services" / "ai")
    if service not in sys.path:
        sys.path.insert(0, service)
    from app.chat_policy import answer_policy
    return answer_policy(datetime.now(timezone.utc).date().isoformat(), searched=False, web_allowed=False)


def verify_local_model(model, opener=None):
    """A loopback Ollama URL can still proxy a hosted model; reject that here."""
    opener = opener or build_opener(ProxyHandler({}), NoRedirect())
    def request(path, body=None):
        req = Request("http://127.0.0.1:11434" + path,
                      None if body is None else json.dumps(body).encode(),
                      {"Content-Type": "application/json"})
        with opener.open(req, timeout=15) as response:
            raw = response.read(MAX_OUTPUT + 1)
        if len(raw) > MAX_OUTPUT:
            raise ValueError("Model metadata exceeded the limit")
        return json.loads(raw)
    aliases = {model, model + ":latest"}
    installed = next((item for item in request("/api/tags").get("models", [])
                      if item.get("name") in aliases), None)
    metadata = request("/api/show", {"model": model})
    if (not installed or not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", installed.get("digest", ""))
            or any(item.get(field) for item in (installed, metadata) for field in ("remote_model", "remote_host"))):
        raise ValueError("Ollama comparison requires an already installed local model, not a cloud alias")
    if "completion" not in metadata.get("capabilities", []):
        raise ValueError("The selected local model cannot generate chat responses")
    return {"digest": installed["digest"], "details": metadata.get("details"),
            "parameters": metadata.get("parameters"), "capabilities": metadata.get("capabilities")}


def execute_one(case, arm, service, token, *, opener=None, clock=time.monotonic):
    """No automatic replay after any request may have been accepted."""
    opener = opener or build_opener(ProxyHandler({}), NoRedirect())
    direct = arm["mode"] == "direct"
    headers = {"Content-Type": "application/json"}
    if direct:
        url = "http://127.0.0.1:11434/api/chat"
        payload = {"model": arm["model"], "stream": True,
                   "messages": [{"role": "system", "content": system_policy()}, *case["messages"]],
                   "options": {"temperature": 0.2, "num_ctx": 16384, "num_predict": 2048}}
    else:
        if not token:
            raise ValueError("Set LOCALMIND_EVAL_TOKEN for the isolated LocalMind test service")
        url = local_service_url(service) + "/chat/stream"
        headers["Authorization"] = "Bearer " + token
        payload = {"provider": arm["provider"], "model": arm["model"],
                   "message": case["messages"][-1]["content"], "history": case["messages"][:-1],
                   "memory_enabled": False, "knowledge_enabled": False,
                   "connector_context_enabled": False, "web_search_enabled": False}
    result = {"case_id": case["id"], "arm": arm["id"], "status": "error", "answer": "",
              "first_text_seconds": None, "total_seconds": None, "usage": None,
              "estimated_cost_usd": 0 if arm["provider"] == "ollama" else None,
              "renderer_review": "not_run", "error": None}
    started = clock()
    consumed = 0
    try:
        request = Request(url, json.dumps(payload).encode(), headers, method="POST")
        with opener.open(request, timeout=240) as response:
            while True:
                line = response.readline(MAX_LINE + 1)
                if not line:
                    break
                consumed += len(line)
                if len(line) > MAX_LINE or consumed > MAX_OUTPUT:
                    raise ValueError("Response exceeded the evaluation output limit")
                line = line.strip()
                if not line:
                    continue
                if not direct:
                    if not line.startswith(b"data:"):
                        continue
                    line = line[5:].strip()
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("Invalid stream event")
                if event.get("error") or event.get("type") == "error":
                    result["error"] = "provider_or_service_error"
                    break
                content = (event.get("message", {}).get("content", "") if direct
                           else event.get("content", "") if event.get("type") == "token" else "")
                if not isinstance(content, str):
                    raise ValueError("Invalid stream text")
                if content:
                    if result["first_text_seconds"] is None:
                        result["first_text_seconds"] = clock() - started
                    result["answer"] += content
                if event.get("type") == "usage":
                    result["usage"] = event
                    cost = event.get("cost_usd")
                    if type(cost) in (int, float) and cost >= 0:
                        result["estimated_cost_usd"] = cost
                if event.get("type") == "diagnostics":
                    result["diagnostics"] = event.get("data")
                if (direct and event.get("done") is True) or (not direct and event.get("type") == "done"):
                    result["status"] = "completed" if result["answer"].strip() else "empty"
                    if direct:
                        result["usage"] = {key: event.get(key) for key in (
                            "prompt_eval_count", "eval_count", "load_duration", "prompt_eval_duration", "eval_duration")}
                    break
        if result["status"] == "error" and not result["error"]:
            result["error"] = "missing_completion_receipt"
    except KeyboardInterrupt:
        result["status"], result["error"] = "cancelled", "operator_cancelled"
    except HTTPError as exc:
        result["error"] = f"http_{exc.code}"
    except (OSError, ValueError, TypeError, AttributeError):
        result["error"] = "transport_or_stream_error"
    result["total_seconds"] = clock() - started
    result["word_count"] = len(result["answer"].split())
    return result


def summarize_runs(results):
    summary = {}
    for arm in sorted({row["arm"] for row in results}):
        rows = [row for row in results if row["arm"] == arm]
        completed = [row for row in rows if row["status"] == "completed"]
        first = [row["first_text_seconds"] for row in completed if row["first_text_seconds"] is not None]
        costs = [row["estimated_cost_usd"] for row in rows if row["estimated_cost_usd"] is not None]
        summary[arm] = {
            "status_counts": dict(Counter(row["status"] for row in rows)),
            "median_first_text_seconds": statistics.median(first) if first else None,
            "median_completed_seconds": statistics.median(row["total_seconds"] for row in completed) if completed else None,
            "known_estimated_cost_usd": sum(costs), "requests_with_unknown_cost": len(rows) - len(costs),
            "human_quality_review": "pending",
        }
    return summary


def blind_review(cases, results, seed):
    """Random labels per task; mirror a sample to audit position consistency."""
    rng = random.Random(seed)
    cards, keys = [], []
    for case in cases:
        rows = [row for row in results if row["case_id"] == case["id"]]
        if len(rows) < 2:
            continue
        rng.shuffle(rows)
        card = {"review_id": f"review-{len(cards) + 1:03d}", "messages": case["messages"],
                "rubric": {"required": case["required"], "forbidden": case["forbidden"]},
                "answers": {}, "preferred": None, "notes": "", "renderer_issue": None}
        key = {"review_id": card["review_id"], "case_id": case["id"], "split": case["split"],
               "labels": {}, "mirror_of": None}
        for index, row in enumerate(rows):
            label = chr(ord("A") + index)
            card["answers"][label] = {"text": row["answer"], "status": row["status"],
                                      "scores": dict.fromkeys(SCORE_FIELDS)}
            key["labels"][label] = row["arm"]
        cards.append(card)
        keys.append(key)
    eligible = list(range(len(cards)))
    rng.shuffle(eligible)
    for index in eligible[:min(10, len(cards))]:
        original = cards[index]
        labels = list(original["answers"])
        mirrored = json.loads(json.dumps(original))
        mirrored["review_id"] = f"review-{len(cards) + 1:03d}"
        mirrored["answers"] = dict(zip(labels, reversed(list(mirrored["answers"].values()))))
        keys.append({**keys[index], "review_id": mirrored["review_id"],
                     "labels": dict(zip(labels, reversed(list(keys[index]["labels"].values())))),
                     "mirror_of": original["review_id"]})
        cards.append(mirrored)
    rng.shuffle(cards)
    return {"schema_version": 1,
            "instructions": "Score each field 0–4 (higher is better; repetition 4 means no needless repetition). Prefer a label or tie. Judge correctness and usefulness rather than length. Do not open the key until review is finished. Text may reveal a model identity, so this is label-blinded, not guaranteed fully blinded. Renderer review is separate and remains untested by this service harness.",
            "cards": cards}, {"schema_version": 1, "seed": seed, "keys": keys}


def score_review(review, key, *, allow_partial=False):
    keys = {row["review_id"]: row for row in key["keys"]}
    if len(keys) != len(key["keys"]):
        raise ValueError("Duplicate key IDs")
    seen, votes, scores, choices = set(), Counter(), {}, {}
    length_wins = Counter()
    for card in review["cards"]:
        identity = card["review_id"]
        if identity not in keys or identity in seen:
            raise ValueError("Unknown or duplicate review ID")
        seen.add(identity)
        entry = keys[identity]
        if set(card["answers"]) != set(entry["labels"]):
            raise ValueError("Review labels changed")
        preferred = card.get("preferred")
        if preferred is None and allow_partial:
            continue
        if preferred not in {*entry["labels"], "tie"}:
            raise ValueError("Each reviewed task needs a preferred label or tie")
        for label, answer in card["answers"].items():
            values = answer.get("scores", {})
            if set(values) != set(SCORE_FIELDS) or any(type(v) is not int or not 0 <= v <= 4 for v in values.values()):
                raise ValueError("Every score must be an integer from 0 to 4")
            if not entry["mirror_of"]:
                arm = entry["labels"][label]
                for metric, value in values.items():
                    scores.setdefault(arm, {}).setdefault(metric, []).append(value)
        choice = "tie" if preferred == "tie" else entry["labels"][preferred]
        choices[identity] = choice
        if not entry["mirror_of"]:
            votes[choice] += 1
            if preferred != "tie":
                lengths = {label: len(answer["text"].split()) for label, answer in card["answers"].items()}
                if len(set(lengths.values())) > 1:
                    length_wins["longest_preferred" if lengths[preferred] == max(lengths.values()) else "other_preferred"] += 1
    if not allow_partial and seen != set(keys):
        raise ValueError("Review is missing tasks")
    mirrors = [row for row in key["keys"] if row["mirror_of"] and row["review_id"] in choices
               and row["mirror_of"] in choices]
    return {"reviewed_cards": len(choices), "preference_counts": dict(votes),
            "scores": {arm: {metric: statistics.mean(values) for metric, values in metrics.items()}
                       for arm, metrics in scores.items()},
            "position_audit": {"paired_cards": len(mirrors), "consistent_choices": sum(
                choices[row["review_id"]] == choices[row["mirror_of"]] for row in mirrors)},
            "length_audit": dict(length_wins),
            "caveat": "Length association is not proof of bias. Inspect disagreements and correctness before changing defaults. No automatic release approval."}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run", "score"))
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--arm", action="append", default=[])
    parser.add_argument("--split", choices=("development", "held_out"), default="development")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--service", default="http://127.0.0.1:8765")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-cloud", action="store_true")
    parser.add_argument("--review", type=Path)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "score":
        if args.review is None or args.key is None:
            parser.error("score needs --review and --key")
        result = score_review(json.loads(args.review.read_text(encoding="utf-8")),
                              json.loads(args.key.read_text(encoding="utf-8")), allow_partial=args.allow_partial)
        write_json(args.out / "review-summary.json", result)
        return
    all_cases = load_suite(args.suite)
    cases = [case for case in all_cases if case["split"] == args.split]
    arms = [parse_arm(value) for value in args.arm]
    if len({arm["id"] for arm in arms}) != len(arms):
        parser.error("Choose distinct comparison arms")
    manifest = {"suite_sha256": hashlib.sha256(args.suite.read_bytes()).hexdigest(),
                "split": args.split, "cases": len(cases), "multiturn": sum(len(c["messages"]) > 1 for c in cases),
                "arms": arms, "seed": args.seed, "human_review": "pending", "results": [],
                "scope": "Synthetic service/API comparison. No personal retrieval, UI renderer, model downloads or training. Fixed prior assistant turns, not generated interactive histories. App and direct request preparation differ; inspect configuration before attributing differences to model quality."}
    if args.command == "prepare":
        write_json(args.out / "manifest.json", manifest)
        return
    if not args.execute or len(arms) < 2:
        parser.error("run needs --execute and at least two --arm selections")
    if any(arm["provider"] != "ollama" for arm in arms) and not args.allow_cloud:
        parser.error("Cloud evaluation can incur charges; pass --allow-cloud only for an approved run")
    local_service_url(args.service)
    if (args.out / "run.json").exists():
        parser.error("Use a new output directory; completed paid requests must not be silently replayed")
    rng = random.Random(args.seed)
    token = os.environ.get("LOCALMIND_EVAL_TOKEN", "")
    if any(arm["mode"] == "app" for arm in arms) and not token:
        parser.error("Set LOCALMIND_EVAL_TOKEN for the isolated test service before running")
    manifest["local_models"] = {model: verify_local_model(model) for model in {
        arm["model"] for arm in arms if arm["provider"] == "ollama"}}
    for case in cases:
        ordered = list(arms)
        rng.shuffle(ordered)
        for arm in ordered:
            row = execute_one(case, arm, args.service, token)
            manifest["results"].append(row)
            manifest["execution_summary"] = summarize_runs(manifest["results"])
            write_json(args.out / "run.json", manifest)
            review, key = blind_review(cases, manifest["results"], args.seed)
            write_json(args.out / "blind-review.json", review)
            write_json(args.out / "private-review-key.json", key)
            if row["status"] == "cancelled":
                return


if __name__ == "__main__":
    main()
