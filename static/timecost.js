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
