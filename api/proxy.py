import os
import time
import json
import hmac
import hashlib
import secrets
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

WORKER_URL = os.environ.get("WORKER_URL")
SECRET = os.environ.get("SECRET")
MODEL = os.environ.get("MODEL", "HuggingFaceTB/SmolLM3-3B:hf-inference")
# Optional: base URL where candidate AGRI documents are listed (for worker / debugging)
AGRI_DOCS_INDEX = os.environ.get("AGRI_DOCS_INDEX", "https://www.europarl.europa.eu/committees/en/agri/documents/latest-documents")

if not WORKER_URL or not SECRET:
    app.logger.warning("WORKER_URL or SECRET environment variable not set")

SYSTEM_INSTRUCTION = (
    "You are a search assistant that only uses European Parliament AGRI committee documents provided in the context. "
    "You must not invent or infer document titles or links. "
    "If the supplied candidate list contains relevant documents, produce a concise answer that references only those documents by exact title and URL. "
    "If none match, reply exactly: 'I can only search AGRI committee documents; no matching documents found.' "
    "Output must be a JSON array of matches '[{\"title\":\"...\",\"url\":\"...\",\"snippet\":\"...\",\"matched_terms\":\"...\"}]'."
)

def make_envelope(model: str, user_text: str, candidates: list | None = None) -> dict:
    """
    envelope messages: system first, then user. Optionally include a 'candidates' field
    that lists candidate documents the worker/LLM can search against.
    """
    ts = int(time.time())
    nonce = secrets.token_hex(8)

    messages = [
        {"role": "system", "content": SYSTEM_INSTRUCTION},
        {"role": "user", "content": user_text}
    ]

    envelope = {
        "model": model,
        "messages": messages,
        "stream": False,
        "timestamp": ts,
        "nonce": nonce
    }

    # If candidate documents are provided, attach them under a separate field so the downstream worker
    # and/or model can access them (do not rely on implicit web fetching by the model).
    if candidates:
        # candidates should be a list of {"title": "...", "url": "...", "text": "..."} or similar
        envelope["candidates"] = candidates

    return envelope

def sign_envelope(envelope_json: str, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), envelope_json.encode("utf-8"), hashlib.sha256)
    sig = mac.hexdigest()
    return f"sha256={sig}"

@app.route("/api/proxy", methods=["POST"])
def proxy():
    if not WORKER_URL or not SECRET:
        return jsonify({"error": "server configuration missing"}), 500

    body = request.get_json(silent=True) or {}
    user_text = body.get("text")
    # Accept optional 'candidates' list from frontend (preferred) or let worker discover using AGRI_DOCS_INDEX
    candidates = body.get("candidates")

    if not user_text or not isinstance(user_text, str):
        return jsonify({"error": "missing or invalid text field"}), 400

    # Build envelope: include candidates if provided
    envelope = make_envelope(MODEL, user_text, candidates=candidates)
    # Use compact JSON for deterministic signing
    envelope_json = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False)

    signature = sign_envelope(envelope_json, SECRET)
    headers = {"Content-Type": "application/json", "X-Signature": signature}

    try:
        resp = requests.post(f"{WORKER_URL}/chat", headers=headers, data=envelope_json, timeout=30)
    except requests.RequestException as e:
        return jsonify({"error": "failed to reach worker", "detail": str(e)}), 502

    try:
        resp_json = resp.json()
    except ValueError:
        return jsonify({"error": "worker returned non-json", "status_code": resp.status_code, "body": resp.text}), 502

    if resp.status_code < 200 or resp.status_code >= 300:
        return jsonify({"error": "worker error", "status_code": resp.status_code, "worker_response": resp_json}), 502

    # Expect worker to return top-level "reply" and/or the JSON array in reply or raw
    # If the worker forwarded the model output in resp_json["reply"] (string), return it raw.
    reply = resp_json.get("reply")
    raw = resp_json.get("raw")
    # If the worker returned the required JSON array in raw or reply, forward it
    return jsonify({"reply": reply, "raw": raw}), 200


# import os
# import time
# import json
# import hmac
# import hashlib
# import secrets
# import requests
# from flask import Flask, request, jsonify

# app = Flask(__name__)

# WORKER_URL = os.environ.get("WORKER_URL")
# SECRET = os.environ.get("SECRET")
# MODEL = os.environ.get("MODEL", "HuggingFaceTB/SmolLM3-3B:hf-inference")

# if not WORKER_URL or not SECRET:
#     app.logger.warning("WORKER_URL or SECRET environment variable not set")

# def make_envelope(model: str, user_text: str) -> dict:
#     ts = int(time.time())
#     nonce = secrets.token_hex(8)
#     messages = [{"role": "user", "content": user_text}]
#     envelope = {
#         "model": model,
#         "messages": messages,
#         "stream": False,
#         "timestamp": ts,
#         "nonce": nonce
#     }
#     return envelope

# def sign_envelope(envelope_json: str, secret: str) -> str:
#     mac = hmac.new(secret.encode("utf-8"), envelope_json.encode("utf-8"), hashlib.sha256)
#     sig = mac.hexdigest()
#     return f"sha256={sig}"

# @app.route("/api/proxy", methods=["POST"])
# def proxy():
#     if not WORKER_URL or not SECRET:
#         return jsonify({"error": "server configuration missing"}), 500

#     body = request.get_json(silent=True) or {}
#     user_text = body.get("text")
#     if not user_text or not isinstance(user_text, str):
#         return jsonify({"error": "missing or invalid text field"}), 400

#     envelope = make_envelope(MODEL, user_text)
#     envelope_json = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False)

#     signature = sign_envelope(envelope_json, SECRET)
#     headers = {"Content-Type": "application/json", "X-Signature": signature}

#     try:
#         resp = requests.post(f"{WORKER_URL}/chat", headers=headers, data=envelope_json, timeout=30)
#     except requests.RequestException as e:
#         return jsonify({"error": "failed to reach worker", "detail": str(e)}), 502

#     # propagate non-JSON responses as error
#     try:
#         resp_json = resp.json()
#     except ValueError:
#         return jsonify({"error": "worker returned non-json", "status_code": resp.status_code, "body": resp.text}), 502

#     # If worker returned an error-like status, forward it
#     if resp.status_code < 200 or resp.status_code >= 300:
#         return jsonify({"error": "worker error", "status_code": resp.status_code, "worker_response": resp_json}), 502

#     # expected shape based on your example: top-level "reply" and "raw"
#     reply = resp_json.get("reply")
#     raw = resp_json.get("raw")
#     return jsonify({"reply": reply, "raw": raw}), 200

