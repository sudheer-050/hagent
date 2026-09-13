(function () {
  "use strict";
  var page = document.querySelector(".terminal-page");
  if (!page) return;
  if (typeof Terminal === "undefined") {
    var fallbackButton = document.getElementById("terminal-new");
    var fallbackPreset = document.getElementById("terminal-path-preset");
    var fallbackCwd = document.getElementById("terminal-cwd");
    var fallbackPathRow = document.getElementById("terminal-custom-path-row");
    var fallbackMenu = document.getElementById("terminal-dropdown");
    if (fallbackButton && fallbackMenu) {
      var syncFallbackPath = function (focusCustom) {
        if (!fallbackPreset || !fallbackPathRow || !fallbackCwd) return;
        fallbackPathRow.hidden = fallbackPreset.value !== "custom";
        if (fallbackPreset.value === "home") fallbackCwd.value = page.dataset.userHome || page.dataset.defaultCwd;
        else if (fallbackPreset.value === "project") fallbackCwd.value = page.dataset.projectCwd || page.dataset.defaultCwd;
        else if (focusCustom) fallbackCwd.focus();
      };
      if (fallbackPreset && fallbackCwd) {
        fallbackPreset.addEventListener("change", function () { syncFallbackPath(true); });
        fallbackCwd.addEventListener("input", function () { fallbackPreset.value = "custom"; syncFallbackPath(false); });
        syncFallbackPath(false);
      }
      document.body.appendChild(fallbackMenu);
      var fallbackNotice = document.createElement("p");
      fallbackNotice.className = "inline-empty";
      fallbackNotice.setAttribute("role", "status");
      fallbackNotice.textContent = "The terminal client did not load. Refresh this page before creating a session.";
      fallbackMenu.insertBefore(fallbackNotice, fallbackMenu.firstChild);
      var fallbackToggle = function (open) {
        fallbackMenu.hidden = !open;
        fallbackButton.setAttribute("aria-expanded", open ? "true" : "false");
        if (open) {
          var rect = fallbackButton.getBoundingClientRect();
          fallbackMenu.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - Math.min(360, window.innerWidth - 16) - 8)) + "px";
          fallbackMenu.style.top = (rect.bottom + 6) + "px";
        }
      };
      fallbackButton.addEventListener("click", function () { fallbackToggle(fallbackMenu.hidden); });
      ["terminal-menu-close", "terminal-cancel"].forEach(function (id) {
        var close = document.getElementById(id);
        if (close) close.addEventListener("click", function () { fallbackToggle(false); });
      });
      document.addEventListener("pointerdown", function (event) {
        if (!fallbackMenu.hidden && !fallbackMenu.contains(event.target) && !fallbackButton.contains(event.target)) fallbackToggle(false);
      });
    }
    return;
  }

  var tabs = document.getElementById("terminal-tabs");
  var views = document.getElementById("terminal-views");
  var addButton = document.getElementById("terminal-new");
  var dropdown = document.getElementById("terminal-dropdown");
  document.body.appendChild(dropdown);
  var newWrap = document.getElementById("terminal-new-wrap");
  var menuClose = document.getElementById("terminal-menu-close");
  var form = document.getElementById("terminal-create-form");
  var cancel = document.getElementById("terminal-cancel");
  var nameInput = document.getElementById("terminal-name");
  var cwdInput = document.getElementById("terminal-cwd");
  var pathPreset = document.getElementById("terminal-path-preset");
  var customPathRow = document.getElementById("terminal-custom-path-row");
  var userHome = page.dataset.userHome || page.dataset.defaultCwd;
  var projectCwd = page.dataset.projectCwd || page.dataset.defaultCwd;
  var defaultCwd = page.dataset.defaultCwd;
  var sessions = new Map();
  var activeId = null;
  var nextNumber = 1;
  var storageKey = "hagent-terminal-tabs-v1";

  function saveLayout() {
    var layout = Array.from(sessions.values()).map(function (s) { return { name: s.name, cwd: s.cwd }; });
    try { localStorage.setItem(storageKey, JSON.stringify(layout)); } catch (_) {}
  }

  function positionDropdown() {
    if (dropdown.hidden) return;
    var anchor = addButton.getBoundingClientRect();
    dropdown.style.left = Math.max(8, Math.min(anchor.left, window.innerWidth - Math.min(360, window.innerWidth - 16) - 8)) + "px";
    dropdown.style.top = (anchor.bottom + 6) + "px";
  }

  function activate(id) {
    var session = sessions.get(id);
    if (!session) return;
    activeId = id;
    sessions.forEach(function (item, itemId) {
      item.tab.classList.toggle("active", itemId === id);
      item.tab.setAttribute("aria-selected", itemId === id ? "true" : "false");
      item.view.hidden = itemId !== id;
    });
    requestAnimationFrame(function () {
      session.fit.fit();
      session.term.focus();
      sendResize(session);
    });
  }

  function sendResize(session) {
    if (session.socket.readyState === WebSocket.OPEN) {
      session.socket.send(JSON.stringify({ type: "resize", rows: session.term.rows, cols: session.term.cols }));
    }
  }

  function closeSession(id) {
    var session = sessions.get(id);
    if (!session) return;
    var ids = Array.from(sessions.keys());
    var index = ids.indexOf(id);
    session.socket.close();
    session.term.dispose();
    session.tab.remove();
    session.view.remove();
    sessions.delete(id);
    if (activeId === id) {
      var replacement = ids[index + 1] || ids[index - 1];
      activeId = null;
      if (replacement && sessions.has(replacement)) activate(replacement);
    }
    saveLayout();
  }

  function createSession(name, cwd, restoring) {
    var id = "terminal-" + Date.now() + "-" + nextNumber++;
    var tab = document.createElement("button");
    tab.type = "button";
    tab.className = "terminal-tab";
    tab.setAttribute("role", "tab");
    tab.innerHTML = '<span class="terminal-tab-icon">›_</span><span class="terminal-tab-name"></span><span class="terminal-tab-close" title="Close terminal" aria-label="Close terminal">×</span>';
    tab.querySelector(".terminal-tab-name").textContent = name;
    tab.addEventListener("click", function (event) {
      if (event.target.classList.contains("terminal-tab-close")) closeSession(id);
      else activate(id);
    });
    tabs.insertBefore(tab, newWrap);

    var view = document.createElement("div");
    view.className = "terminal-view";
    view.hidden = false;
    views.appendChild(view);

    var term = new Terminal({
           cursorBlink: true,
      convertEol: false,
      fontFamily: '"Cascadia Code", Consolas, monospace',
      fontSize: 13,
      lineHeight: 1.0,
      scrollback: 10000,
      allowProposedApi: false,
      theme: { background: "#08080c", foreground: "#e8e8ed", cursor: "#e8a857", selectionBackground: "#654b2f88", black: "#050508", brightBlack: "#6b6b74", red: "#f27a83", green: "#4bd1a0", yellow: "#f0c95c", blue: "#6fb2e8", magenta: "#b39ef2", cyan: "#62c7c2", white: "#fafafa" }
    });
    var fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(view);
    fit.fit();

    var protocol = location.protocol === "https:" ? "wss:" : "ws:";
    var socket = new WebSocket(protocol + "//" + location.host + "/ws/terminal?cwd=" + encodeURIComponent(cwd) + "&rows=" + term.rows + "&cols=" + term.cols);
    var session = { id: id, name: name, cwd: cwd, tab: tab, view: view, term: term, fit: fit, socket: socket };
    sessions.set(id, session);
    term.onData(function (data) {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "input", data: data }));
    });
    socket.onmessage = function (event) {
      var message = JSON.parse(event.data);
      if (message.type === "output") term.write(message.data);
      else if (message.type === "ready") { session.cwd = message.cwd; tab.title = message.cwd; }
      else if (message.type === "error") term.writeln("\r\n\x1b[31m" + message.message + "\x1b[0m");
      else if (message.type === "exit") term.writeln("\r\n\x1b[90mProcess exited. Close this tab or create a new terminal.\x1b[0m");
    };
    socket.onerror = function () { term.writeln("\r\n\x1b[31mTerminal connection failed.\x1b[0m"); };
    socket.onopen = function () { sendResize(session); };

    var observer = new ResizeObserver(function () {
      if (!view.hidden) { fit.fit(); sendResize(session); }
    });
    observer.observe(view);
    session.observer = observer;
    activate(id);
    if (!restoring) saveLayout();
  }

  function updatePathPreset() {
    customPathRow.hidden = pathPreset.value !== "custom";
    if (pathPreset.value === "home") cwdInput.value = userHome;
    else if (pathPreset.value === "project") cwdInput.value = projectCwd;
    else cwdInput.focus();
  }

  function openMenu() {
    nameInput.value = "PowerShell " + (sessions.size + 1);
    cwdInput.value = activeId && sessions.get(activeId) ? sessions.get(activeId).cwd : userHome;
    pathPreset.value = cwdInput.value.toLowerCase() === userHome.toLowerCase() ? "home" : (cwdInput.value.toLowerCase() === projectCwd.toLowerCase() ? "project" : "custom");
    updatePathPreset();
    dropdown.hidden = false;
    addButton.setAttribute("aria-expanded", "true");
    positionDropdown();
    nameInput.focus();
    nameInput.select();
  }

  function closeMenu() {
    dropdown.hidden = true;
    addButton.setAttribute("aria-expanded", "false");
  }

  addButton.addEventListener("click", function () {
    if (dropdown.hidden) openMenu(); else closeMenu();
  });
  pathPreset.addEventListener("change", updatePathPreset);
  cwdInput.addEventListener("input", function () { pathPreset.value = "custom"; customPathRow.hidden = false; });
  cancel.addEventListener("click", closeMenu);
  menuClose.addEventListener("click", closeMenu);
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    createSession(nameInput.value.trim() || "PowerShell", cwdInput.value.trim() || userHome, false);
    closeMenu();
  });
  document.addEventListener("pointerdown", function (event) {
    if (!dropdown.hidden && !dropdown.contains(event.target) && !addButton.contains(event.target)) closeMenu();
  });
  document.addEventListener("keydown", function (event) { if (event.key === "Escape" && !dropdown.hidden) closeMenu(); });
  window.addEventListener("resize", positionDropdown);
  window.addEventListener("scroll", positionDropdown, true);
  window.addEventListener("beforeunload", function () { sessions.forEach(function (s) { s.socket.close(); }); });

  var saved = [];
  try { saved = JSON.parse(localStorage.getItem(storageKey) || "[]"); } catch (_) {}
  if (Array.isArray(saved) && saved.length) saved.slice(0, 12).forEach(function (item) { createSession(item.name || "PowerShell", item.cwd || defaultCwd, true); });
  else createSession("PowerShell 1", defaultCwd, false);
  saveLayout();
})();