import os
import re
import time
import json
import hmac
import hashlib
import secrets
import requests
from urllib.parse import urlparse
from flask import Flask, request, jsonify

app = Flask(__name__)

WORKER_URL = os.environ.get("WORKER_URL")
SECRET = os.environ.get("SECRET")
MODEL = os.environ.get("MODEL", "HuggingFaceTB/SmolLM3-3B:hf-inference")
AGRI_DOCS_INDEX = os.environ.get("AGRI_DOCS_INDEX", "https://www.europarl.europa.eu/committees/en/agri/documents/latest-documents")

SYSTEM_INSTRUCTION = (
    "You are a search assistant that only uses European Parliament AGRI committee documents provided in the context. "
    "You must not invent or infer document titles or links. "
    "If the supplied candidate list contains relevant documents, produce a concise answer that references only those documents by exact title and URL. "
    "If none match, reply exactly: 'I can only search AGRI committee documents; no matching documents found.' "
    "Output must be a JSON array of matches '[{\"title\":\"...\",\"url\":\"...\",\"snippet\":\"...\",\"matched_terms\":\"...\"}]'."
)

# network params
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "10"))
VERIFY_TLS = os.environ.get("VERIFY_TLS", "true").lower() != "false"
USER_AGENT = "agri-proxy/1.0"

if not WORKER_URL or not SECRET:
    app.logger.warning("WORKER_URL or SECRET environment variable not set")

def compact_json(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)

def sign_envelope(envelope_json: str, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), envelope_json.encode("utf-8"), hashlib.sha256)
    return f"sha256={mac.hexdigest()}"

def normalize_url(u: str) -> str | None:
    try:
        p = urlparse(u)
        if p.scheme not in ("http", "https") or not p.netloc:
            return None
        return p.geturl()
    except Exception:
        return None

def url_is_alive(url: str) -> bool:
    # Try HEAD, fallback to GET limited bytes
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.head(url, timeout=HTTP_TIMEOUT, allow_redirects=True, headers=headers, verify=VERIFY_TLS)
        if r.status_code >= 200 and r.status_code < 400:
            return True
        # Some servers reject HEAD; try small GET
        r = requests.get(url, timeout=HTTP_TIMEOUT, stream=True, headers=headers, verify=VERIFY_TLS)
        return 200 <= r.status_code < 400
    except requests.RequestException:
        return False

def sanitize_candidates(raw_candidates: list) -> list:
    """
    Accepts candidate dicts from the caller. Verifies URL format and reachability.
    Returns a filtered list containing only reachable candidates with exact title and url preserved.
    """
    out = []
    for c in raw_candidates:
        if not isinstance(c, dict):
            continue
        title = c.get("title")
        url = c.get("url")
        if not title or not url:
            continue
        url_norm = normalize_url(url)
        if not url_norm:
            continue
        if url_is_alive(url_norm):
            # preserve only title and url and optional text/snippet if provided
            entry = {"title": title, "url": url_norm}
            if "text" in c:
                entry["text"] = c["text"]
            if "snippet" in c:
                entry["snippet"] = c["snippet"]
            out.append(entry)
    return out

def make_envelope(model: str, system_instruction: str, user_text: str, candidates: list | None = None) -> dict:
    ts = int(time.time())
    nonce = secrets.token_hex(8)
    messages = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": user_text}
    ]
    envelope = {
        "model": model,
        "messages": messages,
        "stream": False,
        "timestamp": ts,
        "nonce": nonce
    }
    if candidates:
        envelope["candidates"] = candidates
    return envelope

def validate_worker_reply(reply_value):
    # Accept exact fallback string or JSON array of objects with required keys
    fallback = "I can only search AGRI committee documents; no matching documents found."
    if isinstance(reply_value, str) and reply_value.strip() == fallback:
        return {"ok": True, "is_fallback": True, "value": reply_value}
    # Try parse if it's a JSON string
    arr = None
    if isinstance(reply_value, str):
        try:
            arr = json.loads(reply_value)
        except Exception:
            return {"ok": False, "error": "reply not valid JSON array nor exact fallback string"}
    elif isinstance(reply_value, list):
        arr = reply_value
    else:
        return {"ok": False, "error": "reply is neither string nor list"}

    if not isinstance(arr, list):
        return {"ok": False, "error": "reply JSON is not an array"}

    # validate each entry
    for i, item in enumerate(arr):
        if not isinstance(item, dict):
            return {"ok": False, "error": f"array element {i} not an object"}
        for key in ("title", "url", "snippet", "matched_terms"):
            if key not in item:
                return {"ok": False, "error": f"array element {i} missing key '{key}'"}
    return {"ok": True, "is_fallback": False, "value": arr}

