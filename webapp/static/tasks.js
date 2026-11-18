/* 任务中心 —— 全量列表 / 状态筛选 / 删除 */
const $ = (id) => document.getElementById(id);
let jobs = [], filter = "all";
const taskPlayer = new Audio();
taskPlayer.preload = "none";
let playingJob = "";

function jobTimeFull(j) {
  const m = String(j.created || "").match(/^(\d{4})(\d{2})(\d{2})[ -](\d{2})(\d{2})(\d{2})/);
  if (m) return `${m[1]}.${m[2]}.${m[3]} ${m[4]}:${m[5]}:${m[6]}`;
  try {
    const p = j.job_id.split("-");
    return `${p[0].slice(0,4)}.${p[0].slice(4,6)}.${p[0].slice(6,8)} ${p[1].slice(0,2)}:${p[1].slice(2,4)}:${p[1].slice(4,6)}`;
  } catch { return ""; }
}

async function init() {
  document.querySelectorAll("#filters button").forEach(b =>
    b.addEventListener("click", () => {
      filter = b.dataset.f;
      document.querySelectorAll("#filters button").forEach(x => x.classList.toggle("on", x === b));
      render();
    }));
  await refresh(); setInterval(refresh, 3000);
}

async function refresh() {
  try { jobs = await (await fetch("/api/jobs")).json(); } catch { return; }
  const done = jobs.filter(j => j.status === "done").length;
  const mins = Math.round(jobs.reduce((a, j) => a + (j.minutes || 0), 0));
  $("sum").textContent = `${jobs.length} 个任务 · ${done} 个成品 · 约 ${mins} 分钟`;
  render();
}

function render() {
  const map = { all: () => true,
    active: j => ["queued", "running", "synthesizing"].includes(j.status),
    script_ready: j => j.status === "script_ready",
    done: j => j.status === "done", failed: j => j.status === "failed" };
  const list = jobs.filter(map[filter] || map.all);
  const el = $("list");
  if (!list.length) { el.innerHTML = `<div class="empty">该状态下没有任务</div>`; return; }
  const badge = { done: ["done", "已完成"], script_ready: ["script", "脚本就绪"],
    running: ["running", "生成中"], synthesizing: ["running", "合成中"],
    queued: ["script", "排队中"], failed: ["failed", "失败"], postprocessing: ["running", "后期中"] };
  el.innerHTML = list.map(j => {
    const [cls, label] = badge[j.status] || ["script", j.status];
    return `<div class="task" onclick="location.href='/job/${j.job_id}'">
      <button class="tplay ${playingJob === j.job_id ? "playing" : ""}" data-id="${j.job_id}"
        onclick="toggleTaskAudio(event,'${j.job_id}',this)" title="播放" aria-label="播放">
        <svg class="ic-play" viewBox="0 0 24 24" fill="currentColor"><path d="M7 5l12 7-12 7z"/></svg>
        <svg class="ic-pause" viewBox="0 0 24 24" fill="currentColor"><rect x="7" y="5" width="3.5" height="14" rx=".8"/><rect x="13.5" y="5" width="3.5" height="14" rx=".8"/></svg>
      </button>
      <div class="info"><div class="title">${escapeHtml(j.topic || j.job_id)}</div>
        <div class="meta">${j.minutes} 分钟 · ${j.style}</div></div>
      <span class="badge ${cls}">${label}</span>
      <span class="ttime mono">${jobTimeFull(j)}</span>
      <a class="detail-link" onclick="event.stopPropagation();location.href='/job/${j.job_id}'">详情 →</a>
      <button class="ghost del" onclick="delJob(event,'${j.job_id}','${escapeHtml(j.topic || "").replace(/'/g, "")}')">删除</button>
    </div>`;
  }).join("");
  syncPlayingButtons();
}

function syncPlayingButtons() {
  document.querySelectorAll(".tplay").forEach(b => {
    const on = b.dataset.id === playingJob;
    b.classList.toggle("playing", on);
    b.title = on ? "暂停" : "播放";
    b.setAttribute("aria-label", on ? "暂停" : "播放");
  });
}
function toggleTaskAudio(e, id, btn) {
  e.stopPropagation();
  if (playingJob === id && !taskPlayer.paused) {
    taskPlayer.pause();
    playingJob = "";
    syncPlayingButtons();
    return;
  }
  taskPlayer.pause();
  playingJob = id;
  syncPlayingButtons();
  taskPlayer.src = `/api/jobs/${id}/play`;
  const p = taskPlayer.play();
  if (p && p.catch) p.catch(() => {
    playingJob = "";
    syncPlayingButtons();
    toast("该任务暂无可播放音频（音频尚未生成）");
  });
}
taskPlayer.addEventListener("ended", () => {
  playingJob = ""; syncPlayingButtons();
});

async function delJob(e, id, name) {
  e.stopPropagation();
  if (!confirm(`确定删除任务「${name || id}」？\n音频、脚本、字幕将一并删除，不可恢复。`)) return;
  await fetch(`/api/jobs/${id}`, { method: "DELETE" });
  toast("已删除"); refresh();
}

function escapeHtml(s) {
  return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(t._h); t._h = setTimeout(() => t.hidden = true, 2400);
}

init();
