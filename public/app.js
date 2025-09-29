document.getElementById("go").addEventListener("click", async () => {
  const text = document.getElementById("text").value;
  const out = document.getElementById("out");
  out.innerHTML = "Searching…";
  try {
    const res = await fetch("/api/proxy", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ text })
    });
    const j = await res.json();
    if (!res.ok) throw new Error(j.error || JSON.stringify(j));
    if (j.matches && j.matches.length > 0) {
      // Render each match with Title bold
      out.innerHTML = "";
      j.matches.forEach(m => {
        const div = document.createElement("div");
        div.style.marginBottom = "1rem";
        // Title bold: Title: <title>
        const title = document.createElement("div");
        title.innerHTML = "<strong>Title:</strong> " + escapeHtml(m.title);
        div.appendChild(title);
        const url = document.createElement("div");
        url.innerHTML = "<strong>URL:</strong> <a href=\"" + escapeHtml(m.url) + "\" target=\"_blank\">" + escapeHtml(m.url) + "</a>";
        div.appendChild(url);
        const snippet = document.createElement("div");
        snippet.innerHTML = "<strong>Snippet:</strong> " + escapeHtml(m.snippet);
        div.appendChild(snippet);
        const mt = document.createElement("div");
        mt.innerHTML = "<strong>Matched_terms:</strong> " + escapeHtml(Array.isArray(m.matched_terms) ? m.matched_terms.join(", ") : m.matched_terms);
        div.appendChild(mt);
        out.appendChild(div);
      });
    } else if (j.reply && typeof j.reply === "string") {
      out.innerHTML = escapeHtml(j.reply);
    } else {
      out.innerHTML = "No results";
    }
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
});

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, function (m) {
    return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":"&#39;"}[m]);
  });
}


