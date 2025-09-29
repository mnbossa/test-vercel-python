# api/proxy.py
import os
import time
import json
import hmac
import hashlib
import secrets
import requests
from urllib.parse import urlencode, urljoin, urlparse
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify

app = Flask(__name__)

# Required env vars
WORKER_URL = os.environ.get("WORKER_URL")  # e.g., https://my-worker.example.workers.dev
SECRET = os.environ.get("SECRET")          # HMAC secret used by worker
MODEL = os.environ.get("MODEL", "HuggingFaceTB/SmolLM3-3B:hf-inference")

# AGRI search endpoint base
AGRI_SEARCH_BASE = os.environ.get(
    "AGRI_SEARCH_BASE",
    "https://www.europarl.europa.eu/committees/en/agri/documents/search"
)

HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "12"))
USER_AGENT = "agri-proxy/1.0"

# Worker fallback exact string (kept for parity with your worker)
FALLBACK_EXACT = "I can only search AGRI committee documents; no matching documents found."

if not WORKER_URL or not SECRET:
    app.logger.warning("WORKER_URL or SECRET not set; worker classification will fail")

# ---------------------------
# Worker call helpers
# ---------------------------
def compact_json(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)

def sign_envelope(envelope_json: str, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), envelope_json.encode("utf-8"), hashlib.sha256)
    return f"sha256={mac.hexdigest()}"

def call_worker_classify(user_text: str) -> dict:
    """
    Send a compact envelope to the Cloudflare worker at WORKER_URL/chat.
    The worker's job: decide if the user_text is an AGRI search, and if so produce
    a JSON array of plain query strings; otherwise return the exact FALLBACK_EXACT string
    or a short instruction message.
    Returns: {"ok":True,"type":"queries","queries":[...]} or {"ok":True,"type":"fallback","message":"..."} or {"ok":False,"error":...}
    """
    if not WORKER_URL or not SECRET:
        return {"ok": False, "error": "server configuration missing"}

    # Build minimal envelope: no system heavy instruction here; worker contains the classifier logic
    ts = int(time.time())
    nonce = secrets.token_hex(8)
    # we send a short message asking the worker to classify/produce queries
    messages = [{"role": "user", "content": user_text}]
    envelope = {"model": MODEL, "messages": messages, "stream": False, "timestamp": ts, "nonce": nonce}
    envelope_json = compact_json(envelope)
    signature = sign_envelope(envelope_json, SECRET)
    headers = {"Content-Type": "application/json", "X-Signature": signature}

    target = WORKER_URL.rstrip("/") + "/chat"
    try:
        resp = requests.post(target, headers=headers, data=envelope_json.encode("utf-8"), timeout=30)
    except requests.RequestException as e:
        app.logger.error("Error calling worker: %s", e)
        return {"ok": False, "error": "worker unreachable", "detail": str(e)}

    text = resp.text or ""
    # If non-2xx, return raw error
    if resp.status_code < 200 or resp.status_code >= 300:
        app.logger.error("Worker returned status %s body head: %s", resp.status_code, text[:2000])
        return {"ok": False, "error": "worker error", "status_code": resp.status_code, "worker_body": text[:2000]}

    # Parse JSON if possible
    try:
        j = resp.json()
    except ValueError:
        # Not JSON — try to extract first JSON array or fallback string from free text
        arr, err = try_extract_json_array_from_text(text)
        if arr == "FALLBACK":
            return {"ok": True, "type": "fallback", "message": FALLBACK_EXACT}
        if arr is None:
            # fallback to passing entire raw text as a message
            # Worker may have returned plain explanation, accept that
            # But we mark as fallback so the client gets instructions
            return {"ok": True, "type": "fallback", "message": text.strip()[:2000]}
        # arr is a list — expect list of strings
        queries = []
        for e in arr:
            if isinstance(e, str) and e.strip():
                queries.append(e.strip())
        if queries:
            return {"ok": True, "type": "queries", "queries": queries}
        return {"ok": True, "type": "fallback", "message": FALLBACK_EXACT}

    # If JSON, prefer j["reply"] if string, or j["raw"]
    reply = j.get("reply") if isinstance(j, dict) else None
    raw = j.get("raw") if isinstance(j, dict) else None
    candidate = None
    if reply:
        candidate = reply
    elif raw:
        # raw may be nested; stringify
        try:
            candidate = json.dumps(raw)
        except Exception:
            candidate = str(raw)
    else:
        # entire JSON could be the array
        if isinstance(j, list):
            candidate = j
        else:
            candidate = json.dumps(j)

    # Candidate may be list or string
    if isinstance(candidate, list):
        # expecting list of strings
        queries = [str(x).strip() for x in candidate if isinstance(x, str) and x.strip()]
        if queries:
            return {"ok": True, "type": "queries", "queries": queries}
        return {"ok": True, "type": "fallback", "message": FALLBACK_EXACT}

    # candidate is string: try extracing array or fallback
    arr, err = try_extract_json_array_from_text(candidate)
    if arr == "FALLBACK":
        return {"ok": True, "type": "fallback", "message": FALLBACK_EXACT}
    if arr is None:
        # not array — treat as free-text instruction
        return {"ok": True, "type": "fallback", "message": candidate.strip()[:2000]}
    # arr is a list of queries (hopefully strings)
    queries = [str(x).strip() for x in arr if isinstance(x, str) and x.strip()]
    if queries:
        return {"ok": True, "type": "queries", "queries": queries}
    return {"ok": True, "type": "fallback", "message": FALLBACK_EXACT}

