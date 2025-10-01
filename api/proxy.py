# api/proxy.py
import time
import json
import hmac
import hashlib
import secrets
import logging
import requests
from flask import Flask, request, jsonify
import uuid
from typing import Dict, Any
from config import WORKER_URL, SECRET, MODEL

app = Flask(__name__)
logger = logging.getLogger("agri-proxy")
logger.setLevel(logging.INFO)

# session memory
# Structure: { session_id: { "system_msg": <str or None>, "history": [ {"role": "...", "content":"..."} ] } }
CHAT_MEMORY: Dict[str, Dict[str, Any]] = {}
# Maximum messages to keep per session to avoid unbounded memory growth
MAX_HISTORY_MESSAGES = 12

# Helpers
def ensure_session(session_id: str) -> Dict[str, Any]:
    if session_id not in CHAT_MEMORY:
        CHAT_MEMORY[session_id] = {"system_msg": None, "history": []}
    return CHAT_MEMORY[session_id]

def reset_session_memory(session_id: str, new_system_msg: str | None = None):
    CHAT_MEMORY[session_id] = {"system_msg": new_system_msg, "history": []}

def append_to_history(session_id: str, role: str, content: str):
    sess = ensure_session(session_id)
    sess["history"].append({"role": role, "content": content})
    # trim oldest if needed
    if len(sess["history"]) > MAX_HISTORY_MESSAGES:
        sess["history"] = sess["history"][-MAX_HISTORY_MESSAGES:]

#
def compact_json(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)

def sign_envelope_bytes(envelope_json: str, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), envelope_json.encode("utf-8"), hashlib.sha256)
    return f"sha256={mac.hexdigest()}"

def call_worker(user_text: str, system_msg: str | None = None, 
                session_id: str | None = None, debug: bool = False) -> dict:
    """
    Forward messages to the Cloudflare Worker and returns the 
    worker's 'reply' content verbatim. Returns a dict:
      - ok: bool
      - reply: str (when ok)
      - worker_body: parsed JSON (when debug or on error)
      - status_code: int (worker HTTP status)
      - error/detail keys on failure
    """
    if not WORKER_URL or not SECRET:
        return {"ok": False, "error": "server configuration missing"}

    ts = int(time.time())
    nonce = secrets.token_hex(8)
    # choose system message provided by client or None (send only if present)
    messages = []
    if system_msg and isinstance(system_msg, str) and system_msg.strip():
        messages.append({"role": "system", "content": system_msg})

    if session_id:
        sess = CHAT_MEMORY.get(session_id)
        if sess and isinstance(sess.get("history"), list):
            # copy the stored history into messages: these are prior assistant/user messages
            for entry in sess["history"]:
                # only append valid entries of form {"role":..,"content":..}
                if isinstance(entry, dict) and "role" in entry and "content" in entry:
                    messages.append({"role": entry["role"], "content": entry["content"]})

    # append the current user message 
    messages.append({"role": "user", "content": user_text})

    envelope = {"model": MODEL, "messages": messages, "stream": False, "timestamp": ts, "nonce": nonce}
    envelope_json = compact_json(envelope)
    sig = sign_envelope_bytes(envelope_json, SECRET.strip())
    target = WORKER_URL.rstrip("/") + "/chat"
    headers = {"Content-Type": "application/json", "X-Signature": sig}

    logger.info("call_worker -> target=%s", target)
    logger.info("envelope_head=%s", envelope_json[:800])
    logger.info("signature_head=%s", sig[:120])

    try:
        resp = requests.post(target, headers=headers, data=envelope_json.encode("utf-8"), timeout=30)
    except Exception as e:
        logger.error("Error calling worker: %s", e)
        return {"ok": False, "error": "worker unreachable", "detail": str(e)}

    raw_text = resp.text or ""
    status = resp.status_code

    # if non-2xx return error + debug snippet
    if status < 200 or status >= 300:
        logger.error("Worker returned status %s body_head=%s", status, raw_text[:2000])
        return {
            "ok": False,
            "error": "worker error",
            "status_code": status,
            "worker_body": raw_text[:2000],
            "debug_info": {"envelope_head": envelope_json[:800], "signature_head": sig[:120], "target": target}
        }

    # Try parse JSON and extract reply field if present
    try:
        body = resp.json()
    except Exception:
        # not JSON: return raw text as reply when debug requested, else error
        if debug:
            return {"ok": True, "reply": raw_text, "worker_body": raw_text, "status_code": status, "debug_info": {"envelope_head": envelope_json[:800], "signature_head": sig[:120], "target": target}}
        return {"ok": False, "error": "worker returned non-json", "worker_body": raw_text[:2000]}

    # Extract reply
    if isinstance(body, dict) and "reply" in body:
        reply = body.get("reply")
        result = {"ok": True, "reply": reply, "status_code": status}
        if debug:
            result["worker_body"] = body
            result["debug_info"] = {"envelope_head": envelope_json[:800], "signature_head": sig[:120], "target": target}
        return result

    # If no reply field, return body under worker_body (debug) or error
    if debug:
        return {"ok": True, "reply": json.dumps(body), "worker_body": body, "status_code": status, "debug_info": {"envelope_head": envelope_json[:800], "signature_head": sig[:120], "target": target}}
    return {"ok": False, "error": "worker did not include reply", "worker_body": json.dumps(body)[:2000]}

