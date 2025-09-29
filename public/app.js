document.getElementById("go").addEventListener("click", async () => {
  const n = document.getElementById("num").value;
  const out = document.getElementById("out");
  out.textContent = "Computing…";
  try {
    const res = await fetch("/api/test", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ n })
    });
    const j = await res.json();
    if (!res.ok) throw new Error(j.error || JSON.stringify(j));
    out.textContent = "Result: " + j.result;
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
});

