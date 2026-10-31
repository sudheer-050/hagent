// Live page refresh: keeps the open page current without a manual reload.
//
// Every few seconds it asks the server for the same page as a fragment (the same
// X-Hagent-Nav request the client-side router uses) and, only if the content changed,
// swaps it in. It never interrupts you: nothing is swapped while you are typing, have text
// selected, have a modal open, are mid-click, or the tab is hidden. Scroll position, inner
// scroll panels and open <details> are kept. Pages with live behaviour of their own
// (terminal, chat thread) and the login page are left alone.
// Opt a page out with data-no-live on <body>, or pause from the console: hagentLive.pause().
(function () {
  "use strict";

  var BASE_MS = 4000;
  var MAX_MS = 60000;
  var SKIP = [/^\/login/, /^\/terminal/, /^\/chat\/[^/]+/, /^\/api\//, /^\/static\//, /\/diff(\/|$)/];

  var mainSlot = document.getElementById("main-slot");
  var sidebarScroll = document.querySelector(".sidebar-scroll");
  if (!mainSlot) return;

  var delay = BASE_MS;
  var timer = null;
  var paused = false;
  var inFlight = false;
  var refHref = null;
  var refMain = null;
  var refNav = null;
  var lastPointer = 0;
  var baseline = null;

  function skipped() {
    if (document.body.hasAttribute("data-no-live")) return true;
    var path = window.location.pathname;
    return SKIP.some(function (re) { return re.test(path); });
  }

  function typingNow() {
    var el = document.activeElement;
    if (!el || el === document.body) return false;
    var tag = el.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
  }

  // Field values right after the page was drawn; anything different later is the user's edit.
  // (Comparing to the HTML defaults is wrong: a <select> with no selected option reads as "changed".)
  function fieldState() {
    var out = [];
    mainSlot.querySelectorAll("input, textarea, select").forEach(function (f) {
      if (f.type === "hidden" || f.type === "submit" || f.type === "button") return;
      out.push(f.type === "checkbox" || f.type === "radio" ? (f.checked ? "1" : "0") : f.value);
    });
    return out.join("");
  }

  function hasEdits() {
    return baseline !== null && fieldState() !== baseline;
  }

  function busy() {
    if (document.hidden || paused) return true;
    if (typingNow() || hasEdits()) return true;
    var sel = window.getSelection && window.getSelection();
    if (sel && !sel.isCollapsed && String(sel).length > 0) return true;
    var overlay = document.getElementById("modal-overlay");
    if ((overlay && !overlay.hidden) || document.body.classList.contains("modal-open")) return true;
    var palette = document.getElementById("palette-overlay");
    if (palette && !palette.hidden) return true;
    if (Date.now() - lastPointer < 1500) return true;
    return false;
  }

  document.addEventListener("pointerdown", function () { lastPointer = Date.now(); }, true);
  document.addEventListener("keydown", function () { lastPointer = Date.now(); }, true);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) schedule(300); });

  function openDetailsIndexes(root) {
    var out = [];
    root.querySelectorAll("details").forEach(function (d, i) { if (d.open) out.push(i); });
    return out;
  }

  function scrollPanels(root) {
    var out = [];
    root.querySelectorAll("*").forEach(function (el, i) {
      if (el.scrollTop > 0 && el.scrollHeight > el.clientHeight) out.push([i, el.scrollTop]);
    });
    return out;
  }

  function swapMain(html) {
    var y = window.scrollY;
    var opened = openDetailsIndexes(mainSlot);
    var panels = scrollPanels(mainSlot);
    mainSlot.innerHTML = html; // scripts set this way do not run; page behaviour is bound by delegation / hagentPageInits
    var details = mainSlot.querySelectorAll("details");
    opened.forEach(function (i) { if (details[i]) details[i].open = true; });
    var all = mainSlot.querySelectorAll("*");
    panels.forEach(function (p) { if (all[p[0]]) all[p[0]].scrollTop = p[1]; });
    (window.hagentPageInits || []).forEach(function (fn) { try { fn(); } catch (e) { /* keep going */ } });
    window.scrollTo(0, y);
    baseline = fieldState();
  }

  function swapNav(html) {
    if (!sidebarScroll) return;
    var top = sidebarScroll.scrollTop;
    sidebarScroll.innerHTML = html;
    sidebarScroll.scrollTop = top;
  }

  function tick() {
    timer = null;
    if (skipped() || inFlight) return schedule(delay);
    var href = window.location.href;
    if (href !== refHref) { // navigated: take a fresh baseline, do not swap this time
      refHref = href; refMain = null; refNav = null; baseline = null;
    }
    inFlight = true;
    fetch(href, { headers: { "X-Hagent-Nav": "1" }, credentials: "same-origin", cache: "no-store" })
      .then(function (r) {
        var type = r.headers.get("content-type") || "";
        if (!r.ok || type.indexOf("application/json") === -1) throw new Error("not a fragment");
        return r.json();
      })
      .then(function (data) {
        delay = BASE_MS;
        if (window.location.href !== href) return; // user navigated while this was in flight
        if (refMain === null) { refMain = data.main; refNav = data.nav; baseline = fieldState(); return; }
        if (busy()) return; // keep the old reference so the change is applied once you are idle
        if (data.main !== refMain) { swapMain(data.main); refMain = data.main; }
        if (data.nav !== refNav && !(sidebarScroll && sidebarScroll.contains(document.activeElement))) {
          swapNav(data.nav); refNav = data.nav;
        }
        if (data.title && data.title !== document.title) document.title = data.title;
      })
      .catch(function () { delay = Math.min(delay * 2, MAX_MS); }) // offline, signed out, or server restarting
      .then(function () { inFlight = false; schedule(delay); });
  }

  function schedule(ms) {
    if (timer) clearTimeout(timer);
    timer = setTimeout(tick, ms);
  }

  window.hagentLive = {
    pause: function () { paused = true; },
    resume: function () { paused = false; schedule(300); },
  };

  schedule(1500);
})();