@app.route("/api/proxy", methods=["POST"])
def proxy():
    body = request.get_json(silent=True) or {}
    user_text = body.get("text")
    debug = bool(body.get("debug", False))
    # client should include session_id (string). If absent, generate one and return it in response.
    session_id = body.get("session_id")
    system_msg = body.get("system_msg")

    if not user_text or not isinstance(user_text, str):
        return jsonify({"error": "missing or invalid text field"}), 400

    # create session if missing
    if not session_id:
        session_id = str(uuid.uuid4())
        reset_session_memory(session_id, system_msg if system_msg else None)
    else:
        # ensure session exists
        sess = ensure_session(session_id)
        # if SYSTEM_MSG changed from stored, reset session memory
        stored_sys = sess.get("system_msg")
        if system_msg and system_msg.strip() and system_msg != stored_sys:
            reset_session_memory(session_id, system_msg)
        elif system_msg is None and stored_sys is None:
            # nothing to do
            pass
        # if client supplied empty system_msg but server had one, do nothing (keeps stored)

    # Append current user message into memory BEFORE calling worker so worker sees the full conversation
    append_to_history(session_id, "user", user_text)

    # Call the worker passing session_id and the effective system_msg
    worker_ret = call_worker(user_text, system_msg=system_msg, session_id=session_id, debug=debug)

    # After call, append the assistant reply to history (if any)
    if worker_ret.get("ok") and "reply" in worker_ret and isinstance(worker_ret["reply"], str):
        append_to_history(session_id, "assistant", worker_ret["reply"])

    # include session_id so client keeps using same session
    if not worker_ret.get("ok"):
        # include debug_info when present
        err_payload = {"error": "worker call failed", **{k: v for k, v in worker_ret.items() if k != "worker_body"}}
        err_payload["session_id"] = session_id
        if debug and "worker_body" in worker_ret:
            err_payload["worker_body"] = worker_ret["worker_body"]
        return jsonify(err_payload), 502
    
    # Success: return worker's reply verbatim
    payload = {"reply": worker_ret.get("reply"), "session_id": session_id}
    if debug:
        payload["debug_info"] = worker_ret.get("debug_info", {})
        payload["worker_body"] = worker_ret.get("worker_body")
        payload["status_code"] = worker_ret.get("status_code")
    return jsonify(payload), 200
    
if __name__ == "__main__":
    app.run(port=8000, debug=True)
