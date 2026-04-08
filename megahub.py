#!/usr/bin/env python3
"""Megahub — single-file local-first agent coordination hub.

    python megahub_single.py [--port 8765] [--storage megahub.sqlite3]

Zero dependencies beyond Python 3.10+. Provides 12 REST endpoints for
multi-agent coordination via HTTP + SQLite.

Deployment modes:
  1. Single hub: one process serves all agents (default).
  2. Shared-filesystem: multiple hub processes on different ports or
     machines, all pointing --storage at the SAME SQLite file on a
     shared/mounted filesystem. SQLite WAL mode handles concurrent
     access. Messages, claims, and locks are visible across all hubs.

Shared-filesystem example (two sandboxed agents):
  # Sandbox A:  python megahub_single.py --port 8765 --storage /shared/megahub.sqlite3
  # Sandbox B:  python megahub_single.py --port 9876 --storage /shared/megahub.sqlite3
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, sqlite3, tempfile, threading, time, uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import parse_qs, urlparse

__version__ = "0.1.0"
LOCAL_BIND_HOSTS = {"127.0.0.1", "localhost", "::1"}

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
_utcnow = lambda: datetime.now(timezone.utc)
_to_iso = lambda dt: dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
def _from_iso(v):
    return datetime.fromisoformat(v[:-1] + "+00:00" if v.endswith("Z") else v)


def utcnow():
    return _utcnow()


def to_iso(dt):
    return _to_iso(dt)


def from_iso(value):
    return _from_iso(value)

PIDFILE_NAME = ".megahub.pid"
def _storage_dir(storage_path):
    p = Path(storage_path)
    if not p.is_absolute(): p = Path.cwd() / p
    return p.resolve().parent
def _pidfile_path(storage_path): return _storage_dir(storage_path) / PIDFILE_NAME
def _storage_path(storage_path):
    p = Path(storage_path).expanduser()
    if not p.is_absolute(): p = Path.cwd() / p
    return p.resolve()
def _pidfile_url(host, port):
    host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return f"http://[{host}]:{port}" if ":" in host and not host.startswith("[") else f"http://{host}:{port}"
def _instance_fingerprint(storage_path, birth_marker):
    material = f"{Path(storage_path).resolve()}|{birth_marker}".encode("utf-8")
    return f"mh1-{hashlib.sha256(material).hexdigest()[:20]}"
def _read_pidfile(path):
    try: payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return None
    if not isinstance(payload, dict): return None
    try: return {"pid": int(payload["pid"]), "port": int(payload["port"]), "url": str(payload["url"]), "path": str(path)}
    except (KeyError, TypeError, ValueError): return None
def _pidfile_candidates(storage_path):
    out, seen = [], set()
    for root in (Path.cwd().resolve(), _storage_dir(storage_path)):
        for base in (root, *root.parents):
            candidate = base / PIDFILE_NAME
            key = str(candidate)
            if key in seen: continue
            seen.add(key); out.append(candidate)
    return out
def _discover_pidfile(storage_path):
    for candidate in _pidfile_candidates(storage_path):
        info = _read_pidfile(candidate)
        if info is not None: return info
    return None


_candidate_pidfiles = _pidfile_candidates

@dataclass(slots=True)
class HubConfig:
    listen_host: str = "127.0.0.1"; port: int = 8765; allow_remote: bool = False
    storage_path: str = "megahub.sqlite3"; log_events: bool = True
    presence_ttl_sec: int = 120; max_body_chars: int = 16_000
    max_attachment_chars: int = 32_000; max_attachments: int = 16; max_query_limit: int = 500
    def validate(self):
        if self.port < 0 or self.port > 65535: raise ValueError("port out of range")
        if self.presence_ttl_sec < 5: raise ValueError("presence_ttl_sec must be >= 5")
        if self.max_body_chars < 1: raise ValueError("max_body_chars must be at least 1")
        if self.max_attachment_chars < 1: raise ValueError("max_attachment_chars must be at least 1")
        if self.max_attachments < 0: raise ValueError("max_attachments must be non-negative")
        if self.max_query_limit < 1: raise ValueError("max_query_limit must be at least 1")
        if not self.allow_remote and self.listen_host not in LOCAL_BIND_HOSTS:
            raise ValueError("Remote bind requires allow_remote=true")
        storage_path = _storage_path(self.storage_path)
        if storage_path.exists() and storage_path.is_dir():
            raise ValueError("storage_path must point to a file, not a directory")
        try:
            storage_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ValueError(f"unable to create storage directory: {storage_path.parent}") from e
        try:
            if storage_path.exists():
                with storage_path.open("ab"): pass
            else:
                with tempfile.NamedTemporaryFile(dir=storage_path.parent, prefix=".megahub-write-check-", delete=True): pass
        except OSError as e:
            raise ValueError(f"storage_path is not writable: {storage_path}") from e

# ── Storage ───────────────────────────────────────────────────────────
class HubStore:
    _SCHEMA = """
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS channels(name TEXT PRIMARY KEY,created_at TEXT NOT NULL,
        created_by TEXT,metadata_json TEXT NOT NULL DEFAULT '{}');
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,ts TEXT NOT NULL,
        from_agent TEXT NOT NULL,to_agent TEXT,channel TEXT NOT NULL,kind TEXT NOT NULL,
        body TEXT NOT NULL,attachments_json TEXT NOT NULL DEFAULT '[]',
        reply_to INTEGER,thread_id TEXT,metadata_json TEXT NOT NULL DEFAULT '{}');
    CREATE INDEX IF NOT EXISTS ix_m_ch ON messages(channel,id);
    CREATE INDEX IF NOT EXISTS ix_m_to ON messages(to_agent,id);
    CREATE INDEX IF NOT EXISTS ix_m_th ON messages(thread_id,id);
    CREATE TABLE IF NOT EXISTS sessions(session_id TEXT PRIMARY KEY,agent_id TEXT NOT NULL,
        display_name TEXT NOT NULL,capabilities_json TEXT NOT NULL DEFAULT '[]',
        metadata_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,
        last_seen TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
    CREATE INDEX IF NOT EXISTS ix_s_ag ON sessions(agent_id);
    CREATE INDEX IF NOT EXISTS ix_s_ac ON sessions(active,last_seen);
    CREATE TABLE IF NOT EXISTS claims(claim_key TEXT PRIMARY KEY,thread_id TEXT,
        task_message_id INTEGER,owner_agent_id TEXT NOT NULL,claimed_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,released_at TEXT,metadata_json TEXT NOT NULL DEFAULT '{}');
    CREATE INDEX IF NOT EXISTS ix_c_th ON claims(thread_id);
    CREATE TABLE IF NOT EXISTS locks(file_path TEXT PRIMARY KEY,agent_id TEXT NOT NULL,
        locked_at TEXT NOT NULL,expires_at TEXT NOT NULL,released_at TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}');
    CREATE INDEX IF NOT EXISTS ix_l_ag ON locks(agent_id);
    CREATE TABLE IF NOT EXISTS tasks(task_id INTEGER PRIMARY KEY,parent_task_id INTEGER,
        channel TEXT NOT NULL,thread_id TEXT,status TEXT NOT NULL DEFAULT 'open',
        created_at TEXT NOT NULL,completed_at TEXT,
        FOREIGN KEY(parent_task_id) REFERENCES tasks(task_id));
    CREATE INDEX IF NOT EXISTS ix_t_par ON tasks(parent_task_id);
    CREATE INDEX IF NOT EXISTS ix_t_st ON tasks(status);"""

    def __init__(self, db_path: str):
        self.db_path = _storage_path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lk = threading.RLock()
        self._db = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._conn = self._db
        self._lock = self._lk
        self.journal_mode = "unknown"
        with self._lk:
            self._db.executescript(self._SCHEMA)
            row = self._db.execute("PRAGMA journal_mode").fetchone()
            if row is not None: self.journal_mode = str(row[0]).lower()
            self._db.commit()
        for ch in ("general", "direct"):
            now = _to_iso(_utcnow())
            with self._lk:
                self._db.execute("INSERT OR IGNORE INTO channels VALUES(?,?,?,?)", (ch, now, "system", "{}"))
                self._db.commit()

    def close(self):
        with self._lk: self._db.close()

    @property
    def wal_enabled(self):
        return self.journal_mode == "wal"

    def get_storage_info(self):
        return {
            "storage_path": str(self.db_path),
            "journal_mode": self.journal_mode,
            "wal_mode": self.wal_enabled,
        }

    def get_channel(self, name):
        with self._lk:
            r = self._db.execute("SELECT * FROM channels WHERE name=?", (name,)).fetchone()
        return self._ch(r) if r else None

    def list_channels(self):
        with self._lk:
            return [self._ch(r) for r in self._db.execute("SELECT * FROM channels ORDER BY name").fetchall()]

    def create_channel(self, name, created_by, metadata):
        e = self.get_channel(name)
        if e: return e, False
        now = _to_iso(_utcnow())
        with self._lk:
            self._db.execute("INSERT OR IGNORE INTO channels VALUES(?,?,?,?)",
                             (name, now, created_by, json.dumps(metadata or {})))
            self._db.commit()
        return self.get_channel(name), True

    def create_session(self, agent_id, display_name, capabilities, metadata, replace, ttl_sec):
        now, deact = _utcnow(), []
        with self._lk:
            act = self._db.execute("SELECT * FROM sessions WHERE agent_id=? AND active=1 ORDER BY created_at DESC LIMIT 1", (agent_id,)).fetchone()
            if act:
                if _from_iso(act["last_seen"]) >= (now - timedelta(seconds=ttl_sec)) and not replace:
                    raise ValueError("agent_id already has an active session")
                self._db.execute("UPDATE sessions SET active=0 WHERE session_id=?", (act["session_id"],))
                deact.append(self._ss(act))
            sid, iso = str(uuid.uuid4()), _to_iso(now)
            self._db.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,1)",
                (sid, agent_id, display_name or agent_id, json.dumps(list(capabilities or [])),
                 json.dumps(dict(metadata or {})), iso, iso))
            self._db.commit()
        s = {"session_id": sid, "agent_id": agent_id, "display_name": display_name or agent_id,
             "capabilities": list(capabilities or []), "metadata": dict(metadata or {}),
             "created_at": iso, "last_seen": iso, "active": True}
        return s, deact

    def get_session(self, sid):
        with self._lk: r = self._db.execute("SELECT * FROM sessions WHERE session_id=?", (sid,)).fetchone()
        return self._ss(r) if r else None

    def delete_session(self, sid):
        with self._lk:
            r = self._db.execute("SELECT * FROM sessions WHERE session_id=? AND active=1", (sid,)).fetchone()
            if not r: return None
            self._db.execute("UPDATE sessions SET active=0 WHERE session_id=?", (sid,)); self._db.commit()
        return self._ss(r)

    def touch_agent_session(self, agent_id):
        with self._lk:
            self._db.execute("UPDATE sessions SET last_seen=? WHERE agent_id=? AND active=1", (_to_iso(_utcnow()), agent_id))
            self._db.commit()

    def prune_expired(self, ttl_sec):
        c = _to_iso(_utcnow() - timedelta(seconds=ttl_sec))
        with self._lk:
            rows = self._db.execute("SELECT * FROM sessions WHERE active=1 AND last_seen<?", (c,)).fetchall()
            if rows: self._db.execute("UPDATE sessions SET active=0 WHERE active=1 AND last_seen<?", (c,)); self._db.commit()
        return [self._ss(r) for r in rows]

    def prune_expired_sessions(self, ttl_sec):
        return self.prune_expired(ttl_sec)

    def list_live_agents(self, ttl_sec):
        self.prune_expired(ttl_sec)
        with self._lk:
            return [self._ss(r) for r in self._db.execute("SELECT * FROM sessions WHERE active=1 ORDER BY agent_id").fetchall()]

    def create_message(self, **kw):
        now = _to_iso(_utcnow())
        with self._lk:
            cur = self._db.execute("INSERT INTO messages(ts,from_agent,to_agent,channel,kind,body,"
                "attachments_json,reply_to,thread_id,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (now, kw["from_agent"], kw["to_agent"], kw["channel"], kw["kind"], kw["body"],
                 json.dumps(kw["attachments"]), kw["reply_to"], kw["thread_id"], json.dumps(kw["metadata"] or {})))
            self._db.commit()
            r = self._db.execute("SELECT * FROM messages WHERE id=?", (cur.lastrowid,)).fetchone()
        return self._mg(r)

    def list_channel_messages(self, ch, since_id=0, limit=100):
        with self._lk:
            return [self._mg(r) for r in self._db.execute(
                "SELECT * FROM messages WHERE channel=? AND to_agent IS NULL AND id>? ORDER BY id LIMIT ?", (ch, since_id, limit)).fetchall()]

    def list_inbox_messages(self, agent_id, since_id=0, limit=100):
        with self._lk:
            return [self._mg(r) for r in self._db.execute(
                "SELECT * FROM messages WHERE to_agent=? AND id>? ORDER BY id LIMIT ?", (agent_id, since_id, limit)).fetchall()]

    def list_visible_messages_for_agent(self, agent_id, since_id=0, limit=500, *, channel=None, thread_id=None):
        conds = ["id>?", "(to_agent IS NULL OR to_agent=?)"]
        params: list[Any] = [since_id, agent_id]
        if channel is not None:
            conds.append("channel=?")
            params.append(channel)
        if thread_id is not None:
            conds.append("thread_id=?")
            params.append(thread_id)
        params.append(limit)
        with self._lk:
            rows = self._db.execute(
                f"SELECT * FROM messages WHERE {' AND '.join(conds)} ORDER BY id LIMIT ?",
                params,
            ).fetchall()
        return [self._mg(r) for r in rows]

    def list_thread_messages(self, tid, channel=None, since_id=0, limit=100, *, include_direct=False):
        with self._lk:
            if channel:
                vis = "" if include_direct else "AND to_agent IS NULL"
                rows = self._db.execute(
                    f"SELECT * FROM messages WHERE thread_id=? AND channel=? {vis} AND id>? ORDER BY id LIMIT ?",
                    (tid, channel, since_id, limit),
                ).fetchall()
            else:
                vis = "" if include_direct else "AND to_agent IS NULL"
                rows = self._db.execute(
                    f"SELECT * FROM messages WHERE thread_id=? {vis} AND id>? ORDER BY id LIMIT ?",
                    (tid, since_id, limit),
                ).fetchall()
        return [self._mg(r) for r in rows]

    def list_all_thread_messages(self, tid):
        with self._lk:
            rows = self._db.execute("SELECT * FROM messages WHERE thread_id=? ORDER BY id", (tid,)).fetchall()
        return [self._mg(r) for r in rows]

    def acquire_claim(self, *, claim_key, thread_id, task_message_id, owner_agent_id, ttl_sec=300, metadata=None):
        now, exp = _utcnow(), _to_iso(_utcnow() + timedelta(seconds=ttl_sec))
        with self._lk:
            ex = self._db.execute("SELECT * FROM claims WHERE claim_key=?", (claim_key,)).fetchone()
            if ex:
                alive = ex["released_at"] is None and _from_iso(ex["expires_at"]) >= now
                if alive:
                    if ex["owner_agent_id"] == owner_agent_id:
                        self._db.execute("UPDATE claims SET expires_at=? WHERE claim_key=?", (exp, claim_key)); self._db.commit()
                        return self._cl(self._db.execute("SELECT * FROM claims WHERE claim_key=?", (claim_key,)).fetchone()), True
                    return self._cl(ex), False
                self._db.execute("UPDATE claims SET thread_id=?,task_message_id=?,owner_agent_id=?,claimed_at=?,expires_at=?,released_at=NULL,metadata_json=? WHERE claim_key=?",
                    (thread_id, task_message_id, owner_agent_id, _to_iso(now), exp, json.dumps(metadata or {}), claim_key))
            else:
                self._db.execute("INSERT INTO claims VALUES(?,?,?,?,?,?,NULL,?)",
                    (claim_key, thread_id, task_message_id, owner_agent_id, _to_iso(now), exp, json.dumps(metadata or {})))
            self._db.commit()
            return self._cl(self._db.execute("SELECT * FROM claims WHERE claim_key=?", (claim_key,)).fetchone()), True

    def release_claim(self, claim_key, agent_id):
        with self._lk:
            ex = self._db.execute("SELECT * FROM claims WHERE claim_key=?", (claim_key,)).fetchone()
            if not ex or ex["owner_agent_id"] != agent_id: return None
            if ex["released_at"] is not None: return self._cl(ex)
            self._db.execute("UPDATE claims SET released_at=? WHERE claim_key=?", (_to_iso(_utcnow()), claim_key)); self._db.commit()
            return self._cl(self._db.execute("SELECT * FROM claims WHERE claim_key=?", (claim_key,)).fetchone())

    def release_claim_force(self, claim_key):
        with self._lk:
            ex = self._db.execute("SELECT * FROM claims WHERE claim_key=?", (claim_key,)).fetchone()
            if not ex: return None
            if ex["released_at"] is not None: return self._cl(ex)
            self._db.execute("UPDATE claims SET released_at=? WHERE claim_key=?", (_to_iso(_utcnow()), claim_key)); self._db.commit()
            return self._cl(self._db.execute("SELECT * FROM claims WHERE claim_key=?", (claim_key,)).fetchone())

    def list_claims(self, thread_id=None, active_only=False, owner_agent_id=None):
        conds, params = [], []
        if thread_id is not None: conds.append("thread_id=?"); params.append(thread_id)
        if owner_agent_id is not None: conds.append("owner_agent_id=?"); params.append(owner_agent_id)
        if active_only: conds += ["released_at IS NULL", "expires_at>=?"]; params.append(_to_iso(_utcnow()))
        w = " AND ".join(conds) if conds else "1=1"
        with self._lk:
            return [self._cl(r) for r in self._db.execute(f"SELECT * FROM claims WHERE {w} ORDER BY claimed_at", params).fetchall()]

    def refresh_claim(self, claim_key, owner_agent_id, *, ttl_sec=300):
        now = _utcnow()
        exp = _to_iso(now + timedelta(seconds=ttl_sec))
        with self._lk:
            ex = self._db.execute("SELECT * FROM claims WHERE claim_key=?", (claim_key,)).fetchone()
            if not ex or ex["owner_agent_id"] != owner_agent_id: return None
            if ex["released_at"] is not None or _from_iso(ex["expires_at"]) < now: return None
            self._db.execute("UPDATE claims SET expires_at=? WHERE claim_key=?", (exp, claim_key))
            self._db.commit()
            return self._cl(self._db.execute("SELECT * FROM claims WHERE claim_key=?", (claim_key,)).fetchone())

    def create_task(self, *, message_id, parent_task_id, channel, thread_id):
        now = _to_iso(_utcnow())
        with self._lk:
            self._db.execute("INSERT OR IGNORE INTO tasks VALUES(?,?,?,?,'open',?,NULL)",
                (message_id, parent_task_id, channel, thread_id, now))
            self._db.commit()
            r = self._db.execute("SELECT * FROM tasks WHERE task_id=?", (message_id,)).fetchone()
        return self._tk(r)

    def complete_task(self, task_id):
        now = _to_iso(_utcnow())
        with self._lk:
            r = self._db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not r: return None
            if r["status"] == "done": return self._tk(r)
            self._db.execute("UPDATE tasks SET status='done',completed_at=? WHERE task_id=?", (now, task_id))
            self._db.commit()
            r = self._db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return self._tk(r)

    def list_tasks(self, parent_id=None, status=None, channel=None, thread_id=None):
        conds, params = [], []
        if parent_id is not None: conds.append("parent_task_id=?"); params.append(parent_id)
        if status is not None: conds.append("status=?"); params.append(status)
        if channel is not None: conds.append("channel=?"); params.append(channel)
        if thread_id is not None: conds.append("thread_id=?"); params.append(thread_id)
        w = " AND ".join(conds) if conds else "1=1"
        with self._lk:
            return [self._tk(r) for r in self._db.execute(f"SELECT * FROM tasks WHERE {w} ORDER BY task_id", params).fetchall()]

    def get_task(self, task_id):
        with self._lk:
            r = self._db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return self._tk(r) if r else None

    def check_parent_completion(self, task_id):
        t = self.get_task(task_id)
        if not t or t["parent_task_id"] is None: return None
        siblings = self.list_tasks(parent_id=t["parent_task_id"])
        return all(s["status"] == "done" for s in siblings)

    def list_threads(self):
        tids = self._list_thread_ids()
        summaries = [self.get_thread_summary(tid) for tid in tids]
        present = [item for item in summaries if item is not None]
        present.sort(
            key=lambda item: (
                item["latest_message_id"] if item["latest_message_id"] is not None else -1,
                item["thread_id"],
            ),
            reverse=True,
        )
        return present

    def get_thread_summary(self, thread_id):
        messages = self.list_all_thread_messages(thread_id)
        tasks = self.list_tasks(thread_id=thread_id)
        claims = self.list_claims(thread_id=thread_id)
        locks = self.list_thread_locks(thread_id)
        if not messages and not tasks and not claims and not locks:
            return None
        return self._build_thread_summary(thread_id, messages=messages, tasks=tasks, claims=claims, locks=locks)

    def get_thread_detail(self, thread_id):
        messages = self.list_all_thread_messages(thread_id)
        tasks = self.list_tasks(thread_id=thread_id)
        claims = self.list_claims(thread_id=thread_id)
        locks = self.list_thread_locks(thread_id)
        summary = self._build_thread_summary(thread_id, messages=messages, tasks=tasks, claims=claims, locks=locks)
        if summary is None:
            return None
        return {
            "thread": summary,
            "messages": messages,
            "tasks": tasks,
            "claims": claims,
            "locks": locks,
        }

    def list_thread_locks(self, thread_id, *, active_only=False):
        return [
            lock for lock in self.list_locks(active_only=active_only)
            if isinstance(lock.get("metadata"), dict) and lock["metadata"].get("thread_id") == thread_id
        ]

    def _list_thread_ids(self):
        with self._lk:
            rows = self._db.execute(
                """
                SELECT thread_id FROM messages WHERE thread_id IS NOT NULL
                UNION
                SELECT thread_id FROM tasks WHERE thread_id IS NOT NULL
                UNION
                SELECT thread_id FROM claims WHERE thread_id IS NOT NULL
                ORDER BY thread_id
                """
            ).fetchall()
        return [str(r["thread_id"]) for r in rows]

    def _build_thread_summary(self, thread_id, *, messages, tasks, claims, locks):
        if not messages and not tasks and not claims and not locks:
            return None
        latest = messages[-1] if messages else None
        artifact_ids = [m["id"] for m in messages if m.get("kind") == "artifact"]
        now = _utcnow()
        active_claims = [c for c in claims if c["released_at"] is None and _from_iso(c["expires_at"]) >= now]
        active_locks = [l for l in locks if l["released_at"] is None and _from_iso(l["expires_at"]) >= now]
        root_tasks = [t for t in tasks if t["parent_task_id"] is None]
        root_task_id = min((t["task_id"] for t in root_tasks), default=None)
        open_task_count = sum(1 for t in tasks if t["status"] == "open")
        total_task_count = len(tasks)
        if total_task_count > 0 and open_task_count == 0:
            status = "completed"
        elif active_claims or active_locks:
            status = "open"
        else:
            status = "waiting"
        channel = None
        if root_tasks:
            channel = sorted(root_tasks, key=lambda item: item["task_id"])[0]["channel"]
        elif messages:
            channel = messages[0]["channel"]
        elif tasks:
            channel = tasks[0]["channel"]
        return {
            "thread_id": thread_id,
            "channel": channel,
            "root_task_id": root_task_id,
            "latest_message_id": latest["id"] if latest else None,
            "latest_message_ts": latest["ts"] if latest else None,
            "latest_artifact_id": max(artifact_ids) if artifact_ids else None,
            "message_count": len(messages),
            "total_task_count": total_task_count,
            "open_task_count": open_task_count,
            "active_claim_count": len(active_claims),
            "active_lock_count": len(active_locks),
            "status": status,
        }

    def _tk(self, r): return {"task_id":r["task_id"],"parent_task_id":r["parent_task_id"],"channel":r["channel"],"thread_id":r["thread_id"],"status":r["status"],"created_at":r["created_at"],"completed_at":r["completed_at"]}

    def acquire_lock(self, *, file_path, agent_id, ttl_sec=300, metadata=None):
        now, exp = _utcnow(), _to_iso(_utcnow() + timedelta(seconds=ttl_sec))
        with self._lk:
            ex = self._db.execute("SELECT * FROM locks WHERE file_path=?", (file_path,)).fetchone()
            if ex:
                alive = ex["released_at"] is None and _from_iso(ex["expires_at"]) >= now
                if alive:
                    if ex["agent_id"] == agent_id:
                        self._db.execute("UPDATE locks SET expires_at=? WHERE file_path=?", (exp, file_path)); self._db.commit()
                        return self._lk_row(self._db.execute("SELECT * FROM locks WHERE file_path=?", (file_path,)).fetchone()), True
                    return self._lk_row(ex), False
                self._db.execute("UPDATE locks SET agent_id=?,locked_at=?,expires_at=?,released_at=NULL,metadata_json=? WHERE file_path=?",
                    (agent_id, _to_iso(now), exp, json.dumps(metadata or {}), file_path))
            else:
                self._db.execute("INSERT INTO locks VALUES(?,?,?,?,NULL,?)",
                    (file_path, agent_id, _to_iso(now), exp, json.dumps(metadata or {})))
            self._db.commit()
            return self._lk_row(self._db.execute("SELECT * FROM locks WHERE file_path=?", (file_path,)).fetchone()), True

    def release_lock(self, file_path, agent_id):
        with self._lk:
            ex = self._db.execute("SELECT * FROM locks WHERE file_path=?", (file_path,)).fetchone()
            if not ex or ex["agent_id"] != agent_id: return None
            if ex["released_at"] is not None: return self._lk_row(ex)
            self._db.execute("UPDATE locks SET released_at=? WHERE file_path=?", (_to_iso(_utcnow()), file_path)); self._db.commit()
            return self._lk_row(self._db.execute("SELECT * FROM locks WHERE file_path=?", (file_path,)).fetchone())

    def release_lock_force(self, file_path):
        with self._lk:
            ex = self._db.execute("SELECT * FROM locks WHERE file_path=?", (file_path,)).fetchone()
            if not ex: return None
            if ex["released_at"] is not None: return self._lk_row(ex)
            self._db.execute("UPDATE locks SET released_at=? WHERE file_path=?", (_to_iso(_utcnow()), file_path)); self._db.commit()
            return self._lk_row(self._db.execute("SELECT * FROM locks WHERE file_path=?", (file_path,)).fetchone())

    def list_locks(self, agent_id=None, active_only=False):
        conds, params = [], []
        if agent_id is not None: conds.append("agent_id=?"); params.append(agent_id)
        if active_only: conds += ["released_at IS NULL", "expires_at>=?"]; params.append(_to_iso(_utcnow()))
        w = " AND ".join(conds) if conds else "1=1"
        with self._lk:
            return [self._lk_row(r) for r in self._db.execute(f"SELECT * FROM locks WHERE {w} ORDER BY locked_at", params).fetchall()]

    def refresh_lock(self, file_path, agent_id, *, ttl_sec=300):
        now = _utcnow()
        exp = _to_iso(now + timedelta(seconds=ttl_sec))
        with self._lk:
            ex = self._db.execute("SELECT * FROM locks WHERE file_path=?", (file_path,)).fetchone()
            if not ex or ex["agent_id"] != agent_id: return None
            if ex["released_at"] is not None or _from_iso(ex["expires_at"]) < now: return None
            self._db.execute("UPDATE locks SET expires_at=? WHERE file_path=?", (exp, file_path))
            self._db.commit()
            return self._lk_row(self._db.execute("SELECT * FROM locks WHERE file_path=?", (file_path,)).fetchone())

    def _lk_row(self, r): return {"file_path":r["file_path"],"agent_id":r["agent_id"],"locked_at":r["locked_at"],"expires_at":r["expires_at"],"released_at":r["released_at"],"metadata":json.loads(r["metadata_json"] or "{}")}

    def _ch(self, r): return {"name":r["name"],"created_at":r["created_at"],"created_by":r["created_by"],"metadata":json.loads(r["metadata_json"] or "{}")}
    def _mg(self, r): return {"id":r["id"],"ts":r["ts"],"from_agent":r["from_agent"],"to_agent":r["to_agent"],"channel":r["channel"],"kind":r["kind"],"body":r["body"],"attachments":json.loads(r["attachments_json"] or "[]"),"reply_to":r["reply_to"],"thread_id":r["thread_id"],"metadata":json.loads(r["metadata_json"] or "{}")}
    def _ss(self, r): return {"session_id":r["session_id"],"agent_id":r["agent_id"],"display_name":r["display_name"],"capabilities":json.loads(r["capabilities_json"] or "[]"),"metadata":json.loads(r["metadata_json"] or "{}"),"created_at":r["created_at"],"last_seen":r["last_seen"],"active":bool(r["active"])}
    def _cl(self, r): return {"claim_key":r["claim_key"],"thread_id":r["thread_id"],"task_message_id":r["task_message_id"],"owner_agent_id":r["owner_agent_id"],"claimed_at":r["claimed_at"],"expires_at":r["expires_at"],"released_at":r["released_at"],"metadata":json.loads(r["metadata_json"] or "{}")}

# ── Server ────────────────────────────────────────────────────────────
MSG_KINDS = {"chat","notice","task","claim","release","artifact"}
ATT_TYPES = {"text","json","code","file_ref","diff_ref"}
_P = {n: re.compile(p) for n, p in [
    ("sessions", r"^/v1/sessions$"), ("session", r"^/v1/sessions/(?P<id>[^/]+)$"),
    ("agents", r"^/v1/agents$"), ("channels", r"^/v1/channels$"), ("hub_info", r"^/v1/hub-info$"),
    ("events", r"^/v1/events$"), ("messages", r"^/v1/messages$"),
    ("threads", r"^/v1/threads$"), ("thread", r"^/v1/threads/(?P<id>[^/]+)$"),
    ("inbox", r"^/v1/inbox/(?P<id>[^/]+)$"),
    ("claims", r"^/v1/claims$"), ("claims_refresh", r"^/v1/claims/refresh$"), ("claims_rel", r"^/v1/claims/release$"),
    ("locks", r"^/v1/locks$"), ("locks_refresh", r"^/v1/locks/refresh$"), ("locks_rel", r"^/v1/locks/release$"),
    ("tasks", r"^/v1/tasks$"), ("task_complete", r"^/v1/tasks/(?P<id>\d+)/complete$"),
    ("root", r"^/$")]}

def _norm_msg(p, cfg):
    fa = str(p.get("from_agent","")).strip()
    if not fa: raise ValueError("from_agent is required")
    ta = p.get("to_agent"); ta = (str(ta).strip() or None) if ta is not None else None
    ch = p.get("channel") or ("direct" if ta else "general"); ch = str(ch).strip()
    if not ch: raise ValueError("channel must be non-empty")
    kind = str(p.get("kind","chat")).strip().lower()
    if kind not in MSG_KINDS: raise ValueError(f"unsupported kind: {kind}")
    body = str(p.get("body",""))
    raw = p.get("attachments") or []
    if not isinstance(raw, list): raise ValueError("attachments must be a list")
    if len(raw) > cfg.max_attachments: raise ValueError("too many attachments")
    atts = [_norm_att(a, cfg.max_attachment_chars) for a in raw]
    if not body and not atts: raise ValueError("body or attachments is required")
    if len(body) > cfg.max_body_chars: raise ValueError("body exceeds max size")
    rt = p.get("reply_to"); rt = _coerce_int(rt, name="reply_to") if rt is not None else None
    tid = p.get("thread_id"); tid = str(tid) if tid is not None else None
    meta = p.get("metadata") or {}
    if not isinstance(meta, dict): raise ValueError("metadata must be a JSON object")
    return {"from_agent":fa,"to_agent":ta,"channel":ch,"kind":kind,"body":body,"attachments":atts,"reply_to":rt,"thread_id":tid,"metadata":meta}

def _norm_att(a, mx):
    if not isinstance(a, dict): raise ValueError("attachments must contain JSON objects")
    t = str(a.get("type","")).strip()
    if t not in ATT_TYPES: raise ValueError(f"unsupported attachment type: {t}")
    n: dict[str,Any] = {"type": t}
    if t in {"text","json","code"}:
        if "content" not in a: raise ValueError(f"attachment type {t} requires content")
        if len(json.dumps(a["content"])) > mx: raise ValueError(f"attachment type {t} exceeds max size")
        n["content"] = a["content"]
        if t == "code" and a.get("language") is not None: n["language"] = str(a["language"])
    if t in {"file_ref","diff_ref"}:
        pt = str(a.get("path","")).strip()
        if not pt: raise ValueError(f"attachment type {t} requires path")
        n["path"] = pt
        for k in ("description","base","head"):
            if k in a and a[k] is not None: n[k] = a[k]
        for k in ("start_line","end_line"):
            if k in a and a[k] is not None: n[k] = _coerce_int(a[k], name=k)
    return n


def _parse_limit(q, max_limit):
    raw = q.get("limit", ["100"])[0]
    try:
        limit = int(raw)
    except ValueError as e:
        raise ValueError("limit must be an integer") from e
    if limit < 1:
        raise ValueError("limit must be at least 1")
    return min(limit, max_limit)


def _parse_timeout(q):
    raw = q.get("timeout", ["0"])[0]
    try:
        t = float(raw)
    except ValueError as e:
        raise ValueError("timeout must be a number") from e
    return max(0.0, min(t, 60.0))

def _poll_until(fetch_fn, timeout, interval=0.25):
    """Call fetch_fn() repeatedly until it returns non-empty or timeout expires."""
    if timeout <= 0:
        return fetch_fn()
    deadline = time.monotonic() + timeout
    while True:
        rows = fetch_fn()
        if rows:
            return rows
        if time.monotonic() >= deadline:
            return rows
        time.sleep(interval)

def _parse_since_id(q):
    raw = q.get("since_id", ["0"])[0]
    try:
        since_id = int(raw)
    except ValueError as e:
        raise ValueError("since_id must be an integer") from e
    if since_id < 0:
        raise ValueError("since_id must be >= 0")
    return since_id

def _coerce_int(v, *, name):
    try: return int(v)
    except (TypeError, ValueError) as e: raise ValueError(f"{name} must be an integer") from e

def _max_req(cfg):
    return cfg.max_body_chars + cfg.max_attachment_chars * cfg.max_attachments + 65_536

class _H(BaseHTTPRequestHandler):
    server: _Srv
    def log_message(self, *a): pass
    def _discard_body(self, n, *, limit=None):
        rem = n if limit is None else min(n, limit)
        while rem > 0:
            chunk = self.rfile.read(min(65_536, rem))
            if not chunk: break
            rem -= len(chunk)
    def _j(self):
        raw = self.headers.get("Content-Length", "0")
        try: n = int(raw)
        except (ValueError, TypeError) as e: raise ValueError("Content-Length must be an integer") from e
        if n < 0: raise ValueError("Content-Length must be >= 0")
        mx = _max_req(self.server.cfg)
        if n > mx:
            self._discard_body(n, limit=mx + 65_536)
            self.close_connection = True
            raise ValueError("request body exceeds max size")
        body = self.rfile.read(n) if n else b""
        try: dec = body.decode("utf-8")
        except UnicodeDecodeError as e: raise ValueError("request body must be valid UTF-8") from e
        try: d = json.loads(dec) if body else {}
        except json.JSONDecodeError as e: raise ValueError("malformed JSON") from e
        if not isinstance(d, dict): raise ValueError("request body must be a JSON object")
        return d
    def _ok(self, d, s=200):
        b = json.dumps(d).encode()
        self.send_response(s); self.send_header("Content-Type","application/json")
        self.send_header("X-Megahub-Instance", self.server.instance_id)
        self.send_header("Connection","close")
        self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
        self.close_connection = True
    def _err(self, m, s=400): self._ok({"ok":False,"error":m}, s)
    def _u(self):
        p = urlparse(self.path); return p.path, parse_qs(p.query)

    def _html(self, html):
        b = html.encode("utf-8")
        self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("X-Megahub-Instance", self.server.instance_id)
        self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        path, q = self._u(); s = self.server.store; cfg = self.server.cfg
        if _P["root"].match(path): return self._html(DASHBOARD_HTML)
        if _P["agents"].match(path): return self._ok({"ok":True,"result":s.list_live_agents(cfg.presence_ttl_sec)})
        if _P["channels"].match(path): return self._ok({"ok":True,"result":s.list_channels()})
        if _P["hub_info"].match(path):
            return self._ok({"ok":True,"result":{
                "storage_path": str(self.server.store.db_path),
                "instance_id": self.server.instance_id,
                "journal_mode": self.server.store.journal_mode,
                "wal_mode": self.server.store.wal_enabled,
            }})
        if _P["events"].match(path):
            aid = q.get("agent_id", [None])[0]
            ch = q.get("channel", [None])[0]
            tid = q.get("thread_id", [None])[0]
            if not aid: return self._err("agent_id query parameter is required")
            if ch and s.get_channel(ch) is None: return self._err("channel not found", 404)
            try:
                si = _parse_since_id(q)
                li = _parse_limit(q, cfg.max_query_limit)
                to = _parse_timeout(q)
            except ValueError as e: return self._err(str(e))
            msgs = _poll_until(lambda: s.list_visible_messages_for_agent(aid, since_id=si, limit=li, channel=ch, thread_id=tid), to)
            return self._ok({"ok":True,"result":msgs})
        if _P["messages"].match(path):
            ch, tid = q.get("channel",[None])[0], q.get("thread_id",[None])[0]
            if not ch and not tid: return self._err("channel or thread_id query parameter is required")
            if ch and s.get_channel(ch) is None: return self._err("channel not found", 404)
            try:
                si = _parse_since_id(q)
                li = _parse_limit(q, cfg.max_query_limit)
                to = _parse_timeout(q)
            except ValueError as e: return self._err(str(e))
            msgs = _poll_until(lambda: s.list_thread_messages(tid,channel=ch,since_id=si,limit=li) if tid else s.list_channel_messages(ch,since_id=si,limit=li), to)
            return self._ok({"ok":True,"result":msgs})
        if _P["threads"].match(path):
            return self._ok({"ok":True,"result":s.list_threads()})
        m = _P["thread"].match(path)
        if m:
            detail = s.get_thread_detail(m.group("id"))
            if detail is None: return self._err("thread not found", 404)
            return self._ok({"ok":True,"result":detail})
        m = _P["inbox"].match(path)
        if m:
            try:
                si = _parse_since_id(q)
                li = _parse_limit(q, cfg.max_query_limit)
            except ValueError as e: return self._err(str(e))
            return self._ok({"ok":True,"result":s.list_inbox_messages(m.group("id"),since_id=si,limit=li)})
        if _P["claims"].match(path):
            tid = q.get("thread_id",[None])[0]; ao = q.get("active_only",[""])[0].lower() in ("true","1","yes")
            return self._ok({"ok":True,"result":s.list_claims(thread_id=tid,active_only=ao)})
        if _P["locks"].match(path):
            aid = q.get("agent_id",[None])[0]; ao = q.get("active_only",[""])[0].lower() in ("true","1","yes")
            return self._ok({"ok":True,"result":s.list_locks(agent_id=aid,active_only=ao)})
        if _P["tasks"].match(path):
            pid_raw = q.get("parent_id",[None])[0]; st = q.get("status",[None])[0]; ch = q.get("channel",[None])[0]; tid = q.get("thread_id",[None])[0]
            pid = None
            if pid_raw is not None:
                try: pid = _coerce_int(pid_raw, name="parent_id")
                except ValueError as e: return self._err(str(e))
            if st is not None and st not in ("open","done"): return self._err("status must be 'open' or 'done'")
            return self._ok({"ok":True,"result":s.list_tasks(parent_id=pid,status=st,channel=ch,thread_id=tid)})
        self._err("not found", 404)

    def do_POST(self):
        path, _ = self._u(); s = self.server.store; cfg = self.server.cfg
        if _P["sessions"].match(path):
            try:
                p = self._j(); aid = str(p.get("agent_id","")).strip()
                if not aid: raise ValueError("agent_id is required")
                caps = p.get("capabilities") or []
                if not isinstance(caps, list): raise ValueError("capabilities must be a list")
                meta = p.get("metadata") or {}
                if not isinstance(meta, dict): raise ValueError("metadata must be a JSON object")
                sess, _ = s.create_session(aid, p.get("display_name"), [str(c) for c in caps], meta, bool(p.get("replace",False)), cfg.presence_ttl_sec)
            except ValueError as e:
                return self._err(str(e), 409 if "already has an active" in str(e) else 400)
            return self._ok({"ok":True,"result":sess}, 201)
        if _P["channels"].match(path):
            try:
                p = self._j(); name = str(p.get("name","")).strip()
                if not name: raise ValueError("name is required")
                cb = p.get("created_by"); cb = str(cb) if cb is not None else None
                meta = p.get("metadata") or {}
                if not isinstance(meta, dict): raise ValueError("metadata must be a JSON object")
                ch, created = s.create_channel(name, cb, meta)
            except ValueError as e: return self._err(str(e))
            return self._ok({"ok":True,"result":ch}, 201 if created else 200)
        if _P["messages"].match(path):
            try:
                raw = self._j()
                ptid = raw.pop("parent_task_id", None)
                if ptid is not None: ptid = _coerce_int(ptid, name="parent_task_id")
                n = _norm_msg(raw, cfg)
                if n["to_agent"] is None and s.get_channel(n["channel"]) is None:
                    raise ValueError(f"channel does not exist: {n['channel']}")
                msg = s.create_message(**n)
            except ValueError as e: return self._err(str(e))
            if msg["kind"] == "task":
                s.create_task(message_id=msg["id"],parent_task_id=ptid,channel=msg["channel"],thread_id=msg["thread_id"])
            s.touch_agent_session(msg["from_agent"])
            return self._ok({"ok":True,"result":msg}, 201)
        if _P["claims_rel"].match(path):
            try:
                p = self._j(); ck = str(p.get("claim_key","")).strip(); aid = str(p.get("agent_id","")).strip()
                if not ck: raise ValueError("claim_key is required")
                if not aid: raise ValueError("agent_id is required")
            except ValueError as e: return self._err(str(e))
            cl = s.release_claim(ck, aid)
            if cl is None: return self._err("claim not found or not owned by agent_id", 404)
            s.touch_agent_session(aid)
            return self._ok({"ok":True,"result":cl})
        if _P["claims_refresh"].match(path):
            try:
                p = self._j(); ck = str(p.get("claim_key","")).strip(); oid = str(p.get("owner_agent_id","")).strip()
                if not ck: raise ValueError("claim_key is required")
                if not oid: raise ValueError("owner_agent_id is required")
                ttl = _coerce_int(p.get("ttl_sec",300), name="ttl_sec")
                if ttl < 5: raise ValueError("ttl_sec must be at least 5")
            except ValueError as e: return self._err(str(e))
            cl = s.refresh_claim(ck, oid, ttl_sec=ttl)
            if cl is None: return self._err("claim not found or not owned by owner_agent_id", 404)
            s.touch_agent_session(oid)
            return self._ok({"ok":True,"acquired":True,"result":cl})
        if _P["locks_rel"].match(path):
            try:
                p = self._j(); fp = str(p.get("file_path","")).strip(); aid = str(p.get("agent_id","")).strip()
                if not fp: raise ValueError("file_path is required")
                if not aid: raise ValueError("agent_id is required")
            except ValueError as e: return self._err(str(e))
            lk = s.release_lock(fp, aid)
            if lk is None: return self._err("lock not found or not owned by agent_id", 404)
            s.touch_agent_session(aid)
            return self._ok({"ok":True,"result":lk})
        if _P["locks_refresh"].match(path):
            try:
                p = self._j(); fp = str(p.get("file_path","")).strip(); aid = str(p.get("agent_id","")).strip()
                if not fp: raise ValueError("file_path is required")
                if not aid: raise ValueError("agent_id is required")
                ttl = _coerce_int(p.get("ttl_sec",300), name="ttl_sec")
                if ttl < 5: raise ValueError("ttl_sec must be at least 5")
            except ValueError as e: return self._err(str(e))
            lk = s.refresh_lock(fp, aid, ttl_sec=ttl)
            if lk is None: return self._err("lock not found or not owned by agent_id", 404)
            s.touch_agent_session(aid)
            return self._ok({"ok":True,"acquired":True,"result":lk})
        if _P["locks"].match(path):
            try:
                p = self._j(); aid = str(p.get("agent_id","")).strip()
                if not aid: raise ValueError("agent_id is required")
                fp = str(p.get("file_path","")).strip()
                if not fp: raise ValueError("file_path is required")
                ttl = _coerce_int(p.get("ttl_sec",300), name="ttl_sec")
                if ttl < 5: raise ValueError("ttl_sec must be at least 5")
                meta = p.get("metadata") or {}
                if not isinstance(meta, dict): raise ValueError("metadata must be a JSON object")
                lk, acq = s.acquire_lock(file_path=fp,agent_id=aid,ttl_sec=ttl,metadata=meta)
            except ValueError as e: return self._err(str(e))
            s.touch_agent_session(aid)
            return self._ok({"ok":True,"acquired":acq,"result":lk}, 201 if acq else 200)
        if _P["claims"].match(path):
            try:
                p = self._j(); oid = str(p.get("owner_agent_id","")).strip()
                if not oid: raise ValueError("owner_agent_id is required")
                ck, tmid = p.get("claim_key"), p.get("task_message_id")
                if tmid is not None: tmid = _coerce_int(tmid, name="task_message_id")
                if ck is not None: ck = str(ck).strip()
                if not ck:
                    if tmid is not None: ck = f"task-{tmid}"
                    else: raise ValueError("claim_key or task_message_id is required")
                tid = p.get("thread_id"); tid = (str(tid).strip() or None) if tid is not None else None
                ttl = _coerce_int(p.get("ttl_sec",300), name="ttl_sec")
                if ttl < 5: raise ValueError("ttl_sec must be at least 5")
                meta = p.get("metadata") or {}
                if not isinstance(meta, dict): raise ValueError("metadata must be a JSON object")
                cl, acq = s.acquire_claim(claim_key=ck,thread_id=tid,task_message_id=tmid,owner_agent_id=oid,ttl_sec=ttl,metadata=meta)
            except ValueError as e: return self._err(str(e))
            s.touch_agent_session(oid)
            return self._ok({"ok":True,"acquired":acq,"result":cl}, 201 if acq else 200)
        m = _P["task_complete"].match(path)
        if m:
            tid = int(m.group("id"))
            t = s.complete_task(tid)
            if t is None: return self._err("task not found", 404)
            result = {"ok":True,"result":t}
            done = s.check_parent_completion(tid)
            if done is True and t["parent_task_id"] is not None:
                parent = s.get_task(t["parent_task_id"])
                if parent and parent["status"] == "open":
                    s.complete_task(t["parent_task_id"])
                    subs = s.list_tasks(parent_id=t["parent_task_id"])
                    s.create_message(from_agent="system",to_agent=None,channel=parent["channel"],
                        kind="notice",body=f"All {len(subs)} subtasks of task {t['parent_task_id']} are complete.",
                        attachments=[],reply_to=t["parent_task_id"],thread_id=parent["thread_id"],
                        metadata={"auto_rollup":True,"parent_task_id":t["parent_task_id"]})
                    result["parent_completed"] = True
            return self._ok(result)
        try:
            raw = self.headers.get("Content-Length", "0")
            n = int(raw)
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            self._discard_body(n, limit=n)
        self._err("not found", 404)

    def do_DELETE(self):
        path, _ = self._u(); m = _P["session"].match(path)
        if m:
            sess = self.server.store.delete_session(m.group("id"))
            if not sess: return self._err("session not found", 404)
            return self._ok({"ok":True,"result":{"session_id":m.group("id"),"deleted":True}})
        self._err("not found", 404)

class _Srv(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    def __init__(self, cfg):
        self.cfg, self.store = cfg, HubStore(cfg.storage_path)
        self._timer: threading.Timer | None = None
        super().__init__((cfg.listen_host, cfg.port), _H)
        self.bound_port = self.server_address[1]
        self.pidfile_path = _pidfile_path(cfg.storage_path)
        general = self.store.get_channel("general")
        birth_marker = str(general["created_at"]) if general and general.get("created_at") else "unknown"
        self.instance_id = _instance_fingerprint(self.store.db_path, birth_marker)
        self.config = cfg
        self.runtime = self

    def log(self, message):
        if not self.cfg.log_events:
            return
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        print(f"[megahub {stamp}] {message}")

    def _write_pidfile(self):
        payload = {"pid": os.getpid(), "port": self.bound_port, "url": _pidfile_url(self.cfg.listen_host, self.bound_port)}
        try:
            self.pidfile_path.parent.mkdir(parents=True, exist_ok=True)
            self.pidfile_path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError as e:
            self.log(f"pidfile write error: {e}")
    def _cleanup_pidfile(self):
        info = _read_pidfile(self.pidfile_path)
        if not info or info["pid"] != os.getpid() or info["port"] != self.bound_port: return
        try: self.pidfile_path.unlink()
        except FileNotFoundError: return
        except OSError as e:
            self.log(f"pidfile cleanup error: {e}")

    def _schedule_prune(self):
        interval = max(1, self.cfg.presence_ttl_sec // 3)
        self._timer = threading.Timer(interval, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def get_hub_info(self):
        info = self.store.get_storage_info()
        return {
            "storage_path": info["storage_path"],
            "instance_id": self.instance_id,
            "journal_mode": info["journal_mode"],
            "wal_mode": info["wal_mode"],
        }

    def start(self):
        self._write_pidfile()
        info = self.get_hub_info()
        self.log(
            f"listening on {self.cfg.listen_host}:{self.bound_port} "
            f"(storage={info['storage_path']}, journal_mode={info['journal_mode']}, "
            f"instance={self.instance_id}, allow_remote={self.cfg.allow_remote})"
        )
        if not info["wal_mode"]:
            self.log(
                "warning: SQLite is not in WAL mode; shared-filesystem coordination may not behave "
                "correctly on this storage backend"
            )
        self._schedule_prune()

    def start_prune(self):
        self.start()

    def _tick(self):
        try:
            expired = self.store.prune_expired_sessions(self.cfg.presence_ttl_sec)
            for session in expired:
                self.log(f"session expired: agent={session['agent_id']} session={session['session_id']}")
            if expired:
                self._recover_expired_work(expired)
        except Exception as e:
            self.log(f"prune error: {e}")
        self._schedule_prune()

    def _recover_expired_work(self, expired_sessions):
        for session in expired_sessions:
            agent_id = session["agent_id"]
            claims = self.store.list_claims(owner_agent_id=agent_id, active_only=True)
            for claim in claims:
                released = self.store.release_claim_force(claim["claim_key"])
                if released is None:
                    continue
                self._post_recovery_notice_for_claim(released, stale_agent_id=agent_id)
                self.log(
                    f"claim recovered: key={released['claim_key']} stale_owner={agent_id} "
                    f"thread={released.get('thread_id') or '-'}"
                )

            locks = self.store.list_locks(agent_id=agent_id, active_only=True)
            for lock in locks:
                released = self.store.release_lock_force(lock["file_path"])
                if released is None:
                    continue
                self._post_recovery_notice_for_lock(released, stale_agent_id=agent_id)
                self.log(
                    f"lock recovered: path={released['file_path']} stale_owner={agent_id} "
                    f"thread={((released.get('metadata') or {}).get('thread_id') or '-')}"
                )

    def _post_recovery_notice_for_claim(self, claim, *, stale_agent_id):
        thread_id = claim.get("thread_id")
        channel = "general"
        if thread_id:
            summary = self.store.get_thread_summary(thread_id)
            if summary and summary.get("channel"):
                channel = str(summary["channel"])
        task_message_id = claim.get("task_message_id")
        self.store.create_message(
            from_agent="system",
            to_agent=None,
            channel=channel,
            kind="notice",
            body=(
                f"Recovered stale claim {claim['claim_key']} from {stale_agent_id}. "
                "Work is available for pickup."
            ),
            attachments=[],
            reply_to=task_message_id,
            thread_id=thread_id,
            metadata={
                "recovery": True,
                "stale_agent_id": stale_agent_id,
                "claim_key": claim["claim_key"],
                "task_message_id": task_message_id,
            },
        )

    def _post_recovery_notice_for_lock(self, lock, *, stale_agent_id):
        metadata = lock.get("metadata") or {}
        thread_id = metadata.get("thread_id")
        channel = str(metadata.get("channel") or "general")
        if thread_id:
            summary = self.store.get_thread_summary(str(thread_id))
            if summary and summary.get("channel"):
                channel = str(summary["channel"])
        self.store.create_message(
            from_agent="system",
            to_agent=None,
            channel=channel,
            kind="notice",
            body=(
                f"Recovered stale lock on {lock['file_path']} from {stale_agent_id}. "
                "The file is available for pickup."
            ),
            attachments=[],
            reply_to=None,
            thread_id=None if thread_id is None else str(thread_id),
            metadata={
                "recovery": True,
                "stale_agent_id": stale_agent_id,
                "file_path": lock["file_path"],
            },
        )

    def stop(self):
        if self._timer: self._timer.cancel()
        self._cleanup_pidfile()
        self.store.close()


def create_server(config=None):
    cfg = config or HubConfig()
    cfg.validate()
    return _Srv(cfg)


def run_server(config=None):
    cfg = config or HubConfig()
    cfg.validate()
    if cfg.allow_remote:
        print(
            "[megahub] Warning: allow_remote=true exposes this daemon to non-local clients. "
            "There is no built-in auth in v1."
        )
    srv = create_server(cfg)
    srv.start()
    try:
        srv.serve_forever()
    finally:
        srv.stop()
        srv.server_close()

def ensure_hub(host="127.0.0.1", port=8765, storage="megahub.sqlite3", timeout=5.0):
    """Check if a hub is running; if not, start one in the background.
    Returns dict with: running (bool), started (bool), url (str).
    The port binding itself is the mutex — only one process can bind."""
    import subprocess, sys, time
    import urllib.error as _ue, urllib.request as _ur
    base = _pidfile_url(host, port)
    def _probe(url):
        try:
            with _ur.urlopen(_ur.Request(f"{url}/v1/channels", method="GET"), timeout=2): return True
        except (_ue.URLError, OSError, TimeoutError): return False
    pid_info = _discover_pidfile(storage)
    if pid_info and _probe(pid_info["url"]): return {"running": True, "started": False, "url": pid_info["url"]}
    if _probe(base): return {"running": True, "started": False, "url": base}
    try:
        subprocess.Popen([sys.executable, __file__, "--host", host, "--port", str(port),
            "--storage", storage, "--quiet"], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError: return {"running": False, "started": False, "url": base, "error": "spawn failed"}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.15)
        if _probe(base): return {"running": True, "started": True, "url": base}
    return {"running": False, "started": True, "url": base, "error": "timeout"}

def main():
    ap = argparse.ArgumentParser(description="Megahub — single-file agent coordination hub")
    sub = ap.add_subparsers(dest="command")
    ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--storage", default="megahub.sqlite3"); ap.add_argument("--allow-remote", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ens = sub.add_parser("ensure", help="Start hub if not already running, then exit")
    ens.add_argument("--host", default="127.0.0.1"); ens.add_argument("--port", type=int, default=8765)
    ens.add_argument("--storage", default="megahub.sqlite3"); ens.add_argument("--timeout", type=float, default=5.0)
    a = ap.parse_args()
    if a.command == "ensure":
        r = ensure_hub(host=a.host, port=a.port, storage=a.storage, timeout=a.timeout)
        print(json.dumps(r, indent=2))
        raise SystemExit(0 if r.get("running") else 1)
    cfg = HubConfig(
        listen_host=a.host,
        port=a.port,
        storage_path=a.storage,
        allow_remote=a.allow_remote,
        log_events=not a.quiet,
    )
    try:
        run_server(cfg)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
