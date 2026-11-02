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

  document.addEventListener("click", function (e) {
    var modalBtn = e.target.closest ? e.target.closest("[data-modal]") : null;
    if (modalBtn) {
      closeQcMenu();
      openModal(modalBtn.getAttribute("data-modal"));
      return;
    }
    var closeBtn = e.target.closest ? e.target.closest("[data-close]") : null;
    if (closeBtn) closeModal();
  });

  if (modalOverlay) {
    modalOverlay.addEventListener("click", function (e) {
      if (e.target === modalOverlay) closeModal();
    });
  }

  var requestedModal = new URLSearchParams(window.location.search).get("add");
  if (requestedModal === "runtime") openModal("page-modal-runtime");
  if (requestedModal === "agent") openModal("page-modal-agent");

  // ---- Image lightbox (agent avatars + chat image attachments) -------

  var lightbox = document.getElementById("image-lightbox");
  var lightboxImg = document.getElementById("image-lightbox-img");

  if (lightbox && lightboxImg) {
    document.addEventListener("click", function (e) {
      var img = e.target.closest ? e.target.closest(".agent-avatar-img, .chat-message-image") : null;
      if (!img) return;
      e.preventDefault();
      lightboxImg.src = img.src;
      lightboxImg.alt = img.alt || "";
      lightbox.hidden = false;
      document.body.classList.add("modal-open");
    });
    lightbox.addEventListener("click", function () {
      lightbox.hidden = true;
      lightboxImg.src = "";
      document.body.classList.remove("modal-open");
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
        closePalette();
        window.hagentNavigate(r.url);
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
        if (chosen) {
          closePalette();
          window.hagentNavigate(chosen.url);
        }
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

// ---- SPA-style client-side router -----------------------------------
// Fetches nav/main/modals fragments instead of full pages so navigating
// around the app never flashes the sidebar or reloads the page.
(function () {
  "use strict";

  var mainSlot = document.getElementById("main-slot");
  var sidebarScroll = document.querySelector(".sidebar-scroll");
  var modalOverlay = document.getElementById("modal-overlay");

  // Narrow screens: the menu button opens the navigation as a dropdown.
  var navToggle = document.getElementById("nav-toggle");
  function setNavOpen(open) {
    document.body.classList.toggle("nav-open", open);
    if (navToggle) navToggle.setAttribute("aria-expanded", open ? "true" : "false");
  }
  if (navToggle) {
    navToggle.addEventListener("click", function () {
      setNavOpen(!document.body.classList.contains("nav-open"));
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") setNavOpen(false);
    });
  }

  function execScripts(container) {
    container.querySelectorAll("script").forEach(function (old) {
      var fresh = document.createElement("script");
      for (var i = 0; i < old.attributes.length; i++) {
        fresh.setAttribute(old.attributes[i].name, old.attributes[i].value);
      }
      fresh.textContent = old.textContent;
      old.parentNode.replaceChild(fresh, old);
    });
  }

  // The router swaps page fragments without reloading <html>, so keep the theme
  // in step by hand: a theme previewed on the Settings page is undone when you
  // leave without saving, and a freshly saved theme applies without a refresh.
  var themeRoot = document.documentElement;
  if (!themeRoot.dataset.savedTheme) themeRoot.dataset.savedTheme = themeRoot.dataset.theme || "";
  function syncTheme() {
    var field = document.getElementById("settings-theme");
    if (field && field.dataset.saved) themeRoot.dataset.savedTheme = field.dataset.saved;
    if (themeRoot.dataset.savedTheme) themeRoot.dataset.theme = themeRoot.dataset.savedTheme;
  }

  function applyFragment(data) {
    if (data.title) document.title = data.title;
    document.body.className = data.body_class || "";
    if (navToggle) navToggle.setAttribute("aria-expanded", "false");
    if (sidebarScroll) sidebarScroll.innerHTML = data.nav;
    if (mainSlot) {
      mainSlot.innerHTML = data.main;
      execScripts(mainSlot);
      (window.hagentPageInits || []).forEach(function (fn) { fn(); });
    }
    syncTheme();
    if (modalOverlay) {
      modalOverlay.hidden = true;
      document.body.classList.remove("modal-open");
      modalOverlay.innerHTML = data.modals || "";
    }
    window.scrollTo(0, 0);
  }

  function navigate(url, recordHistory) {
    return fetch(url, {
      headers: { "X-Hagent-Nav": "1" },
      credentials: "same-origin",
    })
      .then(function (response) {
        var contentType = response.headers.get("content-type") || "";
        if (!response.ok || contentType.indexOf("application/json") === -1) {
          throw new Error("non-fragment response");
        }
        return response.json();
      })
      .then(function (data) {
        applyFragment(data);
        if (recordHistory !== false) history.pushState({ hagentNav: true }, "", url);
      })
      .catch(function () {
        window.location.href = url;
      });
  }

  window.hagentNavigate = navigate;

  function isInternalPlainLink(a) {
    if (!a.href) return false;
    if (a.target || a.hasAttribute("download") || a.hasAttribute("data-no-pjax")) return false;
    var href = a.getAttribute("href") || "";
    if (!href || href.charAt(0) === "#" || href.indexOf("javascript:") === 0 || href.indexOf("mailto:") === 0) return false;
    var url = new URL(a.href, window.location.href);
    return url.origin === window.location.origin;
  }

  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest ? e.target.closest("a") : null;
    if (!a || !isInternalPlainLink(a)) return;
    e.preventDefault();
    navigate(a.href, true);
  });

  document.addEventListener("submit", function (e) {
    if (e.defaultPrevented) return;
    var form = e.target;
    if (!(form instanceof HTMLFormElement) || form.hasAttribute("data-no-pjax")) return;
    var method = (form.getAttribute("method") || "get").toLowerCase();

    if (method === "get") {
      e.preventDefault();
      var params = new URLSearchParams(new FormData(form));
      var action = form.getAttribute("action") || window.location.pathname + window.location.search;
      var url = action.split("?")[0] + (params.toString() ? "?" + params.toString() : "");
      navigate(url, true);
      return;
    }

    e.preventDefault();
    var postUrl = form.getAttribute("action") || window.location.href;
    var finalUrl = postUrl;
    fetch(postUrl, {
      method: "POST",
      body: new FormData(form),
      headers: { "X-Hagent-Nav": "1" },
      credentials: "same-origin",
    })
      .then(function (response) {
        finalUrl = response.url || finalUrl;
        var contentType = response.headers.get("content-type") || "";
        if (!response.ok || contentType.indexOf("application/json") === -1) {
          throw new Error("non-fragment response");
        }
        return response.json();
      })
      .then(function (data) {
        applyFragment(data);
        history.pushState({ hagentNav: true }, "", finalUrl);
      })
      .catch(function () {
        form.submit();
      });
  });

  window.addEventListener("popstate", function () {
    navigate(window.location.href, false);
  });
})();

// Focused collection filtering for the Agents page.
function initAgentFilter() {
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
}
(window.hagentPageInits = window.hagentPageInits || []).push(initAgentFilter);
initAgentFilter();

// API or installed CLI -> provider -> live models -> supported effort.
function initRuntimeModelCatalog() {
  "use strict";
  const $ = id => document.getElementById(id);
  const provider = $("runtime-type"), model = $("runtime-model");
  if(!provider || !model) return;
  const mode=$("runtime-mode"), apiOptions=[...provider.options].map(o=>({value:o.value,label:o.textContent}));
  const form=provider.closest('form'), connection=$("runtime-connection"), key=$("runtime-api-key"), base=$("runtime-base-url"), effort=$("runtime-effort"), status=$("runtime-model-source");
  const initial = {provider:form.dataset.currentProvider, connection:form.dataset.currentConnection, model:form.dataset.currentModel, effort:form.dataset.currentEffort};
  let models=[], generation=0, installedConnections=[];
  const originalSaveLabel=$("runtime-save").textContent;
  const requested=new URLSearchParams(location.search).get('provider');
  const requestedFamily={codex_cli:'openai',claude_code:'claude',gemini_cli:'gemini'}[requested] || requested;
  if(requestedFamily && [...provider.options].some(o=>o.value===requestedFamily)) provider.value=requestedFamily;
  if(requested && requested!==requestedFamily) mode.value='cli';
  function invalidate() {
    generation++; models=[];
    $("runtime-catalog-token").value=''; $("runtime-save").disabled=true; $("runtime-save").textContent=originalSaveLabel;
    $("runtime-model-group").hidden=true; model.disabled=true;
    $("runtime-effort-group").hidden=true; effort.replaceChildren(new Option('Provider default',''));
    status.hidden=true; $("runtime-load-models").disabled=false;
  }
  function chooseEffort() {
    const row=models.find(r=>r.id===model.value);
    effort.replaceChildren(new Option('Provider default',''));
    (row?.efforts || []).forEach(value=>effort.append(new Option(value==='xhigh'?'Extra high':value.charAt(0).toUpperCase()+value.slice(1),value)));
    $("runtime-effort-group").hidden=false;
    effort.disabled=!(row?.efforts || []).length;
    if(model.value===initial.model && [...effort.options].some(o=>o.value===initial.effort)) effort.value=initial.effort;
    $("runtime-save").disabled=!row;
  }
  function connectionChanged() {
    invalidate();
    const local=provider.value==='ollama', cli=connection.value==='cli';
    $("runtime-key-group").hidden=local||cli; key.disabled=local||cli;
    const server=!cli && (local||provider.value==='openai_compatible');
    $("runtime-base-url-group").hidden=!server; base.disabled=!server; base.required=server;
    if(local && !base.value) base.value='http://127.0.0.1:11434';
    form.querySelector('[name="command"]').disabled=!(provider.value===initial.provider && connection.value===initial.connection);
    form.querySelector('[name="working_directory"]').disabled=provider.value!==initial.provider;
  }
  function providerChanged() {
    base.value=provider.value===initial.provider ? base.dataset.savedValue : '';
    connection.value=mode.value==='cli'?'cli':provider.value==='ollama'?'local':'api';
    connectionChanged();
    $("runtime-load-models").disabled=!provider.value;
    $("runtime-load-models").hidden=mode.value==='cli';
    if(mode.value==='cli') {
      model.replaceChildren(new Option('CLI default','default'));
      model.disabled=!provider.value;
      const connected=installedConnections.find(cli=>cli.provider===provider.value)?.connected && !form.dataset.runtimeId;
      $("runtime-save").disabled=!provider.value || connected;
      if(connected) $("runtime-save").textContent='Already connected';
    }
  }
  async function modeChanged() {
    const previous=provider.value;
    invalidate();
    $('runtime-load-models').hidden=mode.value==='cli';
    $("runtime-provider-label").textContent=mode.value==='cli'?'Installed CLI':'Provider';
    if(mode.value==='api') {
      provider.replaceChildren(...apiOptions.map(o=>new Option(o.label,o.value)));
      if(apiOptions.some(o=>o.value===previous)) provider.value=previous;
      provider.disabled=false; providerChanged(); return;
    }
    connection.value='cli'; connectionChanged();
    provider.replaceChildren(new Option('Checking installed CLIs...','')); provider.disabled=true;
    $("runtime-load-models").disabled=true;
    const request=generation;
    try {
      const response=await fetch('/api/runtime-connections');
      if(!response.ok) throw new Error('Could not check installed CLIs.');
      const data=await response.json(); if(request!==generation) return;
      installedConnections=data.installed;
      provider.replaceChildren(...data.installed.map(cli=>new Option(cli.label+(cli.connected?' (connected)':''),cli.provider)));
      if(!data.installed.length) {
        provider.append(new Option('No supported CLIs installed',''));
        status.textContent='No supported CLIs were found on this computer.'; status.hidden=false;
        return;
      }
      if(data.installed.some(cli=>cli.provider===previous)) provider.value=previous;
      provider.disabled=false; providerChanged();
    } catch(error) { if(request===generation) {provider.replaceChildren(new Option('Detection unavailable',''));status.textContent=error.message;status.hidden=false;} }
  }
  async function loadModels() {
    invalidate(); const request=generation;
    $("runtime-load-models").disabled=true; status.hidden=false; status.textContent='Loading models...';
    try {
      const response=await fetch('/api/runtime-models',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider:provider.value,connection:connection.value,api_key:key.disabled?'':key.value,base_url:base.disabled?'':base.value,runtime_id:form.dataset.runtimeId})});
      const data=await response.json(); if(request!==generation) return;
      if(!response.ok) throw new Error(data.detail || 'Could not load models.');
      models=data.models; model.replaceChildren(new Option('Choose a model',''));
      models.forEach(row=>model.append(new Option(row.label===row.id?row.id:row.label+' ('+row.id+')',row.id)));
      $("runtime-catalog-token").value=data.catalog_token; model.disabled=false; $("runtime-model-group").hidden=false;
      if(provider.value===initial.provider && connection.value===initial.connection) {
        if(models.some(r=>r.id===initial.model)) model.value=initial.model;
        else if(initial.model==='default') model.value=models.find(r=>r.is_default)?.id || '';
      }
      status.textContent=models.length+' models available'; chooseEffort();
    } catch(error) { if(request===generation) {status.textContent=error.message;status.hidden=false;} }
    finally { if(request===generation) $("runtime-load-models").disabled=false; }
  }
  provider.addEventListener('change',providerChanged);
  mode.addEventListener('change',modeChanged);
  model.addEventListener('change',chooseEffort);
  [key,base].forEach(input=>input.addEventListener('input',invalidate));
  key.addEventListener('change',()=>{if(key.value.trim()) loadModels();});
  $("runtime-load-models").onclick=loadModels;
  form.addEventListener('submit',event=>{if(mode.value!=='cli' && (!$("runtime-catalog-token").value || !model.value)) {event.preventDefault();status.textContent='Load models and choose one first.';status.hidden=false;}});
  modeChanged();
}
(window.hagentPageInits = window.hagentPageInits || []).push(initRuntimeModelCatalog);
initRuntimeModelCatalog();

