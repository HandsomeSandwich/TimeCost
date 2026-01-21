document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("menuBtn");
  const drawer = document.getElementById("navDrawer");
  if (!btn || !drawer) return;

  function setOpen(open) {
    drawer.classList.toggle("open", open);
    btn.setAttribute("aria-expanded", String(open));
    document.body.classList.toggle("nav-open", open);
  }

  btn.addEventListener("click", () => {
    const open = !drawer.classList.contains("open");
    setOpen(open);
  });

  // Close on Escape
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") setOpen(false);
  });

  // Close when clicking outside the drawer and button
  document.addEventListener("click", (e) => {
    if (!drawer.classList.contains("open")) return;
    const target = e.target;
    if (!(target instanceof Element)) return;
    if (drawer.contains(target) || btn.contains(target)) return;
    setOpen(false);
  });

  // Close when clicking a link in the drawer
  drawer.addEventListener("click", (e) => {
    const target = e.target;
    if (!(target instanceof Element)) return;
    if (target.closest("a")) setOpen(false);
  });
});
