#!/usr/bin/env python3
"""ProcVision Pro: Linux /proc terminal and browser monitor. No third-party dependencies."""
import argparse
import curses
import json
import os
import time
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections import deque

from datetime import datetime


class ProcessLifecycleTracker:
    def __init__(self, max_events=100):
        self.previous = None
        self.events = deque(maxlen=max_events)

    def update(self, processes):
        # Track PID and process start time to avoid PID reuse errors.
        current = {
            (p["pid"], p["start"]): p["name"]
            for p in processes
        }

        if self.previous is None:
            self.previous = current
            return []

        timestamp = datetime.now().strftime("%H:%M:%S")
        new_events = []

        for identity, name in current.items():
            if identity not in self.previous:
                new_events.append({
                    "time": timestamp,
                    "event": "STARTED",
                    "pid": identity[0],
                    "name": name
                })

        for identity, name in self.previous.items():
            if identity not in current:
                new_events.append({
                    "time": timestamp,
                    "event": "TERMINATED",
                    "pid": identity[0],
                    "name": name
                })

        for event in new_events:
            self.events.appendleft(event)

        self.previous = current
        return new_events

HZ = os.sysconf('SC_CLK_TCK')
CORES = os.cpu_count() or 1

def cpu_counters():
    with open('/proc/stat', encoding='utf8') as f:
        x = [int(v) for v in f.readline().split()[1:]]
    return sum(x), x[3] + (x[4] if len(x)>4 else 0)

def memory():
    d={}
    with open('/proc/meminfo',encoding='utf8') as f:
        for line in f:
            k,_,v=line.partition(':')
            if v: d[k]=int(v.strip().split()[0])
    total=d['MemTotal']; available=d.get('MemAvailable',d.get('MemFree',0))
    return {'used':round((total-available)/1024), 'total':round(total/1024), 'percent':round(100*(total-available)/total,1)}

def parse_stat(raw):
    # comm is enclosed in parentheses and may contain spaces or ')' characters.
    a=raw.find('('); b=raw.rfind(')')
    if a<0 or b<a: raise ValueError('bad /proc stat')
    fields=raw[b+2:].split()
    return {'name':raw[a+1:b], 'state':fields[0], 'utime':int(fields[11]), 'stime':int(fields[12]), 'start':int(fields[19]),'ppid': int(fields[1])}

def read_process(pid):
    with open('/proc/%s/stat'%pid,encoding='utf8') as f: p=parse_stat(f.read())
    try:
        with open('/proc/%s/status'%pid,encoding='utf8') as f:
            for line in f:
                if line.startswith('VmRSS:'): p['rss']=int(line.split()[1])/1024; break
            else: p['rss']=0
    except (OSError,ValueError): p['rss']=0
    p['pid']=int(pid)
    return p

class Collector:
    def __init__(self):
        self.prev_total=None; self.prev_idle=None; self.prev_proc={}; self.last=None
        self.cpu_history=deque(maxlen=80); self.mem_history=deque(maxlen=80)
        self.lifecycle=ProcessLifecycleTracker()
    def sample(self):
        now=time.monotonic(); total,idle=cpu_counters(); mem=memory()
        dt=total-(self.prev_total if self.prev_total is not None else total)
        di=idle-(self.prev_idle if self.prev_idle is not None else idle)
        cpu=max(0,min(100,100*(dt-di)/dt)) if dt>0 else 0
        elapsed=now-self.last if self.last is not None else 0
        rows=[]; new={}
        for pid in os.listdir('/proc'):
            if not pid.isdigit(): continue
            try: p=read_process(pid)
            except (OSError,ValueError,IndexError): continue
            identity=(p['pid'],p['start']); ticks=p['utime']+p['stime']
            prior=self.prev_proc.get(identity)
            p['cpu']=round(max(0,100*(ticks-prior)/HZ/elapsed),1) if prior is not None and elapsed>0 else 0.0
            p['rss']=round(p['rss'],1)
            new[identity]=ticks
            rows.append({k: p[k]for k in ('pid', 'name', 'state', 'rss', 'cpu', 'start', 'ppid')
})
        self.prev_total=total; self.prev_idle=idle; self.prev_proc=new; self.last=now
        self.cpu_history.append(round(cpu,1)); self.mem_history.append(mem['percent'])
        self.lifecycle.update(rows)
        
        return {
            'cpu': round(cpu, 1),
            'memory': mem,
            'processes': rows,
            'count': len(rows),
            'cpu_history': list(self.cpu_history),
            'memory_history': list(self.mem_history),
            'events': list(self.lifecycle.events),
            'timestamp': time.time()
        }

