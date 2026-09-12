// Hagent UI helpers: quick-create modals + Ctrl/Cmd+K command palette.
// Vanilla JS, no dependencies, no build step.
(function () {
  "use strict";

  // ---- Quick-create menu + modals -----------------------------------

  var qcToggle = document.getElementById("qc-toggle");
  var qcMenu = document.getElementById("qc-menu");
  var modalOverlay = document.getElementById("modal-overlay");

  function closeQcMenu() {
    if (qcMenu) qcMenu.hidden = true;
    if (qcToggle) qcToggle.setAttribute("aria-expanded", "false");
  }

  function openModal(id) {
    if (!modalOverlay) return;
    var target = document.getElementById(id);
    if (!target) return;
    document.querySelectorAll(".modal").forEach(function (m) { m.hidden = true; });
    target.hidden = false;
    modalOverlay.hidden = false;
    var firstInput = target.querySelector("input, select");
    if (firstInput) window.setTimeout(function () { firstInput.focus(); }, 30);
  }

  function closeModal() {
    if (modalOverlay) modalOverlay.hidden = true;
    document.querySelectorAll(".modal").forEach(function (m) { m.hidden = true; });
  }

  if (qcToggle && qcMenu) {
    qcToggle.addEventListener("click", function (e) {
      e.stopPropagation();
      var willOpen = qcMenu.hidden;
      qcMenu.hidden = !willOpen;
      qcToggle.setAttribute("aria-expanded", String(willOpen));
    });
    document.addEventListener("click", function (e) {
      if (!qcMenu.hidden && !qcMenu.contains(e.target) && e.target !== qcToggle) closeQcMenu();
    });
  }

  document.querySelectorAll("[data-modal]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      closeQcMenu();
      openModal(btn.getAttribute("data-modal"));
    });
  });

  document.querySelectorAll("[data-close]").forEach(function (btn) {
    btn.addEventListener("click", closeModal);
  });

  if (modalOverlay) {
    modalOverlay.addEventListener("click", function (e) {
      if (e.target === modalOverlay) closeModal();
    });
  }

  // ---- Command palette (Ctrl/Cmd+K quick-switch) ---------------------

  var paletteOverlay = document.getElementById("palette-overlay");
  var paletteInput = document.getElementById("palette-input");
  var paletteResults = document.getElementById("palette-results");
  var paletteToggle = document.getElementById("palette-toggle");
  var currentResults = [];
  var selectedIndex = -1;
  var debounceTimer = null;

  function openPalette() {
    if (!paletteOverlay) return;
    paletteOverlay.hidden = false;
    paletteInput.value = "";
    paletteInput.focus();
    renderResults([]);
  }

  function closePalette() {
    if (paletteOverlay) paletteOverlay.hidden = true;
  }

  function renderResults(results) {
    currentResults = results;
    selectedIndex = results.length ? 0 : -1;
    if (!paletteResults) return;
    if (!results.length) {
      paletteResults.innerHTML = '<div class="palette-empty">Type to search projects, agents, and issues&hellip;</div>';
      return;
    }
    paletteResults.innerHTML = "";
    results.forEach(function (r, idx) {
      var row = document.createElement("div");
      row.className = "palette-item" + (idx === selectedIndex ? " selected" : "");
      row.dataset.index = String(idx);
      var title = document.createElement("span");
      title.className = "palette-title";
      title.textContent = r.title;
      var subtitle = document.createElement("span");
      subtitle.className = "palette-subtitle";
      subtitle.textContent = r.subtitle;
      row.appendChild(title);
      row.appendChild(subtitle);
      row.addEventListener("mouseenter", function () {
        selectedIndex = idx;
        updateSelection();
      });
      row.addEventListener("click", function () {
        window.location.href = r.url;
      });
      paletteResults.appendChild(row);
    });
  }

  function updateSelection() {
    var items = paletteResults.querySelectorAll(".palette-item");
    items.forEach(function (item, idx) {
      item.classList.toggle("selected", idx === selectedIndex);
    });
    var active = paletteResults.querySelector(".palette-item.selected");
    if (active) active.scrollIntoView({ block: "nearest" });
  }

  function runSearch(q) {
    if (!q) {
      renderResults([]);
      return;
    }
    fetch("/api/search?q=" + encodeURIComponent(q))
      .then(function (resp) { return resp.json(); })
      .then(function (data) { renderResults(data.results || []); })
      .catch(function () { renderResults([]); });
  }

  if (paletteInput) {
    paletteInput.addEventListener("input", function () {
      var q = paletteInput.value.trim();
      window.clearTimeout(debounceTimer);
      debounceTimer = window.setTimeout(function () { runSearch(q); }, 120);
    });

    paletteInput.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (currentResults.length) {
          selectedIndex = (selectedIndex + 1) % currentResults.length;
          updateSelection();
        }
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        if (currentResults.length) {
          selectedIndex = (selectedIndex - 1 + currentResults.length) % currentResults.length;
          updateSelection();
        }
      } else if (e.key === "Enter") {
        e.preventDefault();
        var chosen = currentResults[selectedIndex];
        if (chosen) window.location.href = chosen.url;
      } else if (e.key === "Escape") {
        closePalette();
      }
    });
  }

  if (paletteToggle) paletteToggle.addEventListener("click", openPalette);

  if (paletteOverlay) {
    paletteOverlay.addEventListener("click", function (e) {
      if (e.target === paletteOverlay) closePalette();
    });
  }

  document.addEventListener("keydown", function (e) {
    var isMod = e.ctrlKey || e.metaKey;
    if (isMod && e.key.toLowerCase() === "k") {
      e.preventDefault();
      openPalette();
      return;
    }
    if (e.key === "Escape") {
      if (paletteOverlay && !paletteOverlay.hidden) closePalette();
      if (modalOverlay && !modalOverlay.hidden) closeModal();
    }
  });
})();
