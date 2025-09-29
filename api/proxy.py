# api/proxy.py
import os
import time
import json
import hmac
import hashlib
import secrets
import logging
from urllib.parse import urlencode, urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify

app = Flask(__name__)
logger = logging.getLogger("agri-proxy")
logger.setLevel(logging.INFO)

# Configuration from environment
WORKER_URL = os.environ.get("WORKER_URL")  # e.g., https://my-worker.x.workers.dev
SECRET = os.environ.get("SECRET", "")
MODEL = os.environ.get("MODEL", "HuggingFaceTB/SmolLM3-3B:hf-inference")
AGRI_SEARCH_BASE = os.environ.get(
    "AGRI_SEARCH_BASE",
    "https://www.europarl.europa.eu/committees/en/agri/documents/search"
)
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "12"))
USER_AGENT = "agri-proxy/1.0"

FALLBACK_EXACT = "I can only search AGRI committee documents; no matching documents found."

# Build strict system + few-shot messages so the worker's LLM call returns
# either the exact fallback string or a JSON array of query strings.
SYSTEM_MSG = (
    "You are an AGRI documents search assistant that only decides whether a user input is a valid search request "
    "for European Parliament AGRI committee documents. Do not act as a general assistant. If the input is NOT a valid "
    "AGRI documents search, output exactly this single-line string (no quotes, no extra whitespace, nothing else):\n"
    "I can only search AGRI committee documents; no matching documents found.\n"
    "If the input IS a valid AGRI documents search, output only a JSON array of one or more plain search-term strings "
    "(only the array, nothing else). Each element must be a short query string suitable to run on the AGRI Documents Search page "
    "(for example: \"CAP final recommendation 2025\", \"CAP Strategic plans amendment time period\"). Do not output explanation, "
    "markup, reasoning, or any text outside the JSON array. The proxy will reject any output that is not exactly the fallback "
    "string or a JSON array of strings."
)

EXAMPLE_USER_1 = "Find final AGRI recommendations about CAP Strategic Plans amendment time period"
EXAMPLE_ASSISTANT_1 = '["CAP Strategic plans amendment final recommendation","CAP Strategic plans amendment time period recommendation 2025"]'

EXAMPLE_USER_2 = "What is the capital of Belgium?"
EXAMPLE_ASSISTANT_2 = "I can only search AGRI committee documents; no matching documents found."


# Helpers
def compact_json(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)

def sign_envelope_bytes(envelope_json: str, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), envelope_json.encode("utf-8"), hashlib.sha256)
    return f"sha256={mac.hexdigest()}"

def try_extract_queries_from_text(s: str):
    if not isinstance(s, str):
        return None, "not a string"
    s_stripped = s.strip()
    if s_stripped == FALLBACK_EXACT:
        return "FALLBACK", None
    try:
        j = json.loads(s_stripped)
        if isinstance(j, list):
            if all(isinstance(x, str) for x in j):
                return j, None
            return None, "array contains non-string elements"
    except Exception:
        pass
    first = s.find('[')
    last = s.rfind(']')
    if first == -1 or last == -1 or last <= first:
        return None, "no JSON array brackets found"
    candidate = s[first:last+1]
    try:
        j = json.loads(candidate)
        if isinstance(j, list) and all(isinstance(x, str) for x in j):
            return j, None
        return None, "extracted array invalid or contains non-strings"
    except Exception as e:
        return None, f"json parse error: {e}"

def normalize_url(u: str, base: str = AGRI_SEARCH_BASE) -> str | None:
    try:
        joined = urljoin(base, u)
        p = urlparse(joined)
        if p.scheme not in ("http", "https") or not p.netloc:
            return None
        return p.geturl()
    except Exception:
        return None

def url_is_alive(url: str) -> bool:
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.head(url, timeout=HTTP_TIMEOUT, allow_redirects=True, headers=headers)
        if 200 <= r.status_code < 400:
            return True
        r = requests.get(url, timeout=HTTP_TIMEOUT, stream=True, headers=headers)
        return 200 <= r.status_code < 400
    except Exception:
        return False

