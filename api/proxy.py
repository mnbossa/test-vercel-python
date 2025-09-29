import os
import re
import time
import json
import hmac
import hashlib
import secrets
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
from urllib.parse import urlencode

app = Flask(__name__)

WORKER_URL = os.environ.get("WORKER_URL")
SECRET = os.environ.get("SECRET")
MODEL = os.environ.get("MODEL", "HuggingFaceTB/SmolLM3-3B:hf-inference")
AGRI_DOCS_INDEX = os.environ.get("AGRI_DOCS_INDEX", "https://www.europarl.europa.eu/committees/en/agri/documents/latest-documents")

HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "10"))
USER_AGENT = "agri-proxy/1.0"

SYSTEM_INSTRUCTION = (
    "You are a search assistant that only uses European Parliament AGRI committee documents provided in the context. "
    "You must not invent or infer document titles or links. "
    "If the supplied candidate list contains relevant documents, produce a concise answer that references only those documents by exact title and URL. "
    "If none match, reply exactly: 'I can only search AGRI committee documents; no matching documents found.' "
    "Output must be a JSON array of matches '[{\"title\":\"...\",\"url\":\"...\",\"snippet\":\"...\",\"matched_terms\":\"...\"}]'."
)

if not WORKER_URL or not SECRET:
    app.logger.warning("WORKER_URL or SECRET environment variable not set")

def compact_json(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)

def sign_envelope(envelope_json: str, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), envelope_json.encode("utf-8"), hashlib.sha256)
    return f"sha256={mac.hexdigest()}"

