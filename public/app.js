// public/app.js
const DEFAULT_SYSTEM_MSG = `You are an AGRI documents search assistant that only decides whether a user input is a valid search request for European Parliament AGRI committee documents. Do not act as a general assistant. If the input is NOT a valid AGRI documents search, output exactly this single-line string (no quotes, no extra whitespace, nothing else):
I can only search AGRI committee documents; no matching documents found.
If the input IS a valid AGRI documents search, output only a JSON array of one or more plain search-term strings (only the array, nothing else). Each element must be a short query string suitable to run on the AGRI Documents Search page (for example: "CAP final recommendation 2025", "CAP Strategic plans amendment time period"). Do not output explanation, markup, reasoning, or any text outside the JSON array. The proxy will reject any output that is not exactly the fallback string or a JSON array of strings.`;

function loadSessionId() {
  let sid = localStorage.getItem("agri_session_id");
  if (!sid) {
    sid = null; // proxy will generate one on first request
  }
  return sid;
}

function saveSessionId(sid) {
  if (sid) localStorage.setItem("agri_session_id", sid);
}

function setSystemTextarea(val) {
  const el = document.getElementById("system_msg");
  if (el) el.value = val;
}

function loadSystemMsg() {
  const stored = localStorage.getItem("agri_system_msg");
  setSystemTextarea(stored || DEFAULT_SYSTEM_MSG);
}

function saveSystemMsg() {
  const val = document.getElementById("system_msg").value;
  localStorage.setItem("agri_system_msg", val);
  console.info("Saved system message to localStorage (starts):", val.slice(0,200));
}

function resetSystemMsg() {
  localStorage.removeItem("agri_system_msg");
  setSystemTextarea(DEFAULT_SYSTEM_MSG);
}

document.addEventListener("DOMContentLoaded", () => {
  loadSystemMsg();

  document.getElementById("save_sys").addEventListener("click", () => {
    saveSystemMsg();
    const out = document.getElementById("out");
    out.textContent = "System message saved locally.";
  });

  document.getElementById("reset_sys").addEventListener("click", () => {
    resetSystemMsg();
    const out = document.getElementById("out");
    out.textContent = "System message reset to default.";
  });

  const go = document.getElementById("go");
  go.addEventListener("click", async () => {
    const qEl = document.getElementById("q");
    // const docTypeEl = document.getElementById("doc_type");
    const debugEl = document.getElementById("debug");
    const out = document.getElementById("out");

    if (!qEl) {
      out.textContent = "Client error: missing input element #q";
      return;
    }
    const q = qEl.value;
    if (!q || q.trim().length === 0) {
      out.textContent = "Please enter a query.";
      return;
    }
    // const doc_type = docTypeEl ? docTypeEl.value || undefined : undefined;
    const debug = debugEl ? debugEl.checked : false;
    // include system_msg if user saved one (prefer local value)
    const system_msg = document.getElementById("system_msg").value || undefined;
    const session_id = loadSessionId();

    out.textContent = "Sending request… (check console and Network tab)";
    // console.info("UI: sending search", { q, doc_type, debug, has_system: !!system_msg });
    console.info("UI: sending search", { q, debug, has_system: !!system_msg });

    try {
      const payload = { text: q, debug };
      // const payload = { text: q, doc_type, debug };
      if (session_id) payload.session_id = session_id;
      if (system_msg) payload.system_msg = system_msg;
      const res = await fetch("/api/proxy", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
      });
      console.info("Fetch completed", { ok: res.ok, status: res.status });
      const text = await res.text();
      console.info("Raw response text:", text.slice(0, 2000));
      let j = null;
      try {
        j = JSON.parse(text);
      } catch (e) {
        console.error("Response is not JSON", e, text.slice(0,2000));
        out.textContent = "Non-JSON response. See console for raw body.";
        return;
      }
      if (!res.ok) {
        console.error("Server returned error", j);
        out.textContent = "Server error: " + (j.error || JSON.stringify(j));
        return;
      }
      if (j.matches && j.matches.length) {
        out.textContent = JSON.stringify(j.matches, null, 2);
      } else {
        out.textContent = j.reply || "No documents found";
      }
      if (j.debug_info) console.info("Debug info from server:", j.debug_info);
    } catch (err) {
      console.error("Fetch failed", err);
      out.textContent = "Fetch failed: " + (err.message || String(err));
    }
  });
});
