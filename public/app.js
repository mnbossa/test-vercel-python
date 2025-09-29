document.addEventListener("DOMContentLoaded", () => {
  const go = document.getElementById("go");
  const out = document.getElementById("out");

  if (!go) {
    console.error("Missing #go button");
    return;
  }

  go.addEventListener("click", async () => {
    const qEl = document.getElementById("q");
    const docTypeEl = document.getElementById("doc_type");

    if (!qEl) {
      out.textContent = "Client error: missing input element #q";
      console.error("Missing element #q");
      return;
    }

    const q = qEl.value;
    const doc_type = docTypeEl ? docTypeEl.value || undefined : undefined;

    out.textContent = "Sending request… (check console and Network tab)";
    console.info("UI: sending search", { q, doc_type });

    try {
      const res = await fetch("/api/proxy", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ text: q, doc_type, debug: true })
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
      if (j.debug_info) {
        console.info("Debug info from server:", j.debug_info);
      }
    } catch (err) {
      console.error("Fetch failed", err);
      out.textContent = "Fetch failed: " + (err.message || String(err));
    }
  });
});

// document.getElementById("go").addEventListener("click", async () => {
//   const text = document.getElementById("text").value;
//   const out = document.getElementById("out");
//   out.innerHTML = "Searching…";
//   try {
//     const res = await fetch("/api/proxy", {
//       method: "POST",
//       headers: {"Content-Type": "application/json"},
//       body: JSON.stringify({ text })
//     });
//     const j = await res.json();
//     if (!res.ok) throw new Error(j.error || JSON.stringify(j));
//     if (j.matches && j.matches.length > 0) {
//       // Render each match with Title bold
//       out.innerHTML = "";
//       j.matches.forEach(m => {
//         const div = document.createElement("div");
//         div.style.marginBottom = "1rem";
//         // Title bold: Title: <title>
//         const title = document.createElement("div");
//         title.innerHTML = "<strong>Title:</strong> " + escapeHtml(m.title);
//         div.appendChild(title);
//         const url = document.createElement("div");
//         url.innerHTML = "<strong>URL:</strong> <a href=\"" + escapeHtml(m.url) + "\" target=\"_blank\">" + escapeHtml(m.url) + "</a>";
//         div.appendChild(url);
//         const snippet = document.createElement("div");
//         snippet.innerHTML = "<strong>Snippet:</strong> " + escapeHtml(m.snippet);
//         div.appendChild(snippet);
//         const mt = document.createElement("div");
//         mt.innerHTML = "<strong>Matched_terms:</strong> " + escapeHtml(Array.isArray(m.matched_terms) ? m.matched_terms.join(", ") : m.matched_terms);
//         div.appendChild(mt);
//         out.appendChild(div);
//       });
//     } else if (j.reply && typeof j.reply === "string") {
//       out.innerHTML = escapeHtml(j.reply);
//     } else {
//       out.innerHTML = "No results";
//     }
//   } catch (err) {
//     out.textContent = "Error: " + err.message;
//   }
// });

// function escapeHtml(s) {
//   return String(s).replace(/[&<>"']/g, function (m) {
//     return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":"&#39;"}[m]);
//   });
// }


