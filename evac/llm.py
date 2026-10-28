"""Thin client for an OpenAI-compatible chat endpoint (Meta Model API / Muse Spark by default).

Env (read from the shell or from .env at the repo root):
  LLAMA_API_KEY   - Meta Model API key (MODEL_API_KEY, the name Meta's docs use, also works)
  LLM_BASE_URL    - override base URL (default: Meta Model API)
  LLM_MODEL       - override model id (default: muse-spark-1.3)
  LLM_REASONING_EFFORT - minimal|low|medium|high|xhigh (default: minimal)
"""
import base64
import json
import os

import requests


def _load_dotenv(path=os.path.join(os.path.dirname(__file__), "..", ".env")):
    """Minimal KEY=VALUE loader; values already set in the shell win."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


_load_dotenv()

BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.meta.ai/v1")
MODEL = os.environ.get("LLM_MODEL", "muse-spark-1.3")
# Muse Spark reasons before answering and those tokens count toward the limit. At its default
# effort it spent 1,500-2,000 reasoning tokens (5-10 s) on a ~110-token extraction; "minimal"
# gave the same answers in 1-1.6 s. The limit still leaves room if effort is raised.
REASONING_EFFORT = os.environ.get("LLM_REASONING_EFFORT", "minimal")
DEFAULT_MAX_TOKENS = 4000


def _key():
    return os.environ.get("LLAMA_API_KEY") or os.environ.get("MODEL_API_KEY")


def available():
    return bool(_key())


def chat(messages, json_mode=False, max_tokens=DEFAULT_MAX_TOKENS):
    body = {"model": MODEL, "messages": messages, "max_completion_tokens": max_tokens,
            "temperature": 0, "reasoning_effort": REASONING_EFFORT}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = requests.post(
        BASE_URL.rstrip("/") + "/chat/completions",
        headers={"Authorization": "Bearer " + _key()},
        json=body,
        timeout=120,
    )
    r.raise_for_status()
    choice = r.json()["choices"][0]
    content = choice["message"].get("content")
    if isinstance(content, list):  # some replies come as content parts
        content = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    if not content:
        raise RuntimeError("empty reply (finish_reason=%s); raise max_tokens" % choice.get("finish_reason"))
    return content


def chat_tools(messages, tools, max_tokens=DEFAULT_MAX_TOKENS):
    """One turn with function calling. Returns the assistant message dict: either "tool_calls" to run, or the
    final "content"."""
    r = requests.post(BASE_URL.rstrip("/") + "/chat/completions", headers={"Authorization": "Bearer " + _key()},
                      json={"model": MODEL, "messages": messages, "tools": tools, "max_completion_tokens": max_tokens,
                            "temperature": 0, "reasoning_effort": REASONING_EFFORT}, timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]


def chat_json(messages):
    text = chat(messages, json_mode=True)
    # Some models wrap JSON in fences even in json mode; take the outermost object.
    out = json.loads(text[text.find("{"): text.rfind("}") + 1])
    if not isinstance(out, dict):
        raise ValueError("expected a JSON object, got %s" % type(out).__name__)
    return out


def image_part(png_bytes):
    b64 = base64.b64encode(png_bytes).decode()
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}