/* Agent creation: guided instruction draft, with explicit user-triggered runtime calls. */
function initAgentDraftHelper() {
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
}
(window.hagentPageInits = window.hagentPageInits || []).push(initAgentDraftHelper);
initAgentDraftHelper();

let runtimeReloadBusy = false;
const runtimeRefreshedAt = new Map();

function runtimeCheckTime(value) {
  // SQLite returns naive timestamps; Hagent stores these values in UTC.
  const iso = value.replace(" ", "T");
  const date = new Date(/[zZ]$|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + "Z");
  return Number.isNaN(date.getTime()) ? "Unknown time" : date.toLocaleString(undefined, {timeZoneName: "short"});
}

function initRuntimeTimes() {
  document.querySelectorAll("time[data-runtime-time]").forEach(time => {
    time.textContent = runtimeCheckTime(time.getAttribute("datetime"));
  });
  document.querySelectorAll("[data-runtime-refreshed]").forEach(label => {
    const refreshed = runtimeRefreshedAt.get(label.dataset.runtimeRefreshed);
    if (refreshed) {
      label.textContent = "Status refreshed: " + runtimeCheckTime(refreshed);
      label.hidden = false;
    } else { label.hidden = true; }
  });
}
(window.hagentPageInits = window.hagentPageInits || []).push(initRuntimeTimes);
initRuntimeTimes();