def bar(value,width):
    n=max(0,min(width,round(value*width/100)))
    return '█'*n+'░'*(width-n)

def spark(values,width):
    chars='▁▂▃▄▅▆▇█'
    return ''.join(chars[min(7,max(0,int(v*7/100)))] for v in list(values)[-width:])

def tui(stdscr,interval):
    try: curses.curs_set(0)
    except curses.error: pass
    curses.start_color(); curses.use_default_colors()
    for i,c in enumerate((curses.COLOR_CYAN,curses.COLOR_GREEN,curses.COLOR_YELLOW,curses.COLOR_RED),1):
        curses.init_pair(i,c,-1)
    stdscr.timeout(100)
    col=Collector(); snap=col.sample(); sort='cpu'; query=''; offset=0; paused=False; last=0
    def draw(y,x,s,color=0,bold=False):
        h,w=stdscr.getmaxyx()
        if y>=h or x>=w or y<0: return
        try: stdscr.addnstr(y,x,str(s),max(0,w-x-1),curses.color_pair(color)|(curses.A_BOLD if bold else 0))
        except curses.error: pass
    while True:
        now=time.monotonic()
        if not paused and now-last>=interval:
            snap=col.sample();last=now
        h,w=stdscr.getmaxyx(); stdscr.erase()
        if h<18 or w<72:
            draw(1,2,'Resize terminal to at least 72 columns x 18 rows',3)
            draw(3,2,'Q quit')
        else:
            draw(1,2,'◆ PROCVISION PRO',1,True);draw(1,25,'LINUX PROCESS INTELLIGENCE',0,True)
            draw(2,2,'─'*min(w-5,100),1)
            draw(4,2,'CPU',1,True);draw(4,10,bar(snap['cpu'],24),2);draw(4,37,'%5.1f%%'%snap['cpu'],3)
            draw(5,2,'RAM',1,True);draw(5,10,bar(snap['memory']['percent'],24),2);draw(5,37,'%5.1f%%'%snap['memory']['percent'],3)
            draw(6,2,'CPU TREND',1);draw(6,14,spark(snap['cpu_history'],min(55,w-18)),2)
            draw(7,2,'RAM TREND',1);draw(7,14,spark(snap['memory_history'],min(55,w-18)),1)
            alert=snap['cpu']>=80 or snap['memory']['percent']>=80
            draw(9,2,'● HIGH RESOURCE USAGE' if alert else '● HEALTHY',4 if alert else 2,True)
            draw(9,27,'PIDS: %d'%snap['count']);draw(9,44,'SORT: %s'%sort.upper());draw(9,60,'PAUSED' if paused else 'LIVE',3 if paused else 2)
            draw(10,2,'FILTER: '+(query or 'all processes'))
            draw(12,2,'PID      PROCESS                  STATE     CPU %     RAM MB',1,True)
            draw(13,2,'─'*min(w-5,90),1)
            rows=[p for p in snap['processes'] if query.lower() in p['name'].lower() or query in str(p['pid'])]
            rows.sort(key=(lambda p:p['cpu']) if sort=='cpu' else (lambda p:p['rss']) if sort=='memory' else (lambda p:-p['pid']),reverse=True)
            capacity=max(0,h-17); offset=min(offset,max(0,len(rows)-capacity))
            for j,p in enumerate(rows[offset:offset+capacity]):
                draw(14+j,2,'%-8d %-24.24s %-7s %8.1f %10.1f'%(p['pid'],p['name'],p['state'],p['cpu'],p['rss']),4 if p['cpu']>=80 else 0)
            draw(h-2,2,'[Q] quit  [C] CPU  [M] RAM  [P] PID  [/] filter  [X] clear  [SPACE] pause  [↑↓] scroll',3)
        stdscr.refresh()
        k=stdscr.getch()
        if k in (ord('q'),ord('Q')): break
        if k in (ord('c'),ord('C')): sort='cpu';offset=0
        if k in (ord('m'),ord('M')): sort='memory';offset=0
        if k in (ord('p'),ord('P')): sort='pid';offset=0
        if k in (ord('x'),ord('X')): query='';offset=0
        if k==ord(' '): paused=not paused
        if k==curses.KEY_DOWN: offset+=1
        if k==curses.KEY_UP: offset=max(0,offset-1)
        if k==ord('/'):
            curses.echo()
            try:
                stdscr.move(min(h-1,11),2);stdscr.clrtoeol();draw(min(h-1,11),2,'Search name/PID: ')
                stdscr.timeout(-1); query=stdscr.getstr(min(h-1,11),19,35).decode('utf8','replace');offset=0
            finally: curses.noecho();stdscr.timeout(100)


