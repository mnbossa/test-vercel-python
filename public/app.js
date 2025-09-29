document.getElementById("go").addEventListener("click", async () => {
  const text = document.getElementById("text").value;
  const out = document.getElementById("out");
  out.textContent = "Sending…";
  try {
    const res = await fetch("/api/proxy", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ text })
    });
    const j = await res.json();
    if (!res.ok) throw new Error(j.error || JSON.stringify(j));
    out.textContent = j.reply ?? "No reply field in response";
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
});