def try_extract_json_array_from_text(s: str):
    """
    Try to extract a JSON array from string s.
    Returns (list | "FALLBACK" | None, error_str_or_None)
    """
    if not isinstance(s, str):
        return None, "not a string"
    s_strip = s.strip()
    if s_strip == FALLBACK_EXACT:
        return "FALLBACK", None
    # try parse whole string
    try:
        j = json.loads(s_strip)
        if isinstance(j, list):
            return j, None
    except Exception:
        pass
    # find first '[' and last ']'
    first = s.find('[')
    last = s.rfind(']')
    if first == -1 or last == -1 or last <= first:
        return None, "no JSON array brackets found"
    candidate = s[first:last+1]
    try:
        j = json.loads(candidate)
        if isinstance(j, list):
            return j, None
        return None, "extracted JSON not array"
    except Exception as e:
        return None, f"json parse error: {e}"

# ---------------------------
# AGRI search helpers (no LLM)
# ---------------------------
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
    except requests.RequestException:
        return False

def search_agri(query: str, doc_type: str | None = None, max_candidates: int = 50) -> list:
    """
    Query the AGRI Documents Search page and return sanitized candidates:
    [{"title":"...","url":"...","snippet":"...","doc_type":"..."}]
    """
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
    except requests.RequestException as e:
        app.logger.warning("AGRI search failed: %s %s", full_url, e)
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    candidates = []
    seen = set()

    # Primary attempt: look for result containers used by the site
    # If none, fallback to anchors containing '/doceo/document' or file extensions
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
        # only consider likely document links
        if ("/doceo/document" not in href and "/documents/" not in href and not href.lower().endswith((".pdf", ".doc", ".docx"))):
            # allow anchors within result blocks even if href is not file-like
            parent = a.parent
            if not parent:
                continue
            cls = " ".join(parent.get("class") or [])
            if "result" not in cls and "doc" not in cls:
                continue

        url_norm = normalize_url(href)
        if not url_norm or url_norm in seen:
            continue

        # snippet: parent text without title
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

        # attempt to detect doc_type label
        doc_type_label = None
        if parent:
            label = parent.find(lambda tag: tag.name in ("span","em","strong","div") and any(k in tag.get_text(" ", strip=True).upper() for k in ["RECOMMENDATION","DRAFT","REPORT","OPINION","AMENDMENT"]))
            if label:
                doc_type_label = label.get_text(" ", strip=True)

        # verify url liveness
        if url_is_alive(url_norm):
            entry = {"title": title, "url": url_norm, "snippet": snippet}
            if doc_type_label:
                entry["doc_type"] = doc_type_label
            candidates.append(entry)
            seen.add(url_norm)
        if len(candidates) >= max_candidates:
            break

    # If doc_type requested, filter conservatively
    if doc_type and candidates:
        dt = doc_type.lower()
        filtered = [c for c in candidates if dt in (c.get("doc_type","").lower()) or dt in c["title"].lower() or dt in c["snippet"].lower()]
        if filtered:
            candidates = filtered

    return candidates

# ---------------------------
# API route
# ---------------------------
@app.route("/api/proxy", methods=["POST"])
def proxy():
    """
    Request JSON: {"text":"user query", "doc_type":"RECOMMENDATION"(optional)}
    Flow:
      - Call worker to classify and/or produce queries (no LLM usage later).
      - If worker returns fallback/instruction -> return it to caller.
      - If worker returns list of search queries -> run search_agri for each query,
        aggregate unique results and return them.
    """
    body = request.get_json(silent=True) or {}
    user_text = body.get("text")
    doc_type = body.get("doc_type")  # optional

    if not user_text or not isinstance(user_text, str):
        return jsonify({"error": "missing or invalid text field"}), 400

    # 1) Ask worker to decide and produce concrete search queries
    worker_ret = call_worker_classify(user_text)
    if not worker_ret.get("ok"):
        return jsonify({"error": "worker classification failed", "detail": worker_ret}), 502

    if worker_ret["type"] == "fallback":
        # return instruction or fallback message to user
        return jsonify({"reply": worker_ret.get("message", "No documents found"), "matches": []}), 200

    if worker_ret["type"] != "queries":
        return jsonify({"error": "unexpected worker response type", "detail": worker_ret}), 502

    queries = worker_ret.get("queries", [])
    if not queries:
        return jsonify({"reply": "No documents found", "matches": []}), 200

    # 2) For each query, perform actual web search and collect candidates
    aggregated = []
    seen_urls = set()
    for q in queries:
        app.logger.info("Searching AGRI for query: %s", q)
        results = search_agri(q, doc_type=doc_type, max_candidates=40)
        for r in results:
            if r["url"] in seen_urls:
                continue
            aggregated.append({"source_query": q, **r})
            seen_urls.add(r["url"])
        # small respectful pause optional (commented out)
        # time.sleep(0.2)

    if not aggregated:
        return jsonify({"reply": "No documents found", "matches": []}), 200

    return jsonify({"reply": "matches", "matches": aggregated}), 200

# ---------------------------
# Run locally helper (for dev only)
# ---------------------------
if __name__ == "__main__":
    app.run(port=8000, debug=True)