HTML = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ProcVision Pro | Group G17</title>
<style>
:root {
  font-family: Inter, system-ui, Arial, sans-serif;
  background: #08111f;
  color: #e8f2ff;
}
* { box-sizing: border-box; }
body { max-width: 1280px; margin: auto; padding: 28px; }
header {
  display: flex; justify-content: space-between;
  align-items: center; border-bottom: 1px solid #263951;
  padding-bottom: 20px;
}
h1 { margin: 0; font-size: 28px; }
h3 { margin-top: 0; }
small, .muted { color: #91a7bd; }
.live, .started { color: #52e1b3; }
.terminated { color: #ff956f; }
.alert { color: #ffb66d; }
.cards, .panels {
  display: grid; gap: 18px; margin: 24px 0;
}
.cards { grid-template-columns: repeat(3, 1fr); }
.panels { grid-template-columns: repeat(2, 1fr); }
.card, .panel {
  background: #111f32;
  border: 1px solid #253a53;
  border-radius: 15px;
  padding: 22px;
}
.metric { font-size: 38px; font-weight: 750; margin: 12px 0; }
.bar {
  height: 9px; background: #293d54;
  border-radius: 9px; overflow: hidden;
}
.fill {
  height: 100%; width: 0;
  background: linear-gradient(90deg, #34d4b0, #4c9eff);
}
canvas { width: 100%; height: 180px; }
.tools { display: flex; gap: 12px; margin-bottom: 14px; }
input, select, button {
  background: #172941; border: 1px solid #34516b;
  color: #e8f2ff; padding: 12px; border-radius: 8px;
}
input { flex: 1; min-width: 0; }
button { cursor: pointer; }
table { border-collapse: collapse; width: 100%; }
th, td {
  text-align: left; padding: 11px 12px;
  border-bottom: 1px solid #25364a;
}
th { color: #9bb8d3; font-size: 12px; }
tbody tr { cursor: pointer; }
tbody tr:hover { background: #1a3048; }
.activity-item {
  display: flex; align-items: center; gap: 16px;
  padding: 14px; margin-bottom: 8px;
  background: #172941; border-radius: 10px;
  border-left: 3px solid #52e1b3;
}
.activity-item.ended { border-left-color: #ff956f; }
.activity-details { flex: 1; overflow-wrap: anywhere; }
.activity-time { color: #91a7bd; font-size: 12px; }
#activityTimeline { max-height: 350px; overflow-y: auto; }
.inspector-grid {
  display: grid; grid-template-columns: repeat(2, 1fr);
  gap: 12px; margin-top: 14px;
}
.inspector-grid div {
  background: #172941; padding: 14px; border-radius: 9px;
}
.inspector-grid strong { display: block; margin-top: 8px; }
@media(max-width:700px) {
  body { padding: 14px; }
  .cards, .panels { grid-template-columns: 1fr; }
  .tools { flex-wrap: wrap; }
}
</style>
</head>
<body>

<header>
  <div>
    <h1>◆ ProcVision <span class="live">PRO</span></h1>
    <small>Linux /proc Intelligence Center · Group G17 · P05</small>
  </div>
  <div id="status" class="live">● CONNECTING</div>
</header>

<section class="cards">
  <div class="card">
    <small>CPU UTILIZATION</small>
    <div class="metric" id="cpu">--</div>
    <div class="bar"><div class="fill" id="cpuBar"></div></div>
  </div>
  <div class="card">
    <small>MEMORY UTILIZATION</small>
    <div class="metric" id="mem">--</div>
    <div class="bar"><div class="fill" id="memBar"></div></div>
  </div>
  <div class="card">
    <small>ACTIVE PROCESSES</small>
    <div class="metric" id="count">--</div>
    <small id="health">Monitoring...</small>
  </div>
</section>

<section class="panels">
  <div class="panel">
    <h3>CPU history</h3>
    <canvas id="cpuPlot" width="500" height="180"></canvas>
  </div>
  <div class="panel">
    <h3>Memory history</h3>
    <canvas id="memPlot" width="500" height="180"></canvas>
  </div>
</section>

<section class="panel">
  <h3>Live Process Explorer</h3>
  <div class="tools">
    <input id="search" placeholder="Search process name or PID">
    <select id="sort">
      <option value="cpu">Highest CPU</option>
      <option value="rss">Highest memory</option>
      <option value="pid">PID</option>
    </select>
  </div>
  <div style="overflow:auto">
    <table>
      <thead>
        <tr>
          <th>PID</th><th>Process</th><th>State</th>
          <th>CPU %</th><th>RAM MB</th>
        </tr>
      </thead>
      <tbody id="rows"></tbody>
    </table>
  </div>
</section>

<section class="panel" style="margin-top:18px">
  <h3>Process Inspector</h3>
  <div id="inspector">
    <p class="muted">Click a process above to inspect it.</p>
  </div>
</section>

<section class="panel" style="margin-top:18px">
  <h3>Smart Resource Alerts</h3>
  <p class="muted">
    Alerts activate after 5 consecutive high-usage samples.
  </p>

  <div class="tools">
    <label>
      CPU threshold
      <input id="cpuThreshold" type="number"
             min="1" max="100" value="80">
    </label>

    <label>
      Memory threshold
      <input id="memoryThreshold" type="number"
             min="1" max="100" value="80">
    </label>
  </div>

  <div id="smartAlertStatus" class="live">
    Monitoring resource usage...
  </div>
</section>
<section class="panel" style="margin-top:18px">
  <div style="display:flex;justify-content:space-between;gap:12px">
    <h3>Live Process Activity</h3>
    <span class="live">● LIVE TRACKING</span>
  </div>
  <p class="muted">Linux process creation and termination events</p>
  <div id="activityTimeline">
    <p class="muted">Waiting for process events...</p>
  </div>
</section>

<section class="panel" style="margin-top:18px">
  <h3>Parent–Child Process Explorer</h3>
  <p class="muted">
    Explore Linux process relationships using PID and PPID.
  </p>

  <div class="tools">
    <input id="treeSearch"
           type="number"
           min="1"
           placeholder="Enter parent PID (example: 1)">
    <button id="treeButton" type="button">
      Explore Process
    </button>
  </div>

  <div id="processTree"
       style="background:#172941;padding:18px;border-radius:10px">
    Enter a PID to view its parent and child processes.
  </div>
</section>

<p class="muted">
  Read-only Linux monitoring · Refreshes every second
</p>

<script>
let latest = null;
let selectedPID = null;

function plot(id, arr, color) {
  const c = document.getElementById(id);
  const g = c.getContext('2d');
  const values = (Array.isArray(arr) ? arr : [])
    .map(Number).filter(Number.isFinite);

  const W = c.width, H = c.height;
  const left = 50, right = 15, top = 15, bottom = 28;
  const plotW = W - left - right;
  const plotH = H - top - bottom;

  g.clearRect(0, 0, W, H);
  g.font = '12px system-ui';

  if (!values.length) {
    g.fillStyle = '#9bb8d3';
    g.fillText('Waiting for samples...', left, H / 2);
    return;
  }

  let lo = Math.min(...values);
  let hi = Math.max(...values);
  const span = Math.max(2, (hi - lo) * 1.35);
  const mid = (lo + hi) / 2;

  lo = Math.max(0, mid - span / 2);
  hi = Math.min(100, lo + span);
  lo = Math.max(0, hi - span);

  const yOf = v => top + (hi - v) / (hi - lo) * plotH;

  g.strokeStyle = '#29435c';
  g.fillStyle = '#a4bad0';
  g.lineWidth = 1;

  for (let k = 0; k <= 4; k++) {
    const value = hi - (hi - lo) * k / 4;
    const y = top + plotH * k / 4;
    g.beginPath();
    g.moveTo(left, y);
    g.lineTo(W - right, y);
    g.stroke();
    g.fillText(value.toFixed(1) + '%', 3, y + 4);
  }

  g.fillText('older', left, H - 5);
  g.fillText('latest', W - right - 37, H - 5);

  g.strokeStyle = color;
  g.lineWidth = 2.5;
  g.beginPath();

  values.forEach((v, i) => {
    const x = left + (values.length === 1
      ? plotW / 2 : i * plotW / (values.length - 1));
    const y = yOf(v);
    if (i === 0) g.moveTo(x, y);
    else g.lineTo(x, y);
  });

  g.stroke();

  const last = values[values.length - 1];
  const x = left + (values.length === 1 ? plotW / 2 : plotW);
  const y = yOf(last);

  g.beginPath();
  g.arc(x, y, 3.5, 0, Math.PI * 2);
  g.fillStyle = color;
  g.fill();

  g.fillStyle = '#e8f2ff';
  g.fillText(last.toFixed(1) + '%',
    Math.max(left, x - 55), Math.max(12, y - 9));
}

function renderActivity(events) {
  const container = document.getElementById('activityTimeline');
  container.replaceChildren();

  if (!Array.isArray(events) || events.length === 0) {
    const p = document.createElement('p');
    p.className = 'muted';
    p.textContent = 'No process events detected yet.';
    container.appendChild(p);
    return;
  }

  events.slice(0, 30).forEach(event => {
    const started = event.event === 'STARTED';

    const item = document.createElement('div');
    item.className = 'activity-item' + (started ? '' : ' ended');

    const details = document.createElement('div');
    details.className = 'activity-details';

    const status = document.createElement('strong');
    status.className = started ? 'started' : 'terminated';
    status.textContent = started
      ? '● PROCESS STARTED' : '● PROCESS TERMINATED';

    const name = document.createElement('div');
    name.textContent = `${event.name} · PID ${event.pid}`;

    const time = document.createElement('span');
    time.className = 'activity-time';
    time.textContent = event.time || '--:--:--';

    details.append(status, name);
    item.append(details, time);
    container.appendChild(item);
  });
}

function renderInspector() {
  const box = document.getElementById('inspector');
  if (selectedPID === null || !latest) return;

  const p = latest.processes.find(x => x.pid === selectedPID);
  box.replaceChildren();

  if (!p) {
    const message = document.createElement('p');
    message.className = 'muted';
    message.textContent = 'This process is no longer running.';
    box.appendChild(message);
    return;
  }

  const heading = document.createElement('h3');
  heading.textContent = p.name;

  const grid = document.createElement('div');
  grid.className = 'inspector-grid';

  const fields = [
    ['PID', p.pid],
    ['State', p.state],
    ['CPU', p.cpu.toFixed(1) + '%'],
    ['Resident memory', p.rss.toFixed(1) + ' MB']
  ];

  fields.forEach(([label, value]) => {
    const cell = document.createElement('div');
    const small = document.createElement('small');
    small.textContent = label;
    const strong = document.createElement('strong');
    strong.textContent = value;
    cell.append(small, strong);
    grid.appendChild(cell);
  });

  box.append(heading, grid);
}

let highCpuSamples = 0;
let highMemorySamples = 0;
let lastAlertTimestamp = null;

function updateSmartAlerts(data) {
  const cpuLimit = Number(
    document.getElementById('cpuThreshold').value
  );

  const memoryLimit = Number(
    document.getElementById('memoryThreshold').value
  );

  const status = document.getElementById('smartAlertStatus');

  if (!Number.isFinite(cpuLimit) ||
      !Number.isFinite(memoryLimit) ||
      cpuLimit < 1 || cpuLimit > 100 ||
      memoryLimit < 1 || memoryLimit > 100) {
    status.textContent = 'Enter thresholds between 1 and 100%.';
    status.className = 'alert';
    return;
  }

  // Count each backend sample only once.
  if (data.timestamp === lastAlertTimestamp) return;
  lastAlertTimestamp = data.timestamp;

  highCpuSamples =
    data.cpu >= cpuLimit ? highCpuSamples + 1 : 0;

  highMemorySamples =
    data.memory.percent >= memoryLimit
      ? highMemorySamples + 1 : 0;

  const alerts = [];

  if (highCpuSamples >= 5) {
    alerts.push(
      'High CPU usage: ' + data.cpu.toFixed(1) + '%'
    );
  }

  if (highMemorySamples >= 5) {
    alerts.push(
      'High memory usage: ' +
      data.memory.percent.toFixed(1) + '%'
    );
  }

  if (alerts.length) {
    status.textContent = '⚠ ' + alerts.join(' | ');
    status.className = 'alert';
  } else {
    status.textContent =
      '● System healthy — monitoring CPU and memory';
    status.className = 'live';
  }
}

function renderProcessTree() {
  const container = document.getElementById('processTree');
  const input = document.getElementById('treeSearch');

  if (!latest || !container || !input) return;

  const pid = Number(input.value);
  container.replaceChildren();

  if (!Number.isInteger(pid) || pid <= 0) {
    container.textContent = 'Enter a valid process ID.';
    return;
  }

  const processes = latest.processes;
  const selected = processes.find(p => p.pid === pid);

  if (!selected) {
    container.textContent = 'Process not found.';
    return;
  }

  function addLine(text, depth) {
    const line = document.createElement('div');
    line.style.paddingLeft = (depth * 22) + 'px';
    line.style.paddingTop = '6px';
    line.style.paddingBottom = '6px';
    line.textContent = text;
    container.appendChild(line);
  }

  const parent = processes.find(p => p.pid === selected.ppid);

  if (parent) {
    addLine(
      'PARENT: ' + parent.name + ' (PID ' + parent.pid + ')',
      0
    );
  } else {
    addLine('PARENT PID: ' + selected.ppid, 0);
  }

  addLine(
    'SELECTED: ' + selected.name + ' (PID ' + selected.pid + ')',
    1
  );

  const visited = new Set([selected.pid]);

  function showChildren(parentPid, depth) {
    if (depth > 5) return;

    const children = processes.filter(p => p.ppid === parentPid);

    children.forEach(child => {
      if (visited.has(child.pid)) return;
      visited.add(child.pid);

      addLine(
        '└── ' + child.name + ' (PID ' + child.pid + ')',
        depth
      );

      showChildren(child.pid, depth + 1);
    });
  }

  showChildren(selected.pid, 2);
}

document.getElementById('treeButton')
  .addEventListener('click', renderProcessTree);

function render() {
  if (!latest) return;
  const d = latest;
updateSmartAlerts(d);
  document.getElementById('cpu').textContent = d.cpu.toFixed(1) + '%';
  document.getElementById('mem').textContent =
    d.memory.percent.toFixed(1) + '%';
  document.getElementById('count').textContent = d.count;

  document.getElementById('cpuBar').style.width = d.cpu + '%';
  document.getElementById('memBar').style.width =
    d.memory.percent + '%';

  const alarm = d.cpu >= 80 || d.memory.percent >= 80;
  const health = document.getElementById('health');
  health.textContent = alarm
    ? '⚠ High resource utilization' : '● System healthy';
  health.className = alarm ? 'alert' : 'live';

  plot('cpuPlot', d.cpu_history, '#4ce0bd');
  plot('memPlot', d.memory_history, '#73aaff');

  renderActivity(d.events || []);
  if (document.getElementById('treeSearch').value) {
  renderProcessTree();
}
  const q = document.getElementById('search').value.toLowerCase();
  const sort = document.getElementById('sort').value;

  const rows = d.processes
    .filter(p =>
      p.name.toLowerCase().includes(q) ||
      String(p.pid).includes(q))
    .sort((a, b) =>
      sort === 'pid' ? a.pid - b.pid : b[sort] - a[sort])
    .slice(0, 100);

  const tbody = document.getElementById('rows');
  tbody.replaceChildren();

  rows.forEach(p => {
    const tr = document.createElement('tr');
    [p.pid, p.name, p.state,
     p.cpu.toFixed(1), p.rss.toFixed(1)].forEach(value => {
      const td = document.createElement('td');
      td.textContent = value;
      tr.appendChild(td);
    });
    tr.addEventListener('click', () => {
      selectedPID = p.pid;
      renderInspector();
    });
    tbody.appendChild(tr);
  });

  renderInspector();
}

async function poll() {
  try {
    const response = await fetch('/api/snapshot', {
      cache: 'no-store'
    });
    if (!response.ok) throw Error(response.status);

    latest = await response.json();
    document.getElementById('status').textContent = '● LIVE';
    document.getElementById('status').className = 'live';
    render();
  } catch (error) {
    document.getElementById('status').textContent = '● DISCONNECTED';
    document.getElementById('status').className = 'alert';
  }
}

document.getElementById('search')
  .addEventListener('input', render);
document.getElementById('sort')
  .addEventListener('change', render);

setInterval(poll, 1000);
poll();
</script>
</body>
</html>'''

def web(host,port):
    collector=Collector(); lock=threading.Lock(); snapshot=collector.sample()
    def worker():
        nonlocal snapshot
        while True:
            time.sleep(1)
            try:
                value=collector.sample()
                with lock: snapshot=value
            except Exception as exc: print('Sampling error:',exc)
    threading.Thread(target=worker,daemon=True).start()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path=='/': data=HTML.encode(); mime='text/html; charset=utf-8'
            elif self.path=='/api/snapshot':
                with lock: data=json.dumps(snapshot).encode()
                mime='application/json'
            else: self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
    server=ThreadingHTTPServer((host,port),Handler)
    print('ProcVision browser dashboard: http://localhost:%s'%port)
    print('Press Ctrl+C to stop. Read-only; bind to localhost by default.')
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

def main():
    p=argparse.ArgumentParser(description='ProcVision Pro Linux process monitor')
    p.add_argument('--web',action='store_true',help='Start browser dashboard')
    p.add_argument('--port',type=int,default=8765)
    p.add_argument('--host',default='127.0.0.1')
    p.add_argument('--interval',type=float,default=1)
    args=p.parse_args()
    if not os.path.exists('/proc/stat'): p.error('Requires Linux /proc filesystem')
    if args.web: web(args.host,args.port)
    else: curses.wrapper(lambda screen:tui(screen,max(.2,args.interval)))
if __name__=='__main__':main()
