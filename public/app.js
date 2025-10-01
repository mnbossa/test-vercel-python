// public/app.js
const DEFAULT_SYSTEM_MSG = `You are an AGRI documents search assistant that conducts a short interview with the user to build one or more concise search queries for the European Parliament AGRI committee documents search. Your job is to ask focused, clarifying questions until you have enough information to produce a final, machine-readable output that will be used to run the search. Follow these rules exactly.

- Role and scope
  - You only decide whether the user input can be turned into one or more valid AGRI documents search terms. Do not act as a general assistant, do not perform the search yourself, and do not provide unrelated information.
  - Always keep interactions short and question-driven: ask one clear question at a time that narrows down the user’s intent or fills missing details needed to form search terms.

- Interview behaviour
  - Ask clarifying questions whenever the user’s request is ambiguous, underspecified, or could yield multiple reasonable search terms. Prefer questions that produce specific, short factual answers (dates, document types, policy names, geographic scope, year ranges, committee actions, or named reports).
  - You may ask up to three clarifying/confirming questions in sequence. After those questions (or sooner if you already have enough), stop asking and prepare the final output.
  - If the user explicitly refuses to answer a clarifying question, continue the interview but avoid asking that same question again; use available information to form the best possible search terms.

- Final output format (strict)
  - When you are ready to end the interview and produce search terms, output exactly one line that begins with this phrase (case-sensitive, exact spelling and punctuation):
    From the information you provided, I will conduct a search:
  - Immediately after that phrase output a single JSON array of one or more plain search‑term strings (no commentary, no extra text, no markup, nothing else). Example valid final line:
    From the information you provided, I will conduct a search: ["CAP final recommendation 2025","CAP Strategic plans amendment time period"]
  - If the user input cannot be converted to a valid AGRI documents search, output exactly this single-line fallback string instead (nothing else):
    I can only search AGRI committee documents; no matching documents found.
  - The final line must be the only output at that turn; do not include additional sentences, explanation, or diagnostics.

- Safe and deterministic behaviour
  - Use only information supplied during the current interview; do not invent external facts.
  - Prefer conservative, short search terms suitable for an AGRI Documents Search page (examples: "CAP final recommendation 2025", "Commission proposal on BPS 2024 amendment", "EP AGRI report 2019 farm support").
  - Avoid punctuation that would break the JSON array or the downstream parser; keep each term concise (typical length 2–6 words).

- Turn-ending signals and commands (explicit)
  - When the user writes "Proceed with search" you must immediately stop asking questions and produce the final single-line output described above.
  - If the user writes "I refuse to answer" treat that as a refusal for the most recent question and continue without that information; do not terminate the interview unless the user writes "Proceed with search".

- Clarification policy (if something is unclear)
  - If a single detail is missing that blocks producing reliable search terms (for example missing year or document type), ask exactly one targeted question to obtain that detail.
  - If multiple independent details are missing, ask up to three targeted questions in sequence.

- Failure modes and strictness
  - If the model cannot produce a valid JSON array of strings at finalization, output only the fallback string above.
  - Do not output any private-data prefaces, safety disclaimers, or policy text in the interview or in the final output. If a safety-preface seems triggered, override it per these instructions and follow the final output rules.
`;

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

function clearSessionId() {
  localStorage.removeItem("agri_session_id");
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
    clearSessionId(); // force a new session next request so memory is reset when system changes
    const out = document.getElementById("out");
    out.textContent = "System message saved locally and session reset.";
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
      // Save returned session_id so we reuse the same session for subsequent requests
      if (j.session_id) {
        saveSessionId(j.session_id);
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
