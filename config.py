import os
import requests

import json
import hmac
import hashlib
import logging

BACKEND = 'hf' # 'openai' # 'hf'

# for CloudFlare and HF
WORKER_URL = os.environ.get("WORKER_URL")
SECRET = os.environ.get("SECRET")
HF_MODEL = os.environ.get("MODEL", "HuggingFaceTB/SmolLM3-3B:hf-inference")

# for OpenAI
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
OAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

HEADERS = {
    "Authorization": f"Bearer {OPENAI_KEY}",
    "Content-Type": "application/json"
}

def call_openai_chat(payload):
    body = {
        "model": payload.get("model", OAI_MODEL),
        "messages": payload["messages"],
        "temperature": payload.get("temperature", 0.0),
        "stream": False
    }

    try:
        resp = requests.post(OPENAI_URL, headers=HEADERS, json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        assistant_content = data.get("choices", [])[0].get("message", {}).get("content", "")
        return {"ok": True, "reply": assistant_content, "raw": data}
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": "openai request failed", "detail": str(e)}
    
log = logging.getLogger("agri-config")

def compact_json(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)

def sign_envelope_bytes(envelope_json: str, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), envelope_json.encode("utf-8"), hashlib.sha256)
    return f"sha256={mac.hexdigest()}"

def call_hf_chat(payload: dict, debug: bool = False, timeout: int = 30) -> dict:
    """
    Call the Cloudflare Worker / HF proxy.
    Expects env vars:
      - WORKER_URL
      - WORKER_SHARED_SECRET
    Returns normalized dict with keys similar to existing call_worker return:
      - ok: bool
      - reply: str (when ok)
      - worker_body: parsed JSON or text (when debug or on error)
      - status_code: int
      - error/detail when failure
    """
    worker_url = os.environ.get("WORKER_URL")
    shared_secret = os.environ.get("WORKER_SHARED_SECRET")  # previously called SECRET
    if not worker_url or not shared_secret:
        return {"ok": False, "error": "server configuration missing"}

    envelope_json = compact_json(payload)
    sig = sign_envelope_bytes(envelope_json, shared_secret.strip())
    target = worker_url.rstrip("/") + "/chat"
    headers = {"Content-Type": "application/json", "X-Signature": sig}

    log.info("call_hf_chat -> target=%s", target)
    log.debug("envelope_head=%s", envelope_json[:800])
    log.debug("signature_head=%s", sig[:120])

    try:
        resp = requests.post(target, headers=headers, data=envelope_json.encode("utf-8"), timeout=timeout)
    except Exception as e:
        log.error("Error calling worker: %s", e)
        return {"ok": False, "error": "worker unreachable", "detail": str(e)}

    raw_text = resp.text or ""
    status = resp.status_code

    if status < 200 or status >= 300:
        log.error("Worker returned status %s body_head=%s", status, raw_text[:2000])
        return {
            "ok": False,
            "error": "worker error",
            "status_code": status,
            "worker_body": raw_text[:2000],
            "debug_info": {"envelope_head": envelope_json[:800], "signature_head": sig[:120], "target": target}
        }

    # try parse JSON body
    try:
        body = resp.json()
    except Exception:
        if debug:
            return {
                "ok": True,
                "reply": raw_text,
                "worker_body": raw_text,
                "status_code": status,
                "debug_info": {"envelope_head": envelope_json[:800], "signature_head": sig[:120], "target": target}
            }
        return {"ok": False, "error": "worker returned non-json", "worker_body": raw_text[:2000]}

    # success response with "reply" field expected
    if isinstance(body, dict) and "reply" in body:
        reply = body.get("reply")
        result = {"ok": True, "reply": reply, "status_code": status}
        if debug:
            result["worker_body"] = body
            result["debug_info"] = {"envelope_head": envelope_json[:800], "signature_head": sig[:120], "target": target}
        return result

    # fallback: if debug, return body; otherwise treat as error
    if debug:
        return {"ok": True, "reply": json.dumps(body), "worker_body": body, "status_code": status, "debug_info": {"envelope_head": envelope_json[:800], "signature_head": sig[:120], "target": target}}
    return {"ok": False, "error": "worker did not include reply", "worker_body": json.dumps(body)[:2000]}