FALLBACK_EXACT = "I can only search AGRI committee documents; no matching documents found."

def try_extract_json_array_from_text(s: str):
    """
    Attempts to find the first JSON array in string s and parse it.
    Returns (parsed_array or None, error_message_or_none)
    """
    if not isinstance(s, str):
        return None, "not a string"
    # quick check: exact fallback
    if s.strip() == FALLBACK_EXACT:
        return "FALLBACK", None

    # try direct JSON parse first
    try:
        j = json.loads(s)
        if isinstance(j, list):
            return j, None
    except Exception:
        pass

    # try to extract substring between first '[' and last ']'
    first = s.find('[')
    last = s.rfind(']')
    if first == -1 or last == -1 or last <= first:
        return None, "no JSON array brackets found"
    candidate = s[first:last+1]
    try:
        j = json.loads(candidate)
        if isinstance(j, list):
            return j, None
        return None, "extracted JSON not an array"
    except Exception as e:
        return None, f"json parse error: {e}"

def validate_matches_array(arr):
    if not isinstance(arr, list):
        return False, "not a list"
    for i, item in enumerate(arr):
        if not isinstance(item, dict):
            return False, f"element {i} not an object"
        for k in ("title","url","snippet","matched_terms"):
            if k not in item:
                return False, f"element {i} missing key {k}"
    return True, None

@app.route("/api/proxy", methods=["POST"])
def proxy():
    if not WORKER_URL or not SECRET:
        return jsonify({"error": "server configuration missing"}), 500

    body = request.get_json(silent=True) or {}
    user_text = body.get("text")
    raw_candidates = body.get("candidates")

    if not user_text or not isinstance(user_text, str):
        return jsonify({"error": "missing or invalid text field"}), 400

    candidates = None
    if isinstance(raw_candidates, list):
        candidates = sanitize_candidates(raw_candidates)

    envelope = make_envelope(MODEL, SYSTEM_INSTRUCTION, user_text, candidates=candidates)
    envelope_json = compact_json(envelope)
    signature = sign_envelope(envelope_json, SECRET)
    headers = {"Content-Type": "application/json", "X-Signature": signature}

    try:
        resp = requests.post(f"{WORKER_URL.rstrip('/')}/chat", headers=headers, data=envelope_json.encode("utf-8"), timeout=30)
    except requests.RequestException as e:
        return jsonify({"error": "failed to reach worker", "detail": str(e)}), 502

    try:
        resp_json = resp.json()
    except ValueError:
        return jsonify({"error": "worker returned non-json", "status_code": resp.status_code, "body": resp.text}), 502

    if resp.status_code < 200 or resp.status_code >= 300:
        return jsonify({"error": "worker error", "status_code": resp.status_code, "worker_response": resp_json}), 502

    # Worker expected to return top-level 'reply' string (as in your example) or already parsed JSON in 'raw'
    reply_field = resp_json.get("reply")
    raw_field = resp_json.get("raw")

    # prefer reply_field (string) when present
    candidate_value = reply_field if reply_field is not None else raw_field
    if candidate_value is None:
        return jsonify({"error":"worker response missing 'reply' and 'raw'","worker_response":resp_json}), 502
    
    # Normalize candidate_value to string if it's not
    if not isinstance(candidate_value, str):
        # try to serialize structured raw_field into string for extraction
        try:
            candidate_value = json.dumps(candidate_value)
        except Exception:
            candidate_value = str(candidate_value)
    
    extracted, err = try_extract_json_array_from_text(candidate_value)
    if extracted == "FALLBACK":
        # map to friendly message
        return jsonify({"reply": "No documents found", "matches": []}), 200
    if extracted is None:
        # preserve raw for debugging in logs
        app.logger.warning("Worker reply could not be parsed as JSON array: %s", err)
        return jsonify({"error":"invalid worker reply","detail":err,"worker_response":candidate_value}), 502
    
    ok, v_err = validate_matches_array(extracted)
    if not ok:
        app.logger.warning("Worker returned array but validation failed: %s", v_err)
        return jsonify({"error":"invalid worker reply","detail":v_err,"worker_response":extracted}), 502

    # Valid array
    if len(extracted) == 0:
        return jsonify({"reply":"No documents found","matches":[]}), 200
    
    return jsonify({"reply":"matches","matches":extracted}), 200