def search_agri(query: str, doc_type: str | None = None, max_candidates: int = 50) -> list:
    if not query:
        return []
    params = {"searchText": query}
    if doc_type:
        params["documentType"] = doc_type
    search_url = AGRI_SEARCH_BASE
    full_url = f"{search_url}?{urlencode(params)}"
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(full_url, timeout=HTTP_TIMEOUT, headers=headers)
        r.raise_for_status()
    except Exception as e:
        logger.warning("AGRI search request failed: %s %s", full_url, e)
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    candidates = []
    seen = set()
    result_blocks = soup.select(".search-result, .result, .doc-list-item, li.result-item")
    anchors = []
    if result_blocks:
        for blk in result_blocks:
            a = blk.find("a", href=True)
            if a:
                anchors.append(a)
    else:
        anchors = soup.find_all("a", href=True)
    for a in anchors:
        href = a["href"].strip()
        title = a.get_text(separator=" ", strip=True)
        if not title:
            continue
        if ("/doceo/document" not in href and "/documents/" not in href and not href.lower().endswith((".pdf", ".doc", ".docx"))):
            parent = a.parent
            cls = " ".join(parent.get("class") or []) if parent else ""
            if "result" not in cls and "doc" not in cls:
                continue
        url_norm = normalize_url(href)
        if not url_norm or url_norm in seen:
            continue
        snippet = ""
        parent = a.parent
        if parent:
            snippet = parent.get_text(" ", strip=True).replace(title, "").strip()
        if not snippet:
            sib = a.find_next_sibling(text=True)
            if sib:
                snippet = str(sib).strip()
        if not snippet:
            snippet = title
        doc_type_label = None
        if parent:
            lab = parent.find(lambda tag: tag.name in ("span","em","strong","div") and any(k in tag.get_text(" ", strip=True).upper() for k in ["RECOMMENDATION","DRAFT","REPORT","OPINION","AMENDMENT"]))
            if lab:
                doc_type_label = lab.get_text(" ", strip=True)
        if url_is_alive(url_norm):
            entry = {"title": title, "url": url_norm, "snippet": snippet}
            if doc_type_label:
                entry["doc_type"] = doc_type_label
            candidates.append(entry)
            seen.add(url_norm)
        if len(candidates) >= max_candidates:
            break
    if doc_type and candidates:
        dt = doc_type.lower()
        filtered = [c for c in candidates if dt in (c.get("doc_type","").lower()) or dt in c["title"].lower() or dt in c["snippet"].lower()]
        if filtered:
            candidates = filtered
    return candidates

