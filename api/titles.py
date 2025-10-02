# server/app.py
from flask import Flask, jsonify, make_response
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


@app.route("/titles", methods=["GET"])
def titles():
    try:
        items = fetch_titles()
        return make_response(jsonify(items), 200)
    except Exception as e:
        # keep error message minimal for production; log full details server-side
        app.logger.exception("Failed to fetch titles")
        return make_response(jsonify([]), 200)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
