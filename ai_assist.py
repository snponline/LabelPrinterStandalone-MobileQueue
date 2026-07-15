"""Thin REST clients for the 3 AI providers a shop can pick between - phase 1
of a general symptom-lookup helper (free-text only: symptoms/age/gender
typed in by the pharmacist, nothing sourced from or linked to any patient
record in this app's own database - no name, no phone, no document/photo
upload). Reference-only: callers must show the reply next to a disclaimer
that it is not a medical diagnosis.

No SDK dependency (openai/anthropic packages) - just `requests` against each
provider's plain REST API, since all three are simple JSON-in/JSON-out and
pulling in 3 SDKs would bloat the PyInstaller build for very little benefit.

Each call_*() takes (api_key, prompt) and returns (success: bool, text: str)
- text is either the model's reply or a human-readable Thai error message,
never a raw exception/traceback (this goes straight into a UI label)."""
import requests

TIMEOUT_SECONDS = 60


def call_openai(api_key, prompt):
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": prompt}], "max_tokens": 1000},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        return False, f"เชื่อมต่อ OpenAI ไม่สำเร็จ: {e}"
    if resp.status_code != 200:
        return False, f"OpenAI ตอบกลับผิดพลาด ({resp.status_code}): {resp.text[:300]}"
    try:
        return True, resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError):
        return False, f"อ่านคำตอบจาก OpenAI ไม่ได้: {resp.text[:300]}"


def call_anthropic(api_key, prompt):
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json",
            },
            json={"model": "claude-sonnet-5", "max_tokens": 1000, "messages": [{"role": "user", "content": prompt}]},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        return False, f"เชื่อมต่อ Claude ไม่สำเร็จ: {e}"
    if resp.status_code != 200:
        return False, f"Claude ตอบกลับผิดพลาด ({resp.status_code}): {resp.text[:300]}"
    try:
        return True, resp.json()["content"][0]["text"]
    except (KeyError, IndexError, ValueError):
        return False, f"อ่านคำตอบจาก Claude ไม่ได้: {resp.text[:300]}"


def call_xai(api_key, prompt):
    # xAI's API is OpenAI-compatible (same request/response shape).
    # Check https://docs.x.ai if this model id changes.
    try:
        resp = requests.post(
            "https://api.x.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "grok-4.5", "messages": [{"role": "user", "content": prompt}], "max_tokens": 1000},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        return False, f"เชื่อมต่อ Grok ไม่สำเร็จ: {e}"
    if resp.status_code != 200:
        return False, f"Grok ตอบกลับผิดพลาด ({resp.status_code}): {resp.text[:300]}"
    try:
        return True, resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError):
        return False, f"อ่านคำตอบจาก Grok ไม่ได้: {resp.text[:300]}"


PROVIDERS = {
    "openai": {"label": "ChatGPT (OpenAI)", "call": call_openai, "key_field": "openai_api_key"},
    "anthropic": {"label": "Claude (Anthropic)", "call": call_anthropic, "key_field": "anthropic_api_key"},
    "xai": {"label": "Grok (xAI)", "call": call_xai, "key_field": "xai_api_key"},
}
