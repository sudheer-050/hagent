/* Local, owner-operated terminal. Output is rendered by xterm, never as HTML. */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const sessions = [];
  let selected = null;
  const dismissedAgents = new Set();
  const send = (session, data) => { if(session && session.socket && session.socket.readyState === WebSocket.OPEN) session.socket.send(JSON.stringify(data)); };
  function refresh() {
    $('terminal-status').textContent = selected ? selected.status : 'Ready to start a PowerShell session.';
    $('terminal-start').disabled = sessions.filter(s => s.socket && s.socket.readyState < WebSocket.CLOSING).length >= 8;
    $('terminal-empty').hidden = sessions.length > 0;
    sessions.forEach(s => { s.button.setAttribute('aria-selected', String(s === selected)); s.button.tabIndex = s === selected ? 0 : -1; s.detail.textContent = s.status; });
  }
  function select(session) {
    selected = session;
    sessions.forEach(s => { s.view.hidden = s !== session; });
    refresh();
    requestAnimationFrame(() => { if(selected === session && !session.closed) { session.fit.fit(); session.terminal.focus(); } });
  }
  function closeSession(session) {
    if(!session || session.closed) return;
    session.closed = true;
    if(session.agentId) dismissedAgents.add(session.agentId);
    const index = sessions.indexOf(session);
    if(index >= 0) sessions.splice(index,1);
    session.view.remove(); session.button.parentElement.remove();
    if(selected === session) {
      selected = null;
      if(sessions.length) select(sessions[Math.min(index,sessions.length-1)]);
    }
    refresh();
    // Remove the tab even if transport or terminal cleanup fails.
    if(session.socket) {
      try { send(session,{type:'stop'}); } catch(error) { console.warn('Terminal stop failed',error); }
      try { session.socket.close(); } catch(error) { console.warn('Terminal disconnect failed',error); }
    }
    try { session.terminal.dispose(); } catch(error) { console.warn('Terminal cleanup failed',error); }
  }
  const observer = new ResizeObserver(() => { if(selected) selected.fit.fit(); });
  observer.observe($('terminal-screen'));
  $('terminal-tabs').onkeydown = event => {
    if(event.key === 'Delete' && event.target.getAttribute('role') === 'tab') { event.preventDefault(); closeSession(sessions.find(s => s.button === event.target)); return; }
    if(!['ArrowDown','ArrowUp','ArrowLeft','ArrowRight','Home','End'].includes(event.key) || !sessions.length) return;
    event.preventDefault();
    let index = sessions.indexOf(selected);
    if(event.key === 'Home') index = 0;
    else if(event.key === 'End') index = sessions.length - 1;
    else index = (index + (['ArrowDown','ArrowRight'].includes(event.key) ? 1 : -1) + sessions.length) % sessions.length;
    select(sessions[index]); sessions[index].button.focus();
  };
  let nextTab = 0;
  function pane(cwd, label) {
    const number = ++nextTab;
    const terminal = new Terminal({cursorBlink:true, fontSize:13, scrollback:5000, screenReaderMode:true, theme:{background:'#0b0d14',foreground:'#e6e7ee'}});
    const fit = new FitAddon.FitAddon();
    const view = document.createElement('div'); view.className = 'terminal-session-view'; view.id = 'terminal-view-' + number; view.setAttribute('role','tabpanel');
    const button = document.createElement('button'); button.type = 'button'; button.className = 'terminal-session-tab'; button.id = 'terminal-tab-' + number; button.setAttribute('role','tab'); button.setAttribute('aria-controls',view.id); button.title = cwd;
    view.setAttribute('aria-labelledby',button.id);
    const name = document.createElement('span'); name.textContent = label || number + ' - ' + (cwd.split(/[\\/]/).filter(Boolean).pop() || 'PowerShell');
    const detail = document.createElement('small'); button.append(name,detail);
    const item = document.createElement('div'); item.className = 'terminal-session-item'; item.setAttribute('role','presentation');
    const close = document.createElement('button'); close.type = 'button'; close.className = 'terminal-session-close'; close.setAttribute('aria-label','Close ' + name.textContent); close.title = 'Close tab';
    close.onclick = () => closeSession(sessions.find(s => s.button === button));
    item.append(button,close); $('terminal-tabs').append(item); $('terminal-screen').append(view);
    terminal.loadAddon(fit); terminal.open(view);
    return {terminal,fit,view,button,detail};
  }
  $('terminal-start').onclick = () => {
    const cwd = $('terminal-cwd').value.trim();
    const {terminal,fit,view,button,detail} = pane(cwd);
    const socket = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/terminal?cwd=' + encodeURIComponent(cwd));
    const session = {terminal,fit,view,button,detail,socket,status:'Connecting...',ready:false};
    sessions.push(session); button.onclick = () => select(session); select(session);
    terminal.onData(data => send(session,{type:'input',data}));
    terminal.onResize(size => send(session,{type:'resize',rows:size.rows,cols:size.cols}));
    socket.onmessage = event => {
      if(session.closed) return;
      const data = JSON.parse(event.data);
      if(data.type === 'output') terminal.write(data.data);
      if(data.type === 'ready') { session.status = 'Connected - PowerShell'; session.ready = true; send(session,{type:'resize',rows:terminal.rows,cols:terminal.cols}); }
      if(data.type === 'error') session.status = data.message;
      refresh();
    };
    socket.onerror = () => { session.status = 'Connection failed. Check owner sign-in and the eight-session limit.'; refresh(); };
    socket.onclose = () => { session.ready = false; if(session.status.startsWith('Connected') || ['Connecting...', 'Stopping...'].includes(session.status)) session.status = 'Session closed'; refresh(); history(); };
  };
  async function json(url) { const r=await fetch(url); if(!r.ok) throw new Error('Request failed ('+r.status+')'); return r.json(); }
  async function history() {
    try {
      const rows=await json('/api/terminal/sessions'); $('terminal-history').replaceChildren();
      rows.forEach(row => { const b=document.createElement('button'); b.textContent=row.started_at.replace('T',' ')+' · '+row.status+' · '+row.cwd; b.onclick=async()=>{try {const data=await json('/api/terminal/sessions/'+row.id); $('terminal-transcript').textContent=data.transcript.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g,'');}catch(e){$('terminal-transcript').textContent=e.message;}}; $('terminal-history').append(b); });
    } catch(e) { $('terminal-history').textContent=e.message; }
  }
  async function activity() {
    try {
      const data=await json('/api/terminal/activity'); $('terminal-runs').replaceChildren(); $('terminal-events').replaceChildren();
      data.runs.forEach(run=>{const row=document.createElement('div');row.className='activity-row';const a=document.createElement('a');a.href='/issues/'+run.issue_id;a.textContent=run.title; a.dataset.noPjax=''; const small=document.createElement('small');small.textContent=run.agent+' · '+run.status.replaceAll('_',' ');row.append(a,small);$('terminal-runs').append(row);});
      data.events.forEach(event=>{const row=document.createElement('details');row.className='activity-row';const summary=document.createElement('summary');summary.textContent=event.type.replaceAll('_',' ');const pre=document.createElement('pre');pre.textContent=event.detail;row.append(summary,pre);$('terminal-events').append(row);});
      $('activity-status').textContent='Updated '+new Date().toLocaleTimeString();
    } catch(e) { $('activity-status').textContent=e.message; }
  }
  let leaving = false;
  async function agentTerminals() {
    try {
      const rows = await json('/api/terminal/agents');
      const live = new Set(rows.map(row => row.id));
      for(const session of [...sessions]) {
        if(!session.agentId || live.has(session.agentId)) continue;
        closeSession(session);
      }
      for(const id of dismissedAgents) { if(!live.has(id)) dismissedAgents.delete(id); }
      for(const row of rows) {
        if(dismissedAgents.has(row.id)) continue;
        let session = sessions.find(s => s.agentId === row.id);
        if(!session) {
          session = {...pane(row.cwd, row.label), agentId:row.id, socket:null, ready:false, status:'Agent running', output:''};
          session.terminal.options.disableStdin = true;
          session.view.hidden = true;
          sessions.push(session); session.button.onclick = () => select(session);
        }
        if(row.transcript !== session.output) {
          if(row.transcript.startsWith(session.output)) session.terminal.write(row.transcript.slice(session.output.length).replace(/\r?\n/g,'\r\n'));
          else { session.terminal.reset(); session.terminal.write(row.transcript.replace(/\r?\n/g,'\r\n')); }
          session.output = row.transcript;
        }
      }
      if(!selected && sessions.length) select(sessions[0]);
      refresh();
      $('agent-terminal-status').textContent = rows.length ? rows.length + ' agent terminal(s) running. Tabs close automatically when finished.' : 'Agent terminals appear here automatically while tasks run.';
    } catch(e) { $('agent-terminal-status').textContent = 'Agent terminals: ' + e.message; }
    finally { if(!leaving) setTimeout(agentTerminals,1000); }
  }
  // Avoid PJAX leaving a live shell behind in a detached page.
  document.querySelectorAll('a').forEach(a=>a.setAttribute('data-no-pjax',''));
  window.addEventListener('beforeunload',()=>{leaving = true;sessions.forEach(s => { if(s.socket) s.socket.close(); });});
  history(); activity(); agentTerminals(); setInterval(activity,4000);
})();
