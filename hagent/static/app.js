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
    document.body.classList.add("modal-open");
    var firstInput = target.querySelector("input, select");
    if (firstInput) window.setTimeout(function () { firstInput.focus(); }, 30);
  }

  function closeModal() {
    if (modalOverlay) modalOverlay.hidden = true;
    document.body.classList.remove("modal-open");
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

  var requestedModal = new URLSearchParams(window.location.search).get("add");
  if (requestedModal === "runtime") openModal("page-modal-runtime");
  if (requestedModal === "agent") openModal("page-modal-agent");
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

  var GROUP_LABELS = {
    page: "Pages", project: "Projects", agent: "Agents", chat: "Chat",
    issue: "Issues", runtime: "Runtimes", skill: "Skills", squad: "Squads",
  };

  function renderResults(results) {
    currentResults = results;
    selectedIndex = results.length ? 0 : -1;
    if (!paletteResults) return;
    if (!results.length) {
      paletteResults.innerHTML = '<div class="palette-empty">Search everything &mdash; chat, issues, runtimes, settings&hellip;</div>';
      return;
    }
    paletteResults.innerHTML = "";
    var lastType = null;
    results.forEach(function (r, idx) {
      if (r.type !== lastType) {
        var heading = document.createElement("div");
        heading.className = "palette-group-label";
        heading.textContent = GROUP_LABELS[r.type] || r.type;
        paletteResults.appendChild(heading);
        lastType = r.type;
      }
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

// Focused collection filtering for the Agents page.
(function () {
  "use strict";
  var input = document.getElementById("agent-filter");
  if (!input) return;
  var cards = Array.prototype.slice.call(document.querySelectorAll("[data-agent-card]"));
  var count = document.getElementById("agent-visible-count");
  var empty = document.getElementById("agent-filter-empty");

  input.addEventListener("input", function () {
    var query = input.value.trim().toLowerCase();
    var visible = 0;
    cards.forEach(function (card) {
      var matches = !query || (card.dataset.search || "").toLowerCase().indexOf(query) !== -1;
      card.hidden = !matches;
      if (matches) visible += 1;
    });
    if (count) count.textContent = String(visible);
    if (empty) empty.hidden = visible !== 0;
  });
})();
// Provider model catalog, task guidance, and local hardware fit.
(function () {
  "use strict";
  var provider = document.getElementById("runtime-type");
  var model = document.getElementById("runtime-model");
  if (!provider || !model) return;

  var requestedProvider = new URLSearchParams(window.location.search).get("provider");
  if (requestedProvider && provider.querySelector('[value="' + requestedProvider + '"]')) provider.value = requestedProvider;
  var form = provider.closest("form");
  var initialProvider = form ? form.dataset.currentProvider : "";
  var initialModel = form ? form.dataset.currentModel : "";
  var customGroup = document.getElementById("runtime-custom-model-group");
  var customModel = document.getElementById("runtime-custom-model");
  var keyGroup = document.getElementById("runtime-key-group");
  var keyInput = document.getElementById("runtime-api-key");
  var keyEnv = document.getElementById("runtime-key-env");
  var baseUrlGroup = document.getElementById("runtime-base-url-group");
  var baseUrl = document.getElementById("runtime-base-url");
  var baseRequirement = document.getElementById("runtime-base-url-requirement");
  var commandGroup = document.getElementById("runtime-command-group");
  var command = document.getElementById("runtime-command");
  var source = document.getElementById("runtime-model-source");
  var providerName = document.getElementById("runtime-provider-name");
  var providerKind = document.getElementById("runtime-provider-kind");
  var providerBest = document.getElementById("runtime-provider-best");
  var providerNote = document.getElementById("runtime-provider-note");
  var providerLink = document.getElementById("runtime-provider-link");
  var modelInfo = document.getElementById("runtime-model-info");
  var modelName = document.getElementById("runtime-model-name");
  var modelBest = document.getElementById("runtime-model-best");
  var modelNote = document.getElementById("runtime-model-note");
  var modelFit = document.getElementById("runtime-model-fit");
  var hardwareBox = document.getElementById("runtime-hardware");
  var hardwareSummary = document.getElementById("runtime-hardware-summary");
  var modelDetails = {};
  var requestNumber = 0;

  function updateModelInfo() {
    var isCustom = model.value === "__custom__";
    if (customGroup) customGroup.hidden = !isCustom;
    if (customModel) {
      customModel.disabled = !isCustom;
      customModel.required = isCustom;
    }
    var detail = modelDetails[model.value];
    if (modelInfo) modelInfo.hidden = !detail;
    if (!detail) return;
    modelName.textContent = detail.label || detail.id;
    modelBest.textContent = detail.best_for || "General use";
    modelNote.textContent = detail.note || "";
    if (detail.fit) {
      modelFit.hidden = false;
      modelFit.className = "fit-badge fit-" + detail.fit.level;
      modelFit.textContent = detail.fit.label;
      if (detail.fit.note) modelNote.textContent += " " + detail.fit.note;
    } else {
      modelFit.hidden = true;
    }
  }

  function renderProvider(info, hardware) {
    var kindLabels = {
      api: "Cloud API",
      local: "Local",
      local_openai: "Local server",
      cli: "Installed CLI",
      custom: "Custom endpoint"
    };
    providerName.textContent = info.name;
    providerKind.textContent = kindLabels[info.kind] || info.kind;
    providerBest.textContent = "Good for: " + info.best_for;
    providerNote.textContent = info.note;
    providerLink.hidden = !info.access_url;
    if (info.access_url) {
      providerLink.href = info.access_url;
      providerLink.textContent = info.access_label || "Provider setup";
    }

    var needsKey = info.kind === "api" || info.kind === "custom";
    keyGroup.hidden = !needsKey;
    keyInput.disabled = !needsKey;
    keyEnv.textContent = info.env ? "or use " + info.env : "if required by this endpoint";

    var showBase = info.kind === "custom" || info.kind === "local_openai";
    baseUrlGroup.hidden = !showBase;
    baseUrl.disabled = !showBase;
    baseUrl.required = info.kind === "custom";
    baseRequirement.textContent = info.kind === "custom" ? "Required" : "Optional";
    if (showBase) {
      var savedBase = provider.value === initialProvider ? baseUrl.dataset.savedValue : "";
      baseUrl.value = savedBase || info.base_url || "";
    }

    var isCli = info.kind === "cli";
    commandGroup.hidden = !isCli;
    command.disabled = !isCli;
    if (isCli) {
      var savedCommand = provider.value === initialProvider ? command.dataset.savedValue : "";
      command.value = savedCommand || info.command || "";
    }

    hardwareBox.hidden = !hardware;
    if (hardware) {
      var gpu = hardware.gpu + (hardware.vram_gb ? " · " + hardware.vram_gb + " GB VRAM" : "");
      hardwareSummary.textContent =
        gpu + " · " + (hardware.ram_gb || "?") +
        " GB RAM. Recommended: quantized 7B–9B models; 14B may be slow; 30B+ is not recommended.";
    }
  }

  function loadModels() {
    var selected = provider.value;
    var currentRequest = ++requestNumber;
    model.disabled = true;
    model.innerHTML = '<option value="">Loading recommendations...</option>';
    source.textContent = "Loading provider details...";
    fetch("/api/runtime-models?provider=" + encodeURIComponent(selected))
      .then(function (response) {
        if (!response.ok) throw new Error("Provider catalog unavailable");
        return response.json();
      })
      .then(function (data) {
        if (currentRequest !== requestNumber) return;
        renderProvider(data.provider_info, data.hardware);
        model.innerHTML = "";
        modelDetails = {};
        (data.model_details || []).forEach(function (detail) {
          modelDetails[detail.id] = detail;
          var option = document.createElement("option");
          option.value = detail.id;
          option.textContent = (detail.label || detail.id) + " — " + (detail.best_for || "General use");
          model.appendChild(option);
        });
        var custom = document.createElement("option");
        custom.value = "__custom__";
        custom.textContent = "Enter a custom model ID...";
        model.appendChild(custom);
        if (selected === initialProvider && initialModel) {
          if (modelDetails[initialModel]) {
            model.value = initialModel;
          } else {
            model.value = "__custom__";
            customModel.value = initialModel;
          }
        } else if (!(data.model_details || []).length) {
          model.value = "__custom__";
        }
        model.disabled = false;
        source.textContent = data.source === "installed"
          ? "Models currently installed on this computer."
          : "Recommended current models. Custom and newly released IDs are supported.";
        updateModelInfo();
      })
      .catch(function () {
        modelDetails = {};
        model.innerHTML = '<option value="__custom__">Enter a custom model ID...</option>';
        model.disabled = false;
        source.textContent = "Catalog unavailable; enter the provider's exact model ID.";
        updateModelInfo();
      });
  }

  provider.addEventListener("change", loadModels);
  model.addEventListener("change", updateModelInfo);
  loadModels();
})();
/* Agent creation: guided instruction draft, with explicit user-triggered runtime calls. */
(function () {
  "use strict";
  var panel = document.getElementById("agent-builder-panel");
  var radios = document.querySelectorAll('input[name="agent-start-mode"]');
  var draftButton = document.getElementById("agent-draft-button");
  var purpose = document.getElementById("agent-purpose");
  var runtime = document.getElementById("agent-runtime");
  var instructions = document.getElementById("agent-instructions");
  var status = document.getElementById("agent-draft-status");
  function updateMode() {
    if (!panel) return;
    var guided = document.querySelector('input[name="agent-start-mode"]:checked');
    panel.hidden = !guided || guided.value !== "guided";
  }
  radios.forEach(function (radio) { radio.addEventListener("change", updateMode); });
  updateMode();
  if (!draftButton) return;
  draftButton.addEventListener("click", function () {
    if (!purpose.value.trim()) {
      status.textContent = "Describe the agent's goal first.";
      purpose.focus();
      return;
    }
    draftButton.disabled = true;
    status.textContent = "Generating a draft with the selected runtime…";
    fetch("/api/agents/draft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ runtime_id: runtime.value, purpose: purpose.value.trim() })
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) throw new Error(data.detail || "The draft could not be generated.");
        return data;
      });
    }).then(function (data) {
      instructions.value = data.instructions || "";
      status.textContent = "Draft ready. Review and edit the instructions before creating the agent.";
      instructions.focus();
    }).catch(function (error) {
      status.textContent = error.message || "The draft could not be generated.";
    }).finally(function () {
      draftButton.disabled = false;
    });
  });
})();
