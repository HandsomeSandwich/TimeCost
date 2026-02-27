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

function syncResponsiveInputs() {
  const isMobile = window.matchMedia("(max-width: 720px)").matches;

  // Disable inputs in the hidden layout so they don't submit duplicate arrays
  document.querySelectorAll(".desktop-only input, .desktop-only select, .desktop-only textarea")
    .forEach(el => el.disabled = isMobile);

  document.querySelectorAll(".mobile-only input, .mobile-only select, .mobile-only textarea")
    .forEach(el => el.disabled = !isMobile);
}

window.addEventListener("DOMContentLoaded", syncResponsiveInputs);
window.addEventListener("resize", syncResponsiveInputs);

// PWA Install Prompt
let deferredPrompt;
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredPrompt = e;
  console.log('Install prompt ready');
});

// --- Share result button ---
function showToast(msg) {
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2200);
}

const shareBtn = document.getElementById('shareResult');
if (shareBtn) {
  shareBtn.addEventListener('click', async () => {
    const text = shareBtn.dataset.text;
    const url = shareBtn.dataset.url;
    const full = text + ' \u2014 ' + url;

    if (navigator.share) {
      try {
        await navigator.share({ text: full });
        return;
      } catch (_) { /* user cancelled or not supported */ }
    }

    // Clipboard fallback
    try {
      await navigator.clipboard.writeText(full);
      showToast(shareBtn.dataset.copied || 'Copied!');
    } catch (_) {
      // execCommand fallback for restricted contexts
      try {
        const ta = document.createElement('textarea');
        ta.value = full;
        ta.style.cssText = 'position:fixed;opacity:0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
        showToast(shareBtn.dataset.copied || 'Copied!');
      } catch (_) {
        showToast('Could not copy');
      }
    }
  });
}
