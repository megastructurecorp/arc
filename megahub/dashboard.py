"""Embedded HTML dashboard for Megahub. Zero external dependencies."""

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Megahub Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:#0f172a;color:#e2e8f0;line-height:1.5;padding:1rem}
h1{font-size:1.5rem;font-weight:700;color:#38bdf8;margin-bottom:.25rem}
.subtitle{color:#64748b;font-size:.85rem;margin-bottom:1rem}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:1rem;margin-bottom:1rem}
.card{background:#1e293b;border:1px solid #334155;border-radius:.5rem;padding:1rem;overflow:hidden}
.card h2{font-size:1rem;font-weight:600;color:#94a3b8;margin-bottom:.75rem;
  display:flex;align-items:center;gap:.4rem}
.card h2 .count{background:#334155;color:#38bdf8;font-size:.75rem;
  padding:.1rem .45rem;border-radius:9999px}
table{width:100%;border-collapse:collapse;font-size:.82rem}
th{text-align:left;color:#64748b;font-weight:500;padding:.35rem .5rem;
  border-bottom:1px solid #334155;white-space:nowrap}
td{padding:.35rem .5rem;border-bottom:1px solid #1e293b;word-break:break-all;max-width:260px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
tr:hover td{background:#334155}
.badge{display:inline-block;padding:.1rem .4rem;border-radius:.25rem;font-size:.7rem;font-weight:600}
.badge-green{background:#064e3b;color:#34d399}
.badge-yellow{background:#713f12;color:#fbbf24}
.badge-red{background:#7f1d1d;color:#f87171}
.badge-blue{background:#1e3a5f;color:#60a5fa}
.badge-purple{background:#3b0764;color:#c084fc}
.msg-body{max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.status-bar{display:flex;align-items:center;gap:.5rem;margin-bottom:1rem;
  font-size:.78rem;color:#64748b}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.dot-green{background:#34d399}
.dot-red{background:#f87171}
.empty{color:#475569;font-style:italic;padding:.5rem}
#last-update{color:#64748b}
.full-width{grid-column:1/-1}
.clickable{cursor:pointer}
.clickable:hover td{background:#334155}
.thread-row-active td{background:#1e3a5f !important}
#thread-detail-card{display:none}
.detail-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1rem;margin-bottom:1rem}
.detail-section{background:#0f172a;border:1px solid #334155;border-radius:.375rem;padding:.75rem}
.detail-section h3{font-size:.85rem;font-weight:600;color:#94a3b8;margin-bottom:.5rem}
.thread-header{margin-bottom:1rem;padding-bottom:.75rem;border-bottom:1px solid #334155}
.thread-header .thread-title{font-size:1.1rem;font-weight:600;color:#38bdf8}
.thread-header .thread-meta{font-size:.78rem;color:#64748b;margin-top:.25rem}
.close-btn{float:right;background:none;border:1px solid #475569;color:#94a3b8;
  border-radius:.25rem;padding:.15rem .5rem;cursor:pointer;font-size:.75rem}
.close-btn:hover{background:#334155;color:#e2e8f0}
</style>
</head>
<body>
<h1>Megahub Dashboard</h1>
<div class="status-bar">
  <span class="dot dot-green" id="status-dot"></span>
  <span id="status-text">Connected</span>
  <span>&mdash;</span>
  <span id="last-update">loading...</span>
</div>
<div class="grid">
  <div class="card" id="agents-card">
    <h2>Agents <span class="count" id="agents-count">0</span></h2>
    <div id="agents-body"><div class="empty">Loading...</div></div>
  </div>
  <div class="card" id="claims-card">
    <h2>Active Claims <span class="count" id="claims-count">0</span></h2>
    <div id="claims-body"><div class="empty">Loading...</div></div>
  </div>
  <div class="card" id="locks-card">
    <h2>Active Locks <span class="count" id="locks-count">0</span></h2>
    <div id="locks-body"><div class="empty">Loading...</div></div>
  </div>
  <div class="card" id="hub-info-card">
    <h2>Hub Info</h2>
    <div id="hub-info-body"><div class="empty">Loading...</div></div>
  </div>
</div>
<div class="grid">
  <div class="card full-width" id="threads-card">
    <h2>Active Threads <span class="count" id="threads-count">0</span></h2>
    <div id="threads-body"><div class="empty">Loading...</div></div>
  </div>
</div>
<div class="grid">
  <div class="card full-width" id="thread-detail-card">
    <div class="thread-header">
      <button class="close-btn" onclick="closeDetail()">Close</button>
      <div class="thread-title" id="detail-title">Thread</div>
      <div class="thread-meta" id="detail-meta"></div>
    </div>
    <div class="detail-grid">
      <div class="detail-section" id="detail-tasks">
        <h3>Tasks</h3>
        <div id="detail-tasks-body"><div class="empty">None</div></div>
      </div>
      <div class="detail-section" id="detail-claims">
        <h3>Active Claims</h3>
        <div id="detail-claims-body"><div class="empty">None</div></div>
      </div>
      <div class="detail-section" id="detail-locks">
        <h3>Active Locks</h3>
        <div id="detail-locks-body"><div class="empty">None</div></div>
      </div>
    </div>
    <h2>Thread Messages <span class="count" id="detail-msg-count">0</span></h2>
    <div id="detail-messages-body" style="max-height:400px;overflow-y:auto">
      <div class="empty">No messages</div>
    </div>
  </div>
</div>
<script>
const BASE='';
let errCount=0;
let selectedThread=null;
let detailTimer=null;

function $(id){return document.getElementById(id)}
function esc(s){if(s==null)return'';const d=document.createElement('div');d.textContent=String(s);return d.innerHTML}
function timeFmt(iso){if(!iso)return'-';try{return new Date(iso).toLocaleTimeString()}catch(e){return iso}}
function timeAgo(iso){
  if(!iso)return'-';
  try{
    const diff=Math.floor((Date.now()-new Date(iso).getTime())/1000);
    if(diff<0)return'just now';
    if(diff<60)return diff+'s ago';
    const m=Math.floor(diff/60);if(m<60)return m+'m ago';
    const h=Math.floor(m/60);if(h<24)return h+'h ago';
    return Math.floor(h/24)+'d ago';
  }catch(e){return iso}
}
function badge(text,cls){return '<span class="badge badge-'+cls+'">'+esc(text)+'</span>'}
function statusBadge(s){
  const m={open:'green',waiting:'yellow',completed:'blue'};
  return badge(s,m[s]||'blue');
}
function kindBadge(k){
  const m={chat:'blue',notice:'yellow',task:'purple',artifact:'green',claim:'yellow',release:'red'};
  return badge(k,m[k]||'blue');
}

async function fetchJSON(path){
  const r=await fetch(BASE+path);
  const j=await r.json();
  return j.ok?j.result:[];
}

function renderAgents(agents){
  $('agents-count').textContent=agents.length;
  if(!agents.length){$('agents-body').innerHTML='<div class="empty">No active agents</div>';return}
  let h='<table><tr><th>Agent</th><th>Display</th><th>Last Seen</th><th>Session</th></tr>';
  for(const a of agents){
    h+='<tr><td>'+esc(a.agent_id)+'</td><td>'+esc(a.display_name)+'</td>';
    h+='<td>'+timeFmt(a.last_seen)+'</td>';
    h+='<td style="font-size:.7rem;color:#64748b">'+esc(a.session_id.slice(0,8))+'</td></tr>';
  }
  $('agents-body').innerHTML=h+'</table>';
}

function renderClaims(claims){
  const active=claims.filter(c=>!c.released_at);
  $('claims-count').textContent=active.length;
  if(!active.length){$('claims-body').innerHTML='<div class="empty">No active claims</div>';return}
  let h='<table><tr><th>Key</th><th>Owner</th><th>Thread</th><th>Expires</th></tr>';
  for(const c of active){
    h+='<tr><td>'+esc(c.claim_key)+'</td><td>'+esc(c.owner_agent_id)+'</td>';
    h+='<td>'+esc(c.thread_id||'-')+'</td><td>'+timeFmt(c.expires_at)+'</td></tr>';
  }
  $('claims-body').innerHTML=h+'</table>';
}

function renderLocks(locks){
  const active=locks.filter(l=>!l.released_at);
  $('locks-count').textContent=active.length;
  if(!active.length){$('locks-body').innerHTML='<div class="empty">No active locks</div>';return}
  let h='<table><tr><th>File</th><th>Agent</th><th>Locked</th><th>Expires</th></tr>';
  for(const l of active){
    h+='<tr><td>'+esc(l.file_path)+'</td><td>'+esc(l.agent_id)+'</td>';
    h+='<td>'+timeFmt(l.locked_at)+'</td><td>'+timeFmt(l.expires_at)+'</td></tr>';
  }
  $('locks-body').innerHTML=h+'</table>';
}

function renderThreads(threads){
  $('threads-count').textContent=threads.length;
  if(!threads.length){$('threads-body').innerHTML='<div class="empty">No active threads</div>';return}
  let h='<table><tr><th>Thread</th><th>Channel</th><th>Status</th><th>Tasks</th><th>Claims</th><th>Locks</th><th>Last Activity</th></tr>';
  for(const t of threads){
    const active=selectedThread===t.thread_id?' thread-row-active':'';
    h+='<tr class="clickable'+active+'" onclick="openThread(\''+esc(t.thread_id)+'\')">';
    h+='<td>'+esc(t.thread_id)+'</td>';
    h+='<td>'+esc(t.channel||'-')+'</td>';
    h+='<td>'+statusBadge(t.status)+'</td>';
    h+='<td>'+t.open_task_count+'/'+t.total_task_count+'</td>';
    h+='<td>'+t.active_claim_count+'</td>';
    h+='<td>'+t.active_lock_count+'</td>';
    h+='<td>'+timeAgo(t.latest_message_ts)+'</td></tr>';
  }
  $('threads-body').innerHTML=h+'</table>';
}

function renderDetailTasks(tasks,messages){
  if(!tasks.length){$('detail-tasks-body').innerHTML='<div class="empty">None</div>';return}
  let h='<table><tr><th>ID</th><th>Status</th><th>Description</th></tr>';
  for(const t of tasks){
    const msg=messages.find(m=>m.id===t.task_id);
    const body=msg?msg.body.slice(0,80):'';
    const st=t.status==='open'?badge('open','green'):badge(t.status,'blue');
    h+='<tr><td>#'+t.task_id+'</td><td>'+st+'</td><td class="msg-body" title="'+esc(body)+'">'+esc(body)+'</td></tr>';
  }
  $('detail-tasks-body').innerHTML=h+'</table>';
}

function renderDetailClaims(claims){
  const now=Date.now();
  const active=claims.filter(c=>!c.released_at&&new Date(c.expires_at).getTime()>=now);
  if(!active.length){$('detail-claims-body').innerHTML='<div class="empty">None</div>';return}
  let h='<table><tr><th>Key</th><th>Owner</th><th>Expires</th></tr>';
  for(const c of active){
    const mins=Math.max(0,Math.floor((new Date(c.expires_at).getTime()-now)/60000));
    h+='<tr><td>'+esc(c.claim_key)+'</td><td>'+esc(c.owner_agent_id)+'</td><td>'+mins+'m</td></tr>';
  }
  $('detail-claims-body').innerHTML=h+'</table>';
}

function renderDetailLocks(locks){
  const now=Date.now();
  const active=locks.filter(l=>!l.released_at&&new Date(l.expires_at).getTime()>=now);
  if(!active.length){$('detail-locks-body').innerHTML='<div class="empty">None</div>';return}
  let h='<table><tr><th>File</th><th>Agent</th><th>Expires</th></tr>';
  for(const l of active){
    const mins=Math.max(0,Math.floor((new Date(l.expires_at).getTime()-now)/60000));
    h+='<tr><td>'+esc(l.file_path)+'</td><td>'+esc(l.agent_id)+'</td><td>'+mins+'m</td></tr>';
  }
  $('detail-locks-body').innerHTML=h+'</table>';
}

function renderDetailMessages(messages){
  $('detail-msg-count').textContent=messages.length;
  if(!messages.length){$('detail-messages-body').innerHTML='<div class="empty">No messages</div>';return}
  let h='<table><tr><th>ID</th><th>Time</th><th>From</th><th>Kind</th><th>Body</th></tr>';
  for(const m of messages){
    h+='<tr><td>'+m.id+'</td><td>'+timeFmt(m.ts)+'</td>';
    h+='<td>'+esc(m.from_agent)+'</td><td>'+kindBadge(m.kind)+'</td>';
    h+='<td class="msg-body" title="'+esc(m.body)+'">'+esc(m.body)+'</td></tr>';
  }
  $('detail-messages-body').innerHTML=h+'</table>';
}

async function openThread(threadId){
  selectedThread=threadId;
  $('thread-detail-card').style.display='block';
  await refreshDetail();
  if(detailTimer)clearInterval(detailTimer);
  detailTimer=setInterval(refreshDetail,3000);
}

function closeDetail(){
  selectedThread=null;
  $('thread-detail-card').style.display='none';
  if(detailTimer){clearInterval(detailTimer);detailTimer=null}
}

async function refreshDetail(){
  if(!selectedThread)return;
  try{
    const r=await fetch(BASE+'/v1/threads/'+encodeURIComponent(selectedThread));
    const j=await r.json();
    if(!j.ok)return;
    const d=j.result;
    const t=d.thread||{};
    $('detail-title').textContent=t.thread_id||'?';
    $('detail-meta').textContent='Channel: '+(t.channel||'?')+' | Status: '+(t.status||'?')+
      ' | Messages: '+(t.message_count||0)+' | Last activity: '+timeAgo(t.latest_message_ts);
    renderDetailTasks(d.tasks||[],d.messages||[]);
    renderDetailClaims(d.claims||[]);
    renderDetailLocks(d.locks||[]);
    renderDetailMessages(d.messages||[]);
  }catch(e){console.error('Detail refresh error:',e)}
}

function renderHubInfo(info){
  if(!info||!info.instance_id){
    $('hub-info-body').innerHTML='<div class="empty">Not available (hub may not support /v1/hub-info yet)</div>';
    return;
  }
  let h='<table>';
  h+='<tr><td style="color:#64748b;width:100px">Instance</td><td style="font-size:.75rem">'+esc(info.instance_id)+'</td></tr>';
  if(info.storage_path)h+='<tr><td style="color:#64748b">Storage</td><td style="font-size:.75rem;word-break:break-all;white-space:normal">'+esc(info.storage_path)+'</td></tr>';
  if(info.wal_mode!==undefined){
    const walBadge=info.wal_mode?badge('WAL enabled','green'):badge('WAL disabled','red');
    h+='<tr><td style="color:#64748b">Journal</td><td>'+walBadge+'</td></tr>';
  }
  h+='</table>';
  $('hub-info-body').innerHTML=h;
}

async function refresh(){
  try{
    let hubInfo=null;
    try{
      const r=await fetch(BASE+'/v1/hub-info');
      const j=await r.json();
      if(j.ok)hubInfo=j.result;
    }catch(e){}
    const [agents,claims,locks,threads]=await Promise.all([
      fetchJSON('/v1/agents'),
      fetchJSON('/v1/claims'),
      fetchJSON('/v1/locks'),
      fetchJSON('/v1/threads')
    ]);
    renderAgents(agents);
    renderClaims(claims);
    renderLocks(locks);
    renderThreads(threads);
    renderHubInfo(hubInfo);

    $('status-dot').className='dot dot-green';
    $('status-text').textContent='Connected';
    $('last-update').textContent='Updated '+new Date().toLocaleTimeString();
    errCount=0;
  }catch(e){
    errCount++;
    $('status-dot').className='dot dot-red';
    $('status-text').textContent='Error (retry in 5s)';
    console.error('Dashboard refresh error:',e);
  }
}

refresh();
setInterval(refresh,5000);
</script>
</body>
</html>"""
