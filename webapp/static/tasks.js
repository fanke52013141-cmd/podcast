/* 任务中心 —— 全量列表 / 状态筛选 / 删除 */
const $ = (id) => document.getElementById(id);
let jobs = [], filter = "all";

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
      <div class="cover"><img src="/api/jobs/${j.job_id}/cover" onerror="this.replaceWith(Object.assign(document.createElement('div'),{textContent:'▶'}))"></div>
      <div class="info"><div class="title">${escapeHtml(j.topic || j.job_id)}</div>
        <div class="meta">${j.minutes} 分钟 · ${j.style} · ${j.created || ""}${j.error ? " · " + escapeHtml(j.error).slice(0, 50) : ""}</div></div>
      <a class="detail-link" onclick="event.stopPropagation();location.href='/job/${j.job_id}'">查看详情 →</a>
      <span class="badge ${cls}">${label}</span>
      <button class="ghost del" onclick="delJob(event,'${j.job_id}','${escapeHtml(j.topic || "").replace(/'/g, "")}')">删除</button>
    </div>`;
  }).join("");
}

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
