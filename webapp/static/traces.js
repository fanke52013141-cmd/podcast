/* 运行日志查看器 —— /api/traces */
const $ = (id) => document.getElementById(id);
const EVENT_NAME = { status: "状态", step: "步骤", error: "错误", step_ms: "耗时",
                     edit: "剪辑", postprocess: "后期" };

async function init() {
  const jobs = await (await fetch("/api/jobs")).json().catch(() => []);
  const sel = $("jobSel");
  sel.innerHTML = `<option value="">全部任务（最近 200 条）</option>` +
    jobs.map(j => `<option value="${j.job_id}">${escapeHtml(j.topic || j.job_id)}</option>`).join("");
  const q = new URLSearchParams(location.search).get("job");
  if (q) sel.value = q;
  sel.addEventListener("change", load);
  $("btnRefresh").addEventListener("click", load);
  load();
}

async function load() {
  const jid = $("jobSel").value;
  const list = await (await fetch("/api/traces?limit=200" + (jid ? "&job_id=" + jid : ""))).json();
  const el = $("traceList");
  if (!list.length) { el.innerHTML = `<div class="empty">暂无日志</div>`; return; }
  el.innerHTML = list.map(r => {
    const meta = Object.entries(r).filter(([k]) => !["ts", "job", "event"].includes(k))
      .map(([k, v]) => `${k}=${v}`).join(" · ");
    const cls = r.event === "error" ? "failed" : r.event === "step_ms" ? "running" : "script";
    return `<div class="trow" style="cursor:default">
      <div class="tmono" style="width:130px">${r.ts.replace("T", " ").slice(5)}</div>
      <span class="badge ${cls}" style="width:56px;text-align:center">${EVENT_NAME[r.event] || r.event}</span>
      <div class="txt mono small" style="flex:1">${escapeHtml(meta)}</div>
      <a class="small" style="color:var(--amber);text-decoration:none"
         href="/job/${r.job}">${r.job.slice(-6)}</a>
    </div>`;
  }).join("");
}

function escapeHtml(s) {
  return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

init();
