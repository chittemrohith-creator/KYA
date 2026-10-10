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
        try {
          const j = await fetch("/api/search?q=" + encodeURIComponent(q)).then(r => r.json());
          resEl.innerHTML = (j.results || []).slice(0, 6)
            .map(x => `<a href="${x.url}" style="display:block;padding:.3rem .6rem">${x.type}: ${x.title}</a>`).join("");
        } catch (e) { /* network failure: leave suggestions empty */ }
      }, 250);
    });
  }

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Scroll reveal (progressive enhancement; content visible without JS via .reveal fallback CSS)
  if (!reduceMotion && "IntersectionObserver" in window) {
    const io = new IntersectionObserver(entries => {
      entries.forEach(en => { if (en.isIntersecting) { en.target.classList.add("visible"); io.unobserve(en.target); } });
    }, { threshold: 0.12 });
    document.querySelectorAll(".reveal").forEach(el => {
      el.classList.add("reveal-pending");
      io.observe(el);
    });
  } else {
    document.querySelectorAll(".reveal").forEach(el => el.classList.add("visible"));
  }

  // Hero pointer parallax — decorative only; disabled for reduced motion and touch.
  const hero = document.querySelector("[data-parallax]");
  if (hero && !reduceMotion && window.matchMedia("(hover: hover)").matches) {
    let raf = null;
    hero.addEventListener("pointermove", ev => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        const rect = hero.getBoundingClientRect();
        const px = ((ev.clientX - rect.left) / rect.width - 0.5) * 2;   // -1..1
        const py = ((ev.clientY - rect.top) / rect.height - 0.5) * 2;
        hero.style.setProperty("--px", px.toFixed(3));
        hero.style.setProperty("--py", py.toFixed(3));
        raf = null;
      });
    });
    hero.addEventListener("pointerleave", () => {
      hero.style.setProperty("--px", "0");
      hero.style.setProperty("--py", "0");
    });
  }

  // Gentle card tilt — subtle 3D depth on interactive cards only.
  if (!reduceMotion && window.matchMedia("(hover: hover)").matches) {
    document.querySelectorAll(".tiltable").forEach(card => {
      card.addEventListener("pointermove", ev => {
        const rect = card.getBoundingClientRect();
        const rx = (((ev.clientY - rect.top) / rect.height) - 0.5) * -3;
        const ry = (((ev.clientX - rect.left) / rect.width) - 0.5) * 3;
        card.style.transform = `translateY(-4px) rotateX(${rx.toFixed(2)}deg) rotateY(${ry.toFixed(2)}deg)`;
      });
      card.addEventListener("pointerleave", () => { card.style.transform = ""; });
    });
  }
});
