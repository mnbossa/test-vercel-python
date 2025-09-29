import os
import time
import json
import hmac
import hashlib
import secrets
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify

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

def scrape_agri_latest(max_candidates: int = 40) -> list:
    """
    Scrape AGRI latest-documents listing for title + URL + short snippet.
    Return sanitized candidates list ready to include in envelope.
    """
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(AGRI_DOCS_INDEX, timeout=HTTP_TIMEOUT, headers=headers)
        r.raise_for_status()
    except requests.RequestException as e:
        app.logger.warning("Failed to fetch AGRI index: %s", e)
        return []

    soup = BeautifulSoup(r.text, "html.parser")

    # Heuristic: the page lists documents as links with document IDs and PDF/DOC links.
    # Find anchor tags inside the documents listing area.
    candidates = []
    seen_urls = set()

    # Narrow scope: search for list-like containers and anchors
    # This is robust to small markup changes: we look for anchors with '/doceo/document' or anchors under the documents listing block.
    anchors = soup.find_all("a", href=True)
    for a in anchors:
        href = a["href"].strip()
        title_text = a.get_text(separator=" ", strip=True)
        # Skip empty text anchors
        if not title_text:
            continue
        # Only consider links that look like document pages or files
        if "/doceo/document" not in href and "/documents/" not in href and not href.lower().endswith((".pdf", ".doc", ".docx")):
            continue
        url_norm = normalize_url(href)
        if not url_norm or url_norm in seen_urls:
            continue
        # Try to get a short snippet: the anchor's parent text or nearby sibling text
        snippet = ""
        parent = a.parent
        if parent:
            snippet = parent.get_text(separator=" ", strip=True).replace(title_text, "").strip()
        if not snippet:
            # try next sibling
            sib = a.find_next_sibling(text=True)
            if sib:
                snippet = str(sib).strip()
        if not snippet:
            snippet = title_text

        # Check URL liveness for documents that are absolute or file-like
        if url_is_alive(url_norm):
            candidates.append({"title": title_text, "url": url_norm, "snippet": snippet})
            seen_urls.add(url_norm)
        # stop when we have enough
        if len(candidates) >= max_candidates:
            break

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
    if not user_text or not isinstance(user_text, str):
        return jsonify({"error": "missing or invalid text field"}), 400

    # Try to fetch candidates from AGRI index
    candidates = scrape_agri_latest()
    if not candidates:
        app.logger.warning("No candidates scraped from AGRI index; proceeding without candidates")
    else:
        app.logger.info("Scraped %d candidate documents from AGRI index", len(candidates))

    envelope = make_envelope(MODEL, user_text, candidates=candidates if candidates else None)
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
