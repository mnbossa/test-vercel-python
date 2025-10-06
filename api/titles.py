# server/app.py
from flask import Flask, jsonify, make_response, request
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

app = Flask(__name__)

TARGET = "https://www.europarl.europa.eu/committees/es/agri/documents/latest-documents"

USER_AGENT = "agri-scraper/1.0"


def fetch_titles():
    """
    Scrape TARGET for .docx links and the last preceding <span class="t-item"> title.
    Returns a list of dicts: [{"title": "...", "url": "https://..."}, ...]
    """
    resp = requests.get(TARGET, headers={"User-Agent": USER_AGENT}, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    # Find all anchor tags that link to a .docx file
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".docx"):
            full_url = urljoin(TARGET, href)
            # Find the last preceding <span class="t-item"> before this anchor
            title_span = None
            node = a
            # walk backwards through previous siblings up the tree to find last t-item
            while node:
                # check previous siblings
                prev = node.previous_sibling
                while prev:
                    try:
                        # if it's a Tag, check for span.t-item inside it
                        if getattr(prev, "find_all", None):
                            span = prev.find("span", class_="t-item")
                            if span and span.get_text(strip=True):
                                title_span = span
                                break
                            # also check if prev itself is the desired span
                            if prev.name == "span" and "t-item" in (prev.get("class") or []):
                                title_span = prev
                                break
                    except Exception:
                        pass
                    prev = prev.previous_sibling
                if title_span:
                    break
                # move up
                node = node.parent

            title_text = title_span.get_text(strip=True) if title_span else None
            if title_text:
                results.append({"title": title_text, "url": full_url})
    # optional: dedupe by url preserving order
    seen = set()
    deduped = []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            deduped.append(r)
    return deduped


def get_titles_compact(max_items: int = 200):
    """
    Return a compact list of documents suitable to send to the filter agent.
    Each item: { "id": <int>, "index": <int>, "title": <str>, "url": <str>}
    This wraps fetch_titles() and is resilient: on error returns [].
    """
    try:
        full = fetch_titles() 
        compact = []
        for i, it in enumerate(full):
            if i >= max_items:
                break
            # it is expected to be dict with "title" and "url"
            title = it.get("title") if isinstance(it, dict) else str(it)
            url = it.get("url") if isinstance(it, dict) else ""
            compact.append({
                "id": it.get("id", i) if isinstance(it, dict) else i,
                "index": i,
                "title": (title or ""),
                "url": url or ""
            })
        return compact
    except Exception:
        # never raise here; callers will fallback if empty
        app.logger.exception("get_titles_compact failed")
        return []

@app.route("/titles", methods=["GET"])
def titles():
    try:
        items = fetch_titles()
        return make_response(jsonify(items), 200)
    except Exception as e:
        # keep error message minimal for production; log full details server-side
        app.logger.exception("Failed to fetch titles")
        return make_response(jsonify([]), 200)

@app.route("/titles/process", methods=["POST"])
def titles_process_route():
    import io, json, os, tempfile, traceback
    try:
        # robust JSON parsing
        try:
            body = request.get_json(silent=True)
            if body is None:
                raw = request.get_data(as_text=True) or ""
                try:
                    body = json.loads(raw) if raw else {}
                except Exception:
                    body = {}
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        file_url = body.get("url") or body.get("file") or body.get("uri")
        if not file_url:
            app.logger.warning("titles_process_route: missing url in request body; raw body: %s", request.get_data(as_text=True))
            return (json.dumps({"error":"missing url"}), 400, {"Content-Type":"application/json"})

        # download file into temp file
        import requests
        r = requests.get(file_url, headers={"User-Agent":"agri-processor/1.0"}, timeout=20)
        r.raise_for_status()

        fd, tmp_path = tempfile.mkstemp(suffix=".docx")
        os.close(fd)
        with open(tmp_path, "wb") as f:
            f.write(r.content)

        # import utilities inside the handler
        try:
            from utils import parse_amendments, create_amendment_report
            from utils import remove_unnec_tags, extract_additions, extract_deletions
        except Exception as e:
            tb = traceback.format_exc()
            try: os.remove(tmp_path)
            except: pass
            return (json.dumps({"error": "utils import failed", "detail": str(e)}), 500, {"Content-Type":"application/json"})

        # parse and build DataFrame
        try:
            parsed = parse_amendments(tmp_path)
            report_df = create_amendment_report(parsed)
        except Exception as e:
            tb = traceback.format_exc()
            try: os.remove(tmp_path)
            except: pass
            return (json.dumps({"error": "processing failed", "detail": str(e)}), 500, {"Content-Type":"application/json"})

        # post-process DataFrame as in your original code
        try:
            report_df['Resumen'] = ''
            report_df['Original'] = ''
            report_df['Elimina'] = ''
            report_df['Añade'] = ''
            for i, a in enumerate(parsed.get('amendments', {}).values()):
                if 'Propuesta de rechazo' in a.keys():
                    report_df.loc[i, 'Resumen'] = 'rechazada'
                else:
                    try:
                        A = remove_unnec_tags(a.get('Amended',''))
                        report_df.loc[i, 'Añade'] = extract_additions(A)
                    except Exception:
                        report_df.loc[i, 'Añade'] = "(Comprobar!!)"
                    try:
                        C = remove_unnec_tags(a.get('Original',''))
                        report_df.loc[i, 'Elimina'] = extract_deletions(C)
                        report_df.loc[i, 'Original'] = a.get('OriginalType','')
                    except Exception:
                        report_df.loc[i, 'Elimina'] = "(Comprobar!!)"
        except Exception:
            # if helpers missing or other error, continue with what you have
            pass

        # write DataFrame to XLSX bytes
        out = io.BytesIO()
        try:
            import pandas as pd
            with pd.ExcelWriter(out, engine="openpyxl") as writer:
                report_df.to_excel(writer, index=False, sheet_name="Report")
            out.seek(0)
            content = out.read()
        except Exception as e:
            try: os.remove(tmp_path)
            except: pass
            return (json.dumps({"error": "excel export failed", "detail": str(e)}), 500, {"Content-Type":"application/json"})

        try: os.remove(tmp_path)
        except: pass

        headers = {
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Content-Disposition": 'attachment; filename="amendments_report.xlsx"'
        }
        # return (headers["Content-Type"], 200, content)
        return (content, 200, headers)

    except Exception as e:
        tb = traceback.format_exc()
        app.logger.exception("titles_process_route: unhandled error")
        return (json.dumps({"error":"unhandled", "detail": str(e)}), 500, {"Content-Type":"application/json"})
    
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
