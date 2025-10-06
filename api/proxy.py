# api/proxy.py
import time
import json
import secrets
import logging
from flask import Flask, request, jsonify
import uuid
from typing import Dict, Any
from config import call_openai_chat, call_hf_chat, BACKEND 

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

def call_chat(user_text: str, system_msg: str | None = None, 
                session_id: str | None = None, debug: bool = False) -> dict:
    """
    Forward messages and returns the reply content verbatim. Returns a dict:
      - ok: bool
      - reply: str (when ok)
      - worker_body: parsed JSON (when debug or on error)
      - status_code: int (worker HTTP status)
      - error/detail keys on failure
    Unified frontend for two backends:
      - Cloudflare Worker (when BACKEND == "hf")
      - OpenAI direct (when BACKEND == "openai")
    """

    ts = int(time.time())
    nonce = secrets.token_hex(8)
    messages = []
    if system_msg and isinstance(system_msg, str) and system_msg.strip():
        messages.append({"role": "system", "content": system_msg})

    if session_id:
        sess = CHAT_MEMORY.get(session_id)
        if sess and isinstance(sess.get("history"), list):
            for entry in sess["history"]:
                if isinstance(entry, dict) and "role" in entry and "content" in entry:
                    messages.append({"role": entry["role"], "content": entry["content"]})

    messages.append({"role": "user", "content": user_text})

    payload = {"messages": messages, "stream": False, "timestamp": ts, "nonce": nonce}

    # Route to OpenAI directly
    if BACKEND == "openai":
        logger.info("call_worker -> routing to OpenAI (direct) model=%s", payload.get("model"))
        # call_openai_chat expects a payload with keys "model" and "messages"
        try:
            oret = call_openai_chat(payload)
        except Exception as e:
            logger.exception("OpenAI call raised")
            return {"ok": False, "error": "openai call exception", "detail": str(e)}

        # success
        reply = oret.get("reply", "")
        result = {"ok": True, "reply": reply, "status_code": 200}
        if debug:
            result["worker_body"] = oret.get("raw", {})
            result["debug_info"] = {"payload_head": compact_json(payload)[:800], "backend": "openai"}
        return result

    # Call the existing Cloudflare Worker and huggingface
    try:
        hf_ret = call_hf_chat(payload, debug=debug)
    except Exception as e:
        logger.exception("call_hf_chat raised")
        return {"ok": False, "error": "worker exception", "detail": str(e)}

    return hf_ret
    

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
    worker_ret = call_chat(user_text, system_msg=system_msg, session_id=session_id, debug=debug)

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