def normalize_url(u: str, base: str = AGRI_DOCS_INDEX) -> str | None:
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
    Query the AGRI Documents Search page for `query` and optional `doc_type` filter.
    Returns a list of sanitized candidate dicts: {"title","url","snippet","doc_type"}.
    """
    if not query or not isinstance(query, str):
        return []

    params = {"searchText": query}
    # the site uses document type labels; when provided, try to set documentType param (heuristic)
    if doc_type:
        params["documentType"] = doc_type

    search_url = AGRI_DOCS_INDEX.replace("/latest-documents", "/search")
    full_url = f"{search_url}?{urlencode(params)}"
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(full_url, timeout=HTTP_TIMEOUT, headers=headers)
        r.raise_for_status()
    except requests.RequestException as e:
        app.logger.warning("AGRI search request failed: %s %s", full_url, e)
        return []

    soup = BeautifulSoup(r.text, "html.parser")

    candidates = []
    seen_urls = set()

    # Heuristic: each search result is a result card; look for result blocks
    # Find elements whose class or structure looks like a search result
    # Fallback: scan anchors that contain '/doceo/document' or file extensions
    # We'll attempt both approaches to be robust.

    # Primary: result container blocks (common patterns on europarl)
    result_blocks = soup.select(".search-result, .result, .doc-list-item, li.result-item")
    if not result_blocks:
        # fallback to anchors
        anchors = soup.find_all("a", href=True)
    else:
        anchors = []
        for blk in result_blocks:
            a = blk.find("a", href=True)
            if a:
                anchors.append(a)

    # parse anchors
    for a in anchors:
        href = a["href"].strip()
        title_text = a.get_text(separator=" ", strip=True)
        if not title_text:
            continue
        # only consider document-like links
        if ("/doceo/document" not in href and "/documents/" not in href and
            not href.lower().endswith((".pdf", ".doc", ".docx"))):
            # try to include anchors under result blocks with meaningful class
            parent = a.parent
            if parent and ("result" in (parent.get("class") or []) or "doc" in (parent.get("class") or [])):
                pass
            else:
                continue

        url_norm = normalize_url(href)
        if not url_norm or url_norm in seen_urls:
            continue

        # try to extract doc_type label inside sibling elements
        doc_type_label = None
        parent = a.parent
        if parent:
            # look for small labels or spans indicating type
            lbl = parent.find(lambda tag: tag.name in ("span","em","strong","div") and re.search(r"\b(RECOMMENDATION|DRAFT|REPORT|OPINION|AMENDMENT|PDF|DOC)\b", tag.get_text(" ", strip=True), re.I))
            if lbl:
                doc_type_label = lbl.get_text(" ", strip=True)

        # snippet heuristic: parent's text minus title
        snippet = ""
        if parent:
            snippet = parent.get_text(" ", strip=True).replace(title_text, "").strip()
        if not snippet:
            sib = a.find_next_sibling(text=True)
            if sib:
                snippet = str(sib).strip()
        if not snippet:
            snippet = title_text

        # verify url liveness, but be lenient for listing pages (200-399)
        if url_is_alive(url_norm):
            entry = {"title": title_text, "url": url_norm, "snippet": snippet}
            if doc_type_label:
                entry["doc_type"] = doc_type_label
            candidates.append(entry)
            seen_urls.add(url_norm)
        if len(candidates) >= max_candidates:
            break

    # If doc_type filter provided, perform case-insensitive filter on doc_type label and also try to match common words
    if doc_type and candidates:
        doc_type_l = doc_type.lower()
        filtered = []
        for c in candidates:
            label = c.get("doc_type","").lower()
            if doc_type_l in label or doc_type_l in c["title"].lower() or doc_type_l in c["snippet"].lower():
                filtered.append(c)
        if filtered:
            candidates = filtered

    return candidates

def make_envelope(model: str, user_text: str, candidates: list | None = None) -> dict:
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
    if candidates:
        envelope["candidates"] = candidates
    return envelope

def extract_json_array_from_text(s: str):
    # tolerant extraction: look for first '[' ... last ']' and parse
    s = s.strip()
    if s == "I can only search AGRI committee documents; no matching documents found.":
        return "FALLBACK", None
    try:
        j = json.loads(s)
        if isinstance(j, list):
            return j, None
    except Exception:
        pass
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
    doc_type = body.get("doc_type")  # optional, e.g., "RECOMMENDATION" or "DRAFT"

    if not user_text or not isinstance(user_text, str):
        return jsonify({"error": "missing or invalid text field"}), 400

    # Fetch candidates by searching for the user query, optionally filtering by doc_type
    candidates = search_agri(user_text, doc_type=doc_type)

    # If the search found nothing, return the friendly response immediately
    if not candidates:
        app.logger.info("search_agri returned no candidates for query=%s doc_type=%s", user_text, doc_type)
        return jsonify({"reply": "No documents found", "matches": []}), 200

    app.logger.info("search_agri returned %d candidates for query=%s", len(candidates), user_text)

    envelope = make_envelope(MODEL, user_text, candidates=candidates)
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

    reply_field = resp_json.get("reply")
    raw_field = resp_json.get("raw")
    candidate_value = reply_field if reply_field is not None else raw_field
    if candidate_value is None:
        return jsonify({"error":"worker response missing 'reply' and 'raw'","worker_response":resp_json}), 502

    # Normalize to string for tolerant extraction
    if not isinstance(candidate_value, str):
        try:
            candidate_value = json.dumps(candidate_value)
        except Exception:
            candidate_value = str(candidate_value)

    extracted, err = extract_json_array_from_text(candidate_value)
    if extracted == "FALLBACK":
        return jsonify({"reply": "No documents found", "matches": []}), 200
    if extracted is None:
        app.logger.warning("Worker reply could not be parsed as JSON array: %s", err)
        return jsonify({"error":"invalid worker reply","detail":err,"worker_response":candidate_value}), 502

    ok, v_err = validate_matches_array(extracted)
    if not ok:
        app.logger.warning("Worker returned array but validation failed: %s", v_err)
        return jsonify({"error":"invalid worker reply","detail":v_err,"worker_response":extracted}), 502

    if len(extracted) == 0:
        return jsonify({"reply":"No documents found","matches":[]}), 200

    return jsonify({"reply":"matches","matches":extracted}), 200