def call_worker_classify(user_text: str, debug: bool = False) -> dict:
    if not WORKER_URL or not SECRET:
        return {"ok": False, "error": "server configuration missing"}
    ts = int(time.time())
    nonce = secrets.token_hex(8)
    messages = [
        {"role": "system", "content": SYSTEM_MSG},
        {"role": "user", "content": EXAMPLE_USER_1},
        {"role": "assistant", "content": EXAMPLE_ASSISTANT_1},
        {"role": "user", "content": EXAMPLE_USER_2},
        {"role": "assistant", "content": EXAMPLE_ASSISTANT_2},
        {"role": "user", "content": user_text},
        ]    
    envelope = {"model": MODEL, "messages": messages, "stream": False, "timestamp": ts, "nonce": nonce}
    envelope_json = compact_json(envelope)
    sig = sign_envelope_bytes(envelope_json, SECRET.strip())
    target = WORKER_URL.rstrip("/") + "/chat"
    headers = {"Content-Type": "application/json", "X-Signature": sig}
    # Log key info (server logs)
    logger.info("Calling worker target=%s", target)
    logger.info("Envelope_head=%s", envelope_json[:800])
    logger.info("Signature_head=%s", sig[:120])
    try:
        resp = requests.post(target, headers=headers, data=envelope_json.encode("utf-8"), timeout=30)
    except Exception as e:
        logger.error("Error calling worker: %s", e)
        return {"ok": False, "error": "worker unreachable", "detail": str(e)}
    raw_text = resp.text or ""
    # If resp.status_code indicates error, return diagnostic as before
    if resp.status_code < 200 or resp.status_code >= 300:
        logger.error("Worker returned status %s body_head=%s", resp.status_code, raw_text[:2000])
        return {"ok": False, "error": "worker error", "status_code": resp.status_code, "worker_body": raw_text[:2000], "debug_info": {"envelope_head": envelope_json[:800], "signature_head": sig[:120], "target": target}}

    # Prefer the worker's JSON "reply" field when present
    worker_reply_str = None
    try:
        resp_json = resp.json()
        if isinstance(resp_json, dict) and "reply" in resp_json:
            worker_reply_str = str(resp_json.get("reply", "")).strip()
        else:
            # fallback to raw text if JSON has no reply field
            worker_reply_str = raw_text.strip()
    except ValueError:
        # not JSON, use raw text
        worker_reply_str = raw_text.strip()

    # Now run the strict parser on the extracted worker reply string
    parsed, err = try_extract_queries_from_text(worker_reply_str)
    if parsed == "FALLBACK":
        return {"ok": True, "type": "fallback", "message": FALLBACK_EXACT}
    if parsed is None:
        return {"ok": False, "error": "invalid worker reply", "detail": err, "worker_body": worker_reply_str[:2000], "debug_info": {"envelope_head": envelope_json[:800], "signature_head": sig[:120], "target": target, "worker_raw_head": raw_text[:2000]}}
    # parsed is list of strings
    if not isinstance(parsed, list) or len(parsed) == 0:
        return {"ok": True, "type": "fallback", "message": FALLBACK_EXACT}
    return {"ok": True, "type": "queries", "queries": parsed}

@app.route("/api/proxy", methods=["POST"])
def proxy():
    body = request.get_json(silent=True) or {}
    user_text = body.get("text")
    doc_type = body.get("doc_type")
    debug = bool(body.get("debug", False))
    if not user_text or not isinstance(user_text, str):
        return jsonify({"error": "missing or invalid text field"}), 400
    # Classify via worker
    worker_ret = call_worker_classify(user_text, debug=debug)
    if not worker_ret.get("ok"):
        # If debug requested, pass debug_info through
        error_payload = {"error": "worker classification failed", "detail": worker_ret}
        return jsonify(error_payload), 502
    if worker_ret["type"] == "fallback":
        # Friendly reply
        payload = {"reply": "No documents found", "matches": []}
        if debug:
            payload["debug_info"] = worker_ret.get("debug_info", {})
        return jsonify(payload), 200
    queries = worker_ret.get("queries", [])
    if not queries:
        payload = {"reply": "No documents found", "matches": []}
        if debug:
            payload["debug_info"] = worker_ret.get("debug_info", {})
        return jsonify(payload), 200
    # Execute web searches
    aggregated = []
    seen_urls = set()
    for q in queries:
        logger.info("Searching AGRI for query=%s doc_type=%s", q, doc_type)
        results = search_agri(q, doc_type=doc_type, max_candidates=40)
        for r in results:
            if r["url"] in seen_urls:
                continue
            aggregated.append({"source_query": q, **r})
            seen_urls.add(r["url"])
    if not aggregated:
        payload = {"reply": "No documents found", "matches": []}
        if debug:
            payload["debug_info"] = worker_ret.get("debug_info", {})
        return jsonify(payload), 200
    payload = {"reply": "matches", "matches": aggregated}
    if debug:
        payload["debug_info"] = worker_ret.get("debug_info", {})
    return jsonify(payload), 200

if __name__ == "__main__":
    app.run(port=8000, debug=True)
