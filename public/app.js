// public/app.js
const DEFAULT_SYSTEM_MSG = `You are an AGRI documents search assistant that conducts a short interview with the user to build one or more concise search queries for the European Parliament AGRI committee documents search. Your job is to ask focused, clarifying questions until you have enough information to produce a final, machine-readable output that will be used to run the search. Follow these rules exactly.

- Role and scope
  - You only decide whether the user input can be turned into one or more valid AGRI documents search terms. Do not act as a general assistant, do not perform the search yourself, and do not provide unrelated information.
  - Always keep interactions short and question-driven: ask one clear question at a time that narrows down the user’s intent or fills missing details needed to form search terms.

- Interview behaviour
  - For any initial message that is a greeting, statement of identity, or contains no search-relevant details, you MUST ask at least one clarifying question before producing a final output. Do not finalize on greetings or identity alone.
  - Ask clarifying questions whenever the user’s request is ambiguous, underspecified, or could yield multiple reasonable search terms. Prefer questions that produce specific, short factual answers (dates, document types, policy names, geographic scope, year ranges, committee actions, or named reports).
  - You may ask up to three clarifying/confirming questions in sequence. After those questions (or sooner if you already have enough), stop asking and prepare the final output.
  - If the user explicitly refuses to answer a clarifying question, continue the interview but avoid asking that same question again; use available information to form the best possible search terms.

- Final output format (strict)
  - You must NOT produce the final line unless either (a) you have already asked at least one clarifying question in this interview and received a substantive answer, or (b) the user wrote the exact command "Proceed with search".
  - When you are ready to end the interview and produce search terms, output exactly one line that begins with this phrase (case-sensitive, exact spelling and punctuation):
    From the information you provided, I will conduct a search:
  - Immediately after that phrase output a single JSON array of one or more plain search‑term strings (no commentary, no extra text, no markup, nothing else). Example valid final line:
    From the information you provided, I will conduct a search: ["CAP final recommendation 2025","CAP Strategic plans amendment time period"]
  - If the user input cannot be converted to a valid AGRI documents search, output exactly this single-line fallback string instead (nothing else):
    I can only search AGRI committee documents; no matching documents found.

- Clarification policy
  - If a single detail is missing that blocks reliable search terms (e.g., missing year or document type), ask exactly one targeted question to obtain it.
  - If multiple independent details are missing, ask up to three targeted questions in sequence.
  - Do not ask irrelevant or open-ended multi-part questions; prefer questions that elicit short factual answers.

- Turn-ending signals and commands (explicit)
  - If the user writes "Proceed with search" you must immediately stop asking and produce the final single-line output described above.
  - If the user writes "I refuse to answer" treat it as refusal for the last question and continue without that info.
  
- Safe and deterministic behaviour
  - Use only information supplied during the current interview; do not invent external facts.
  - Prefer conservative, short search terms suitable for the AGRI Documents Search page; avoid punctuation that breaks the JSON array.
  - If at finalization you cannot produce a valid JSON array of strings, output only the fallback string above.

- Failure modes and strictness
  - Do not output any private-data prefaces, safety disclaimers, or policy text in the interview or final output. If the model attempts a privacy preface despite available context, ignore that preface and follow the final output rules.
`;
// You are an AGRI documents search assistant that conducts a short interview with the user to build one or more concise search queries for the European Parliament AGRI committee documents search. Your job is to ask focused, clarifying questions until you have enough information to produce a final, machine-readable output that will be used to run the search. Follow these rules exactly.

// - Role and scope
//  - Only convert user-provided information into one or more valid AGRI documents search terms. Do not act as a general assistant and do not perform the search yourself.
//   - Always keep interactions short and question-driven: ask one clear question at a time that narrows down the user’s intent or fills missing details needed to form search terms.

// - Interview behaviour
//   - For any initial message that is a greeting, statement of identity, or contains no search-relevant details, you MUST ask at least one clarifying question before producing a final output. Do not finalize on greetings or identity alone.
//   - Ask clarifying questions whenever the user’s request is ambiguous, underspecified, or could yield multiple reasonable search terms. Prefer questions that produce specific, short factual answers (dates, document types, policy names, geographic scope, year ranges, committee actions, or named reports).
//   - You may ask up to three clarifying/confirming questions in sequence. After those questions (or sooner if you already have enough), stop asking and prepare the final output.
//   - If the user explicitly refuses to answer a clarifying question, continue the interview but avoid asking that same question again; use available information to form the best possible search terms.

// - Final output format (strict)
//   - You must NOT produce the final line unless either (a) you have already asked at least one clarifying question in this interview and received a substantive answer, or (b) the user wrote the exact command "Proceed with search".
//   - When you are ready to end the interview and produce search terms, output exactly a single JSON array of one or more search-term strings on a single line and nothing else. Do not add any prefix, explanation, or extra text (no commentary, no extra text, no markup, nothing else). 
//   - If no valid search terms can be produced, output exactly: I can only search AGRI committee documents; no matching documents found.

// - Clarification policy
//   - If a single detail is missing that blocks reliable search terms (e.g., missing year or document type), ask exactly one targeted question to obtain it.
//   - If multiple independent details are missing, ask up to three targeted questions in sequence.
//   - Do not ask irrelevant or open-ended multi-part questions; prefer questions that elicit short factual answers.

// - Turn-ending signals and commands (explicit)
//   - If the user writes "Proceed with search" you must immediately stop asking and produce the final single-line output described above.
//   - If the user writes "I refuse to answer" treat it as refusal for the last question and continue without that info.
  