async function runRuntimeAction(button) {
  const panel = button.closest("[data-runtime-actions]");
  if (panel.dataset.busy) return;
  const remove = button.hasAttribute("data-runtime-remove");
  if (remove && !window.confirm("Remove this runtime from the list? Past work will be preserved. Agents using it must be reassigned first.")) return;
  const status = panel.querySelector("[data-runtime-result]");
  panel.dataset.busy = "true";
  if (!remove) panel.dataset.refreshing = "true";
  panel.querySelectorAll("button").forEach(b => b.disabled = true);
  status.textContent = remove ? "Removing runtime…" : "Testing saved configuration… This can take up to 20 seconds.";
  const row = panel.closest(".runtime-row");
  const pill = row && row.querySelector(".health-pill");
  if (!remove) {
    runtimeRefreshedAt.delete(panel.dataset.runtimeId);
    initRuntimeTimes();
    if (pill) { pill.textContent = "Checking..."; pill.classList.add("needs-key"); }
  }
  let ok = false;
  try {
    const response = await fetch("/runtimes/" + encodeURIComponent(panel.dataset.runtimeId) + (remove ? "/remove" : "/test"), {
      method: "POST", headers: {"Accept": "application/json"}, signal: AbortSignal.timeout(30000)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || data.error || "Request failed");
    ok = data.ok;
    if (remove) {
      const row = panel.closest(".runtime-row");
      if (row) row.remove();
      else window.location.assign("/runtimes");
    } else {
      status.textContent = (data.ok ? "✓ " : "Connection failed: ") + data.message + " (" + data.elapsed_seconds + "s)";
      if (data.checked_at) runtimeRefreshedAt.set(panel.dataset.runtimeId, data.checked_at);
      initRuntimeTimes();
      if (pill) { pill.textContent = data.ok ? "Provider responding" : "Connection failed"; pill.classList.toggle("needs-key", !data.ok); }
    }
  } catch (error) {
    if (!remove && pill) { pill.textContent = "Check failed"; pill.classList.add("needs-key"); }
    status.textContent = error.name === "TimeoutError" ? "The check timed out. Try again after the runtime is reachable." : (error.message || "Could not contact Hagent. Try again.");
  } finally {
    delete panel.dataset.busy;
    delete panel.dataset.refreshing;
    panel.querySelectorAll("button").forEach(b => b.disabled = false);
  }
  return ok;
}

// Delegation survives PJAX page swaps without duplicating handlers.
document.addEventListener("click", async function (event) {
  const reload = event.target.closest("[data-runtime-reload]");
  const button = event.target.closest("[data-runtime-test], [data-runtime-remove]");
  if ((!reload && !button) || runtimeReloadBusy) return;
  if (button) return runRuntimeAction(button);
  const panels = Array.from(document.querySelectorAll("[data-runtime-actions]"));
  const status = document.querySelector("[data-runtime-reload-result]");
  status.hidden = false;
  if (panels.some(panel => panel.dataset.busy)) {
    status.textContent = "A connection test is already running. Please wait for it to finish.";
    return;
  }
  runtimeReloadBusy = true;
  reload.disabled = true;
  reload.setAttribute("aria-busy", "true");
  panels.forEach(panel => { panel.dataset.refreshing = "true"; });
  try {
    const server = await fetch("/api/runtime-status", {cache: "no-store", signal: AbortSignal.timeout(5000)});
    if (server.status === 404) throw new Error("Hagent is still running its older server. Double-click scripts/restart_hagent.vbs to activate fresh checks through the ready CLIs.");
    if (!server.ok) throw new Error("Could not start live checks. Please sign in again or check the Hagent server.");
    status.textContent = "Sending a fresh request to every runtime...";
    let completed = 0;
    const results = await Promise.all(panels.map(async panel => {
      const ok = await runRuntimeAction(panel.querySelector("[data-runtime-test]"));
      completed++;
      status.textContent = "Live checks finished: " + completed + " of " + panels.length + ".";
      return ok;
    }));
    const passed = results.filter(Boolean).length;
    status.textContent = "Fresh checks complete: " + passed + " connected, " + (panels.length - passed) + " failed.";
  } catch (error) {
    status.textContent = error.message || "Could not start live checks.";
  } finally {
    runtimeReloadBusy = false;
    panels.forEach(panel => { delete panel.dataset.refreshing; });
    reload.disabled = false;
    reload.removeAttribute("aria-busy");
  }
});


// Required connection fields follow the selected authentication and transport.
document.addEventListener('change', function (event) {
  if (event.target.id === 'api-auth') {
    document.getElementById('api-token').required = event.target.value !== 'none';
    document.getElementById('api-header').required = event.target.value === 'header';
  }
  if (event.target.id === 'mcp-kind') {
    document.getElementById('mcp-command').required = event.target.value === 'stdio';
    document.getElementById('mcp-url').required = event.target.value !== 'stdio';
  }
});


// Live theme preview: choosing a theme on the Settings page shows it at once.
// It is only saved when the form is submitted; leaving the page undoes it.
function previewTheme(event) {
  if (!event.target || event.target.id !== 'settings-theme') return;
  document.documentElement.dataset.theme = event.target.value;
}
document.addEventListener('input', previewTheme);
document.addEventListener('change', previewTheme);
