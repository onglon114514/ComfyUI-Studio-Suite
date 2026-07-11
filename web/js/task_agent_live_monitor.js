import { app } from "/scripts/app.js";

const STATUS_URL = "/studio-suite/task-agent/monitor/status";
const POSITION_KEY = "studio-suite-task-agent-monitor-position";

function formatMb(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return `${Math.round(number)} MB`;
}

function extractGpu(memory) {
  const cuda = memory?.cuda || {};
  if (cuda.available || cuda.total_mb || cuda.free_mb || cuda.reserved_mb || cuda.allocated_mb) {
    const total = Number(cuda.total_mb);
    const free = Number(cuda.free_mb);
    const used = Number.isFinite(total) && Number.isFinite(free) ? Math.max(0, total - free) : cuda.used_mb;
    return {
      used,
      total: cuda.total_mb,
      free: cuda.free_mb,
      allocated: cuda.allocated_mb,
      reserved: cuda.reserved_mb,
    };
  }
  return null;
}

function extractSystemMemory(memory) {
  const system = memory?.system || {};
  if (!system.available) return null;
  const total = Number(system.total_phys_mb);
  const avail = Number(system.avail_phys_mb);
  return {
    load: system.memory_load_percent,
    used: Number.isFinite(total) && Number.isFinite(avail) ? Math.max(0, total - avail) : null,
    total: system.total_phys_mb,
    avail: system.avail_phys_mb,
  };
}

function extractProcessMemory(memory) {
  const process = memory?.process || {};
  if (!process.available) return null;
  return {
    rss: process.rss_mb ?? process.working_set_mb,
    private: process.private_mb ?? process.private_usage_mb,
  };
}

function backendSummary(payload) {
  const active = payload?.active_backend?.status || {};
  const health = active.healthy ? "healthy" : active.health_error ? "error" : "idle";
  return {
    health,
    provider: active.backend_provider || "-",
    model: active.model_name || "-",
    profile: active.current_backend_profile || "-",
    pid: active.backend_pid || "-",
    error: active.health_error || "",
    log: active.last_launch_log_path || payload?.latest_log?.path || "",
  };
}