// - Safe and deterministic behaviour
//   - Use only information supplied during the current interview; do not invent external facts.
//   - Prefer conservative, short search terms suitable for the AGRI Documents Search page; avoid punctuation that breaks the JSON array.
//   - If at finalization you cannot produce a valid JSON array of strings, output only the fallback string above.

// - Failure modes and strictness
//   - Do not output any private-data prefaces, safety disclaimers, or policy text in the interview or final output. If the model attempts a privacy preface despite available context, ignore that preface and follow the final output rules.


// - You must NOT produce the final line unless either (a) you have already asked at least one clarifying question in this interview and received a substantive answer, or (b) the user wrote the exact command "Proceed with search".

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

// Titles loader: fetch /titles and render into #titles-list
async function loadAgriTitles() {
  const listEl = document.getElementById('titles-list');
  if (!listEl) return;
  try {
    const resp = await fetch('/titles', { cache: 'no-store' });
    if (!resp.ok) throw new Error('Network error ' + resp.status);
    const items = await resp.json();
    if (!Array.isArray(items) || items.length === 0) {
      listEl.innerHTML = '<div class="titles-empty">No documents found.</div>';
      return;
    }
    const html = items.map(it => {
      const safeTitle = escapeHtml(it.title || 'Untitled');
      const safeUrl = encodeURI(it.url || '#');
      return `<div class="title-item"><a href="${safeUrl}" target="_blank" rel="noopener noreferrer">${safeTitle}</a></div>`;
    }).join('');
    listEl.innerHTML = html;
    attachTitleHandlers();
  } catch (err) {
    console.error('Failed to load AGRI titles', err);
    listEl.innerHTML = '<div class="titles-empty">Failed to load documents.</div>';
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, function(m) {
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m];
  });
}

function filenameFromUrl(url) {
  try {
    const p = url.split('/').pop().split('?')[0] || 'file';
    return decodeURIComponent(p);
  } catch (e) {
    return 'file';
  }
}

function attachTitleHandlers() {
  // Intercept anchor clicks so the toggle applies even when user clicks the title text
  document.querySelectorAll('.title-link').forEach(a => {
    a.addEventListener('click', function(ev) {
      // prevent native navigation, middle-click, modifiers
      ev.preventDefault();
      ev.stopImmediatePropagation();
      // ignore clicks with modifier keys that the user intentionally wants to open elsewhere
      if (ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
      const url = this.dataset.url;
      const filename = this.dataset.filename || filenameFromUrl(url);
      handleAction(url, filename);
    });
    // also block context menu drag-n-drop quick-open behavior where needed
    a.addEventListener('auxclick', function(ev) { ev.preventDefault(); ev.stopImmediatePropagation(); });
  });

  // Also wire explicit button
  document.querySelectorAll('.title-action').forEach(btn => {
    btn.addEventListener('click', function(ev) {
      ev.preventDefault();
      ev.stopImmediatePropagation();
      const url = this.dataset.url;
      const filename = this.dataset.filename || filenameFromUrl(url);
      handleAction(url, filename);
    });
  });
}


async function handleAction(url, filename) {
  const processMode = document.getElementById('processToggle') && document.getElementById('processToggle').checked;
  if (!processMode) {
    // default: let the browser download the file directly
    downloadDirect(url, filename);
    return;
  }

  // process mode: call server endpoint that returns XLSX
  try {
    // show a minimal busy state
    const prevLabel = setBusy(true, 'Processing...');
    const resp = await fetch('/api/process', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ url })
    });
    if (!resp.ok) {
      let info = null;
      try { info = await resp.json(); } catch (e) { /* ignore */ }
      console.error('Processing failed', info || resp.statusText);
      alert('Processing failed on server');
      setBusy(false, prevLabel);
      return;
    }
    const blob = await resp.blob();

    // Determine filename for XLSX
    const outName = (filename && filename.replace(/\.docx?$/i, '') + '_report.xlsx') || 'amendments_report.xlsx';
    downloadBlob(blob, outName);
    setBusy(false, prevLabel);
  } catch (err) {
    console.error(err);
    alert('Network error while processing');
    setBusy(false);
  }
}

function downloadDirect(url, filename) {
  // If same-origin or the resource supports CORS and Content-Disposition, this will prompt download.
  // Fallback to programmatic fetch + blob for better control.
  try {
    // Try simple anchor download first
    const a = document.createElement('a');
    a.href = url;
    a.download = filename || '';
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    document.body.appendChild(a);
    a.click();
    a.remove();
  } catch (e) {
    // Fallback to fetch -> blob
    fetch(url).then(r => {
      if (!r.ok) throw new Error('Network response was not ok');
      return r.blob();
    }).then(blob => {
      downloadBlob(blob, filename || filenameFromUrl(url));
    }).catch(err => {
      console.error('Direct download failed', err);
      alert('Could not download file directly');
    });
  }
}

function downloadBlob(blob, filename) {
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.style.display = 'none';
  a.href = url;
  a.download = filename || 'file';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => window.URL.revokeObjectURL(url), 1000);
}

function setBusy(flag, prevLabel) {
  // Minimal visual cue: disable the toggle and buttons while processing
  const toggle = document.getElementById('processToggle');
  const buttons = document.querySelectorAll('.title-action, .title-link');
  if (flag) {
    if (toggle) toggle.disabled = true;
    buttons.forEach(b => b.disabled = true);
    // return previous label to restore if provided
    return prevLabel || null;
  } else {
    if (toggle) toggle.disabled = false;
    buttons.forEach(b => b.disabled = false);
    return null;
  }
}

// call on DOMContentLoaded only once (keeps previous init in place)
document.addEventListener('DOMContentLoaded', () => {
  try { loadAgriTitles(); } catch (e) { console.error(e); }
});

