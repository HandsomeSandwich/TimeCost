document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("menuBtn");
  const drawer = document.getElementById("navDrawer");

  if (!btn || !drawer) return;

  function openMenu() {
    drawer.classList.add("open");
    btn.setAttribute("aria-expanded", "true");
    document.body.classList.add("nav-open");
  }

  function closeMenu() {
    drawer.classList.remove("open");
    btn.setAttribute("aria-expanded", "false");
    document.body.classList.remove("nav-open");
  }

  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const isOpen = drawer.classList.contains("open");
    isOpen ? closeMenu() : openMenu();
  });

  document.addEventListener("click", (e) => {
    if (
      drawer.classList.contains("open") &&
      !drawer.contains(e.target) &&
      !btn.contains(e.target)
    ) {
      closeMenu();
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && drawer.classList.contains("open")) {
      closeMenu();
      btn.focus();
    }
  });
});

const cost = document.getElementById("itemCost");
if (cost) {
  cost.addEventListener("focus", () => cost.select());
}

body[data-view="dusk"] {
  --bg: #0f1a14;
  --panel: #1e1f1c;
  --border: #2a332d;

  --text: #e6e9e5;
  --muted: #9aa59a;

  --accent: #4a7a66;
  --accent-soft: rgba(74, 122, 102, 0.18);

  --btn2: #202623;
}