function createStyles() {
  if (document.getElementById("studio-suite-task-agent-monitor-style")) return;
  const style = document.createElement("style");
  style.id = "studio-suite-task-agent-monitor-style";
  style.textContent = `
    .ss-ta-monitor-bubble {
      position: fixed;
      right: auto;
      bottom: auto;
      z-index: 10020;
      width: 52px;
      height: 52px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,.18);
      background: radial-gradient(circle at 30% 20%, #59f0c5, #1f6feb 45%, #101018 100%);
      color: #fff;
      box-shadow: 0 12px 34px rgba(0,0,0,.45);
      display: flex;
      align-items: center;
      justify-content: center;
      font: 700 18px/1 ui-monospace, SFMono-Regular, Consolas, monospace;
      cursor: pointer;
      user-select: none;
      touch-action: none;
    }
    .ss-ta-monitor-bubble.dragging { cursor: grabbing; }
    .ss-ta-monitor-bubble[data-health="error"] {
      background: radial-gradient(circle at 30% 20%, #ff9b9b, #b42318 55%, #101018 100%);
    }
    .ss-ta-monitor-bubble[data-health="idle"] {
      background: radial-gradient(circle at 30% 20%, #ffe28a, #9a6700 55%, #101018 100%);
    }
    .ss-ta-monitor-panel {
      position: fixed;
      right: auto;
      bottom: auto;
      z-index: 10019;
      width: min(560px, calc(100vw - 48px));
      max-height: min(680px, calc(100vh - 120px));
      border: 1px solid rgba(255,255,255,.14);
      border-radius: 18px;
      overflow: hidden;
      background: rgba(13, 15, 20, .96);
      color: #e8e8ee;
      box-shadow: 0 22px 70px rgba(0,0,0,.55);
      backdrop-filter: blur(16px);
      display: none;
      font: 13px/1.45 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .ss-ta-monitor-panel.open { display: block; }
    .ss-ta-monitor-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 14px 16px;
      border-bottom: 1px solid rgba(255,255,255,.09);
      background: linear-gradient(135deg, rgba(89,240,197,.12), rgba(31,111,235,.1));
    }
    .ss-ta-monitor-title { font-weight: 750; letter-spacing: .02em; }
    .ss-ta-monitor-actions button {
      border: 1px solid rgba(255,255,255,.14);
      background: rgba(255,255,255,.07);
      color: #f4f4f5;
      border-radius: 9px;
      padding: 5px 9px;
      cursor: pointer;
    }
    .ss-ta-monitor-body {
      padding: 14px 16px 16px;
      overflow: auto;
      max-height: calc(min(680px, 100vh - 120px) - 54px);
    }
    .ss-ta-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 12px;
    }
    .ss-ta-card {
      border: 1px solid rgba(255,255,255,.09);
      background: rgba(255,255,255,.045);
      border-radius: 12px;
      padding: 10px 12px;
      min-width: 0;
    }
    .ss-ta-label {
      color: #9da4b3;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: .08em;
      margin-bottom: 4px;
    }
    .ss-ta-value {
      color: #f5f7fb;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    }
    .ss-ta-health {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-weight: 700;
    }
    .ss-ta-dot {
      width: 9px;
      height: 9px;
      border-radius: 999px;
      background: #f7c948;
      box-shadow: 0 0 12px currentColor;
    }
    .ss-ta-health[data-health="healthy"] .ss-ta-dot { background: #3ddc97; color: #3ddc97; }
    .ss-ta-health[data-health="error"] .ss-ta-dot { background: #ff5a5f; color: #ff5a5f; }
    .ss-ta-log {
      margin-top: 10px;
      border: 1px solid rgba(255,255,255,.1);
      border-radius: 12px;
      background: #07080d;
      overflow: hidden;
    }
    .ss-ta-log-title {
      padding: 9px 12px;
      border-bottom: 1px solid rgba(255,255,255,.08);
      color: #bac2d3;
      display: flex;
      justify-content: space-between;
      gap: 12px;
    }
    .ss-ta-log pre {
      margin: 0;
      padding: 12px;
      max-height: 260px;
      overflow: auto;
      color: #d7e1ff;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Consolas, monospace;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .ss-ta-output {
      margin-top: 10px;
      border: 1px solid rgba(89,240,197,.18);
      border-radius: 12px;
      background: rgba(89,240,197,.035);
      overflow: hidden;
    }
    .ss-ta-output-title {
      padding: 9px 12px;
      border-bottom: 1px solid rgba(255,255,255,.08);
      color: #d7fff3;
      display: flex;
      justify-content: space-between;
      gap: 12px;
    }
    .ss-ta-output pre {
      margin: 0;
      padding: 12px;
      max-height: 220px;
      overflow: auto;
      color: #eefdf9;
      font: 12px/1.5 ui-monospace, SFMono-Regular, Consolas, monospace;
      white-space: pre-wrap;
      word-break: break-word;
    }
    @media (max-width: 680px) {
      .ss-ta-grid { grid-template-columns: 1fr; }
      .ss-ta-monitor-panel { width: calc(100vw - 24px); }
    }
  `;
  document.head.appendChild(style);
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function readSavedPosition() {
  try {
    return JSON.parse(localStorage.getItem(POSITION_KEY) || "null");
  } catch {
    return null;
  }
}

function savePosition(x, y) {
  localStorage.setItem(POSITION_KEY, JSON.stringify({ x: Math.round(x), y: Math.round(y) }));
}

function placeBubble(bubble) {
  const saved = readSavedPosition();
  const size = 52;
  const margin = 18;
  const x = saved?.x ?? Math.max(margin, window.innerWidth - size - 120);
  const y = saved?.y ?? Math.max(margin, window.innerHeight - size - 132);
  bubble.style.left = `${clamp(x, margin, window.innerWidth - size - margin)}px`;
  bubble.style.top = `${clamp(y, margin, window.innerHeight - size - margin)}px`;
}

function placePanelNearBubble(panel, bubble) {
  const rect = bubble.getBoundingClientRect();
  const panelWidth = Math.min(560, window.innerWidth - 48);
  const panelHeight = Math.min(680, window.innerHeight - 120);
  const margin = 16;
  let x = rect.left + rect.width + 12;
  if (x + panelWidth > window.innerWidth - margin) {
    x = rect.left - panelWidth - 12;
  }
  x = clamp(x, margin, window.innerWidth - panelWidth - margin);
  let y = rect.top - 12;
  if (y + panelHeight > window.innerHeight - margin) {
    y = window.innerHeight - panelHeight - margin;
  }
  y = clamp(y, margin, window.innerHeight - panelHeight - margin);
  panel.style.left = `${x}px`;
  panel.style.top = `${y}px`;
}

function createMonitorDom() {
  createStyles();
  const bubble = document.createElement("div");
  bubble.className = "ss-ta-monitor-bubble";
  bubble.title = "Task Agent Live Monitor";
  bubble.textContent = "TA";
  bubble.dataset.health = "idle";

  const panel = document.createElement("div");
  panel.className = "ss-ta-monitor-panel";
  panel.innerHTML = `
    <div class="ss-ta-monitor-header">
      <div class="ss-ta-monitor-title">Task Agent Live Monitor</div>
      <div class="ss-ta-monitor-actions">
        <button data-action="refresh">刷新</button>
        <button data-action="close">关闭</button>
      </div>
    </div>
    <div class="ss-ta-monitor-body">
      <div class="ss-ta-grid">
        <div class="ss-ta-card"><div class="ss-ta-label">Health</div><div class="ss-ta-value" data-field="health"></div></div>
        <div class="ss-ta-card"><div class="ss-ta-label">Backend</div><div class="ss-ta-value" data-field="backend"></div></div>
        <div class="ss-ta-card"><div class="ss-ta-label">Model</div><div class="ss-ta-value" data-field="model"></div></div>
        <div class="ss-ta-card"><div class="ss-ta-label">Queue</div><div class="ss-ta-value" data-field="queue"></div></div>
        <div class="ss-ta-card"><div class="ss-ta-label">CUDA</div><div class="ss-ta-value" data-field="gpu"></div></div>
        <div class="ss-ta-card"><div class="ss-ta-label">System RAM</div><div class="ss-ta-value" data-field="system_memory"></div></div>
        <div class="ss-ta-card"><div class="ss-ta-label">Process</div><div class="ss-ta-value" data-field="process"></div></div>
        <div class="ss-ta-card"><div class="ss-ta-label">Task</div><div class="ss-ta-value" data-field="active_task"></div></div>
      </div>
      <div class="ss-ta-card" data-field-card="error" style="display:none;margin-bottom:12px;">
        <div class="ss-ta-label">Latest Error</div>
        <div class="ss-ta-value" data-field="error"></div>
      </div>
      <div class="ss-ta-log">
        <div class="ss-ta-log-title">
          <span data-field="log_name">Log</span>
          <span data-field="updated">-</span>
        </div>
        <pre data-field="log_tail">等待状态...</pre>
      </div>
      <div class="ss-ta-output">
        <div class="ss-ta-output-title">
          <span data-field="output_title">Live Output</span>
          <span data-field="output_time">-</span>
        </div>
        <pre data-field="live_output">暂无输出</pre>
      </div>
    </div>
  `;
  document.body.appendChild(panel);
  document.body.appendChild(bubble);
  placeBubble(bubble);
  placePanelNearBubble(panel, bubble);
  return { bubble, panel };
}

function setText(panel, field, value) {
  const element = panel.querySelector(`[data-field="${field}"]`);
  if (element) element.textContent = value || "-";
}

function renderPayload(dom, payload) {
  const summary = backendSummary(payload);
  dom.bubble.dataset.health = summary.health;
  const healthHtml = `<span class="ss-ta-health" data-health="${summary.health}"><span class="ss-ta-dot"></span>${summary.health}</span>`;
  const healthElement = dom.panel.querySelector('[data-field="health"]');
  if (healthElement) healthElement.innerHTML = healthHtml;

  const queue = payload?.queue || {};
  const gpu = extractGpu(payload?.memory);
  const systemMemory = extractSystemMemory(payload?.memory);
  const processMemory = extractProcessMemory(payload?.memory);
  setText(dom.panel, "backend", `${summary.provider} / ${summary.profile}`);
  setText(dom.panel, "model", summary.model);
  setText(dom.panel, "queue", `running=${queue.running ?? "-"} pending=${queue.pending ?? "-"}`);
  setText(dom.panel, "gpu", gpu ? `used ${formatMb(gpu.used)} / ${formatMb(gpu.total)} | torch ${formatMb(gpu.reserved)}/${formatMb(gpu.allocated)}` : "-");
  setText(dom.panel, "system_memory", systemMemory ? `${systemMemory.load ?? "-"}% | ${formatMb(systemMemory.used)} / ${formatMb(systemMemory.total)}` : "-");
  const processText = processMemory ? `pid=${summary.pid} rss=${formatMb(processMemory.rss)} private=${formatMb(processMemory.private)}` : `pid=${summary.pid} backends=${payload?.backend_count ?? 0}`;
  setText(dom.panel, "process", processText);
  const taskAgent = payload?.task_agent || {};
  const activeTask = taskAgent.active_task;
  const lastOutput = taskAgent.last_output;
  if (activeTask) {
    setText(dom.panel, "active_task", `${activeTask.status || "running"} | ${activeTask.task_type || "-"} | ${activeTask.message || activeTask.stage || ""}`);
    setText(dom.panel, "output_title", `Running: ${activeTask.task_type || "Task"}`);
    setText(dom.panel, "output_time", new Date((activeTask.updated_at || activeTask.started_at || 0) * 1000).toLocaleTimeString());
  } else {
    setText(dom.panel, "active_task", lastOutput ? `${lastOutput.status || "done"} | ${lastOutput.task_type || "-"}` : "idle");
    setText(dom.panel, "output_title", lastOutput ? `Last Output: ${lastOutput.task_type || "Task"}` : "Live Output");
    setText(dom.panel, "output_time", lastOutput?.timestamp ? new Date(lastOutput.timestamp * 1000).toLocaleTimeString() : "-");
  }
  const latestOutputEvent = [...(taskAgent.events || [])]
    .reverse()
    .find((item) => item.output_text || item?.metadata?.raw_text_preview);
  setText(
    dom.panel,
    "live_output",
    activeTask?.stream_text
      || latestOutputEvent?.output_text
      || latestOutputEvent?.metadata?.raw_text_preview
      || lastOutput?.output_text
      || activeTask?.message
      || "暂无输出"
  );
  setText(dom.panel, "log_name", payload?.latest_log?.name || "Log");
  setText(dom.panel, "updated", new Date().toLocaleTimeString());
  setText(dom.panel, "log_tail", payload?.latest_log?.tail || "暂无日志");

  const errorCard = dom.panel.querySelector('[data-field-card="error"]');
  setText(dom.panel, "error", summary.error);
  if (errorCard) errorCard.style.display = summary.error ? "block" : "none";
}

function installDragging(dom) {
  const { bubble, panel } = dom;
  let dragging = false;
  let moved = false;
  let offsetX = 0;
  let offsetY = 0;

  bubble.addEventListener("pointerdown", (event) => {
    dragging = true;
    moved = false;
    const rect = bubble.getBoundingClientRect();
    offsetX = event.clientX - rect.left;
    offsetY = event.clientY - rect.top;
    bubble.classList.add("dragging");
    bubble.setPointerCapture?.(event.pointerId);
  });

  bubble.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    moved = true;
    const size = 52;
    const margin = 8;
    const x = clamp(event.clientX - offsetX, margin, window.innerWidth - size - margin);
    const y = clamp(event.clientY - offsetY, margin, window.innerHeight - size - margin);
    bubble.style.left = `${x}px`;
    bubble.style.top = `${y}px`;
    placePanelNearBubble(panel, bubble);
  });

  bubble.addEventListener("pointerup", (event) => {
    if (!dragging) return;
    dragging = false;
    bubble.classList.remove("dragging");
    bubble.releasePointerCapture?.(event.pointerId);
    const rect = bubble.getBoundingClientRect();
    savePosition(rect.left, rect.top);
    if (moved) {
      event.preventDefault();
      event.stopPropagation();
      setTimeout(() => {
        moved = false;
      }, 0);
    }
  });

  bubble.addEventListener("click", (event) => {
    if (moved) {
      event.preventDefault();
      event.stopPropagation();
    }
  }, true);

  window.addEventListener("resize", () => {
    placeBubble(bubble);
    placePanelNearBubble(panel, bubble);
  });
}

