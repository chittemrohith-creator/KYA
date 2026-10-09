// CivicSync small front-end helpers (search autocomplete + JSON form posts)
async function postJSON(url, data) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return r.json();
}

document.addEventListener("DOMContentLoaded", () => {
  // Search autocomplete on /projects page
  const box = document.getElementById("searchbox");
  if (box) {
    let t;
    box.addEventListener("input", () => {
      clearTimeout(t);
      t = setTimeout(async () => {
        const q = box.value.trim();
        const resEl = document.getElementById("autocomplete");
        if (!resEl) return;
        if (q.length < 2) { resEl.innerHTML = ""; return; }
        const j = await fetch("/api/search?q=" + encodeURIComponent(q)).then(r => r.json());
        resEl.innerHTML = (j.results || []).slice(0, 6)
          .map(x => `<a href="${x.url}" style="display:block;padding:.3rem .6rem">${x.type}: ${x.title}</a>`).join("");
      }, 250);
    });
  }
});