async function fetchMonitorStatus(dom) {
  try {
    const response = await fetch(`${STATUS_URL}?t=${Date.now()}`, { cache: "no-store" });
    const payload = await response.json();
    renderPayload(dom, payload);
  } catch (error) {
    dom.bubble.dataset.health = "error";
    setText(dom.panel, "updated", new Date().toLocaleTimeString());
    setText(dom.panel, "log_tail", `monitor request failed: ${error}`);
  }
}

function installMonitor() {
  if (document.querySelector(".ss-ta-monitor-bubble")) return;
  const dom = createMonitorDom();
  installDragging(dom);
  let open = false;
  let timer = null;

  const schedule = () => {
    clearInterval(timer);
    timer = setInterval(() => fetchMonitorStatus(dom), open ? 750 : 8000);
  };

  dom.bubble.addEventListener("click", () => {
    open = !open;
    dom.panel.classList.toggle("open", open);
    placePanelNearBubble(dom.panel, dom.bubble);
    fetchMonitorStatus(dom);
    schedule();
  });
  dom.panel.querySelector('[data-action="close"]')?.addEventListener("click", () => {
    open = false;
    dom.panel.classList.remove("open");
    schedule();
  });
  dom.panel.querySelector('[data-action="refresh"]')?.addEventListener("click", () => fetchMonitorStatus(dom));

  fetchMonitorStatus(dom);
  schedule();
}

app.registerExtension({
  name: "ComfyUI.StudioSuite.TaskAgentLiveMonitor",
  setup() {
    requestAnimationFrame(installMonitor);
  },
});
