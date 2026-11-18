/* 工作台交互 —— 零构建原生 JS */
const $ = (id) => document.getElementById(id);
const STYLES = ["幽默对谈","漫才","深夜电台","访谈","辩论","实战课堂","故事剧场","纪实探索","热点解读"];
let curStyle = "幽默对谈";
let voices = [];

// ---------- 初始化 ----------
async function init() {
  renderStyles();
  try { voices = await (await fetch("/api/voices")).json(); } catch {}
  fillVoices($("hostVoice"), "female");
  fillVoices($("partnerVoice"), "male");
  try {                                            // 应用设置页里的任务默认值
    const s = await (await fetch("/api/settings")).json();
    if (STYLES.includes(s.defaults.style)) { curStyle = s.defaults.style; renderStyles(); }
    $("minutes").value = s.defaults.minutes;
    $("minutesVal").textContent = s.defaults.minutes + " 分钟";
    $("hostVoice").value = s.defaults.host_voice;
    $("partnerVoice").value = s.defaults.partner_voice;
  } catch {}
  $("minutes").addEventListener("input", e => $("minutesVal").textContent = e.target.value + " 分钟");
  setupDrop();
  $("create").addEventListener("click", createJob);
  $("topic").addEventListener("keydown", e => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) createJob();
  });
  refreshTasks(); setInterval(refreshTasks, 2500);
  setupRss();
  setupTemplates();
  document.querySelectorAll("[data-soon]").forEach(a =>
    a.addEventListener("click", e => { e.preventDefault(); alert("该页面在 UI-4/UI-5 迭代中"); }));
}

// ---------- 节目模板（P3-7） ----------
let templates = [], curTpl = null;
async function setupTemplates() {
  try { templates = await (await fetch("/api/templates")).json(); } catch {}
  $("tplSel").innerHTML = `<option value="">无（手动配置）</option>` +
    templates.map(t => `<option value="${t.id}">${t.name}</option>`).join("");
  $("tplSel").addEventListener("change", () => {
    curTpl = templates.find(t => t.id === $("tplSel").value) || null;
    $("btnDelTpl").hidden = !curTpl;
    if (!curTpl) return;
    if (STYLES.includes(curTpl.style)) { curStyle = curTpl.style; renderStyles(); }
    $("minutes").value = curTpl.minutes;
    $("minutesVal").textContent = curTpl.minutes + " 分钟";
    if (curTpl.host_voice) $("hostVoice").value = curTpl.host_voice;
    if (curTpl.partner_voice) $("partnerVoice").value = curTpl.partner_voice;
    toastMsg(`已应用模板「${curTpl.name}」`);
  });
  $("btnSaveTpl").addEventListener("click", async () => {
    const name = prompt("模板名称（如：深夜电台-小雅老张）");
    if (!name) return;
    const r = await fetch("/api/templates", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, style: curStyle, minutes: +$("minutes").value,
        host_voice: $("hostVoice").value, partner_voice: $("partnerVoice").value,
        bgm: curTpl?.bgm || "" })
    });
    if (!r.ok) return toastMsg("保存失败: " + (await r.json()).detail);
    templates = await (await fetch("/api/templates")).json();
    toastMsg(`模板「${name}」已保存`);
    renderTplOptions();
  });
  $("btnDelTpl").addEventListener("click", async () => {
    if (!curTpl || !confirm(`删除模板「${curTpl.name}」？`)) return;
    await fetch(`/api/templates/${curTpl.id}`, { method: "DELETE" });
    templates = await (await fetch("/api/templates")).json();
    curTpl = null;
    renderTplOptions();
    toastMsg("模板已删除");
  });
}

function renderTplOptions() {
  $("tplSel").innerHTML = `<option value="">无（手动配置）</option>` +
    templates.map(t => `<option value="${t.id}">${t.name}</option>`).join("");
  $("tplSel").value = curTpl?.id || "";
  $("btnDelTpl").hidden = !curTpl;
}

// ---------- RSS 批量（P1-14） ----------
function setupRss() {
  let previewItems = [];
  $("rssPreview").addEventListener("click", async () => {
    const url = $("rssUrl").value.trim();
    if (!url) return toastMsg("请先粘贴 RSS 地址");
    $("rssItems").textContent = "解析中…";
    try {
      const r = await (await fetch("/api/rss/preview", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, limit: +$("rssLimit").value })
      })).json();
      if (r.detail) throw new Error(r.detail);
      previewItems = r.items || [];
      $("rssItems").innerHTML = `<b>${escapeHtml(r.feed_title || "")}</b><br>` +
        previewItems.map((it, i) => `${i + 1}. ${escapeHtml(it.title)}`).join("<br>");
      $("rssBatch").hidden = !previewItems.length;
    } catch (e) { $("rssItems").textContent = "解析失败: " + e.message; }
  });
  $("rssBatch").addEventListener("click", async () => {
    const url = $("rssUrl").value.trim();
    $("rssBatch").disabled = true; $("rssBatch").textContent = "创建中…";
    try {
      const r = await (await fetch("/api/jobs/batch", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, limit: +$("rssLimit").value,
          style: curStyle, minutes: +$("minutes").value,
          provider: $("provider").value,
          host_voice: $("hostVoice").value, partner_voice: $("partnerVoice").value,
          bgm: curTpl?.bgm || "" })
      })).json();
      if (r.detail) throw new Error(r.detail);
      toastMsg(`已创建 ${r.count} 个任务（排队生成中）`);
      refreshTasks();
    } catch (e) { toastMsg("批量创建失败: " + e.message); }
    finally { $("rssBatch").disabled = false; $("rssBatch").textContent = "批量生成"; }
  });
}
function toastMsg(msg) {                         // 工作台轻提示（无 toast 元素时退化为 badge）
  const el = document.getElementById("createErr");
  el.textContent = msg; el.hidden = false; el.style.color = "var(--green)";
  setTimeout(() => { el.hidden = true; el.style.color = ""; }, 3200);
}

function renderStyles() {
  $("styles").innerHTML = STYLES.map(s =>
    `<button class="${s === curStyle ? "on" : ""}" data-s="${s}">${s}</button>`).join("");
  $("styles").querySelectorAll("button").forEach(b =>
    b.addEventListener("click", () => { curStyle = b.dataset.s; renderStyles(); }));
}

function fillVoices(sel, gender) {
  sel.innerHTML = voices.filter(v => v.gender === gender)
    .map(v => `<option value="${v.id}">${v.name} · ${v.style_tags[0] || ""}</option>`).join("");
}

// ---------- 素材拖拽 ----------
function setupDrop() {
  const drop = $("drop"), file = $("file");
  drop.addEventListener("click", () => file.click());
  file.addEventListener("change", () => {
    if (file.files.length) $("dropText").textContent = "素材文件: " + file.files[0].name + "（提交时自动抽取正文）";
  });
  ["dragover", "dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault();
    drop.classList.toggle("over", ev === "dragover");
    if (ev === "drop" && e.dataTransfer.files.length) {
      file.files = e.dataTransfer.files;
      $("dropText").textContent = "素材文件: " + file.files[0].name;
    }
  }));
}

// ---------- 创建任务 ----------
async function createJob() {
  const btn = $("create"), err = $("createErr");
  err.hidden = true;
  const topic = $("topic").value.trim();
  if (!topic) { err.textContent = "请先输入主题或粘贴链接"; err.hidden = false; return; }
  btn.disabled = true; btn.textContent = "提交中…";
  try {
    let material = "";
    const f = $("file").files[0];
    if (f) {                                     // 真实上传 → 服务器路径作为素材
      const fd = new FormData(); fd.append("file", f);
      const up = await fetch("/api/upload", { method: "POST", body: fd });
      const ud = await up.json();
      if (!up.ok) throw new Error(ud.detail || "上传失败");
      material = ud.path;
    } else if (/^https?:\/\//.test(topic)) {     // 主题就是链接 → 服务端抓正文
      material = topic;
    }
    const r = await fetch("/api/jobs", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        topic, style: curStyle, minutes: +$("minutes").value,
        material, provider: $("provider").value,
        bgm: curTpl?.bgm || "",
        host_voice: $("hostVoice")?.value || "",
        partner_voice: $("partnerVoice")?.value || "",
        tts_engine: $("ttsEngine")?.value || ""
      })
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    refreshTasks();
  } catch (e) { err.textContent = "创建失败: " + e.message; err.hidden = false; }
  finally { btn.disabled = false; btn.textContent = "生成播客"; }
}

// ---------- 任务列表 ----------
function jobTime(j) {                            // 从 job_id 解析创建时间 MM-DD HH:MM
  try {
    const p = j.job_id.split("-");               // YYYYMMDD-HHMMSS-xxxx
    return `${p[0].slice(4,6)}-${p[0].slice(6,8)} ${p[1].slice(0,2)}:${p[1].slice(2,4)}`;
  } catch { return ""; }
}
function taskRow(j, withActions) {
  const [cls, label] = STATUS_BADGE[j.status] || ["script", j.status];
  const step = j.status === "running" && j.step ? ` · ${stepLabel(j.step)}` : "";
  const meta = j.error ? escapeHtml(j.error).slice(0, 50)
    : `${j.minutes} 分钟 · ${j.style} · ${jobTime(j)}${step}`;
  return `<div class="task" onclick="location.href='/job/${j.job_id}'">
    <div class="cover"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="1.5" stroke-linecap="round"><path d="M3 10v4M7 7v10M11 4v16M15 7v10M19 10v4"/></svg></div>
    <div class="info"><div class="title">${escapeHtml(j.topic)}</div>
      <div class="meta">${meta}</div></div>
    ${withActions ? `<span class="badge ${cls}">${label}${j.status === "running" ? " ⟳" : ""}</span>
      <a class="detail-link" onclick="event.stopPropagation();location.href='/job/${j.job_id}'">查看详情 →</a>` : ""}
  </div>`;
}

const STATUS_BADGE = {
  done: ["done", "已完成"], script_ready: ["script", "脚本就绪"],
  running: ["running", "生成中"], synthesizing: ["running", "合成中"],
  queued: ["script", "排队中"], failed: ["failed", "失败"],
};
async function refreshTasks() {
  let jobs = [];
  try { jobs = await (await fetch("/api/jobs")).json(); } catch { return; }
  $("stJobs").textContent = jobs.length;
  $("stDone").textContent = jobs.filter(j => j.status === "done" || j.status === "script_ready").length;
  $("stRunning").textContent = jobs.filter(j => ["running", "synthesizing", "queued"].includes(j.status)).length;
  renderWeekBars(jobs);
  const el = $("tasks");
  if (!jobs.length) return;
  jobs = jobs.slice(0, 5);                       // 工作台只显示最近 5 个，全部见任务中心
  el.innerHTML = jobs.map(j => taskRow(j, false)).join("");
}
function stepLabel(s) {
  return { research: "抽取素材", draft: "戏剧化改写", rewrite: "口语化", validate: "校验", synthesize: "语音合成" }[s] || s;
}

// 本周产出迷你条形图（按 job.json 修改时间聚合；mock 数据兜底）
function renderWeekBars(jobs) {
  const bars = new Array(7).fill(0);
  const today = (new Date().getDay() + 6) % 7;          // 周一=0
  for (const j of jobs) {
    if (!["done", "script_ready"].includes(j.status)) continue;
    let d = today;                                       // 无时间戳时归入今天
    try {
      const dir = j.job_id.split("-");                   // YYYYMMDD-HHMMSS-xxxx
      const day = new Date(`${dir[0].slice(0,4)}-${dir[0].slice(4,6)}-${dir[0].slice(6,8)}`).getDay();
      d = (day + 6) % 7;
    } catch {}
    bars[d] += Math.round((j.est_seconds || j.minutes * 60) / 60);
  }
  if (!bars.some(v => v > 0)) bars[today] = 0;           // 全 0 也渲染坐标
  const max = Math.max(...bars, 10);
  $("weekBars").innerHTML = bars.map((v, i) =>
    `<div class="bar${i === today ? " today" : ""}" style="height:${Math.max(10, v / max * 46)}px"
          title="周${"一二三四五六日"[i]} · ${v} 分钟"></div>`).join("");
}

// ---------- 详情 ----------
async function showDetail(id) {
  const j = await (await fetch("/api/jobs/" + id)).json();
  $("dTitle").textContent = j.topic || id;
  $("dMeta").textContent =
    `状态 ${j.status} · ${j.minutes} 分钟 · ${j.style} · 预计 ${Math.round((j.est_seconds || j.minutes * 60) / 60)} min` +
    (j.error ? ` · ${j.error}` : "");
  const turns = j.script?.text || [];
  const names = Object.fromEntries((j.script?.speakers || []).map(s => [s.id, s.id]));
  $("dTurns").innerHTML = turns.length ? turns.map(([sid, txt], i) =>
    `<div class="turn s${sid === "S1" ? 1 : 2}">
       <div class="who">${sid}</div>
       <div>${renderTags(escapeHtml(txt))}</div>
     </div>`).join("")
    : `<div class="empty small">脚本尚未生成</div>`;
  $("detail").showModal();
}
function renderTags(t) {
  return t.replace(/&lt;\|(laughter)\|&gt;/g, '<span class="tag">笑声</span>')
          .replace(/&lt;\|(sigh)\|&gt;/g, '<span class="tag">叹气</span>')
          .replace(/&lt;\|(breathing)\|&gt;/g, '<span class="tag">呼吸</span>')
          .replace(/&lt;\|(coughing)\|&gt;/g, '<span class="tag">咳嗽</span>')
          .replace(/&lt;\|(throat_clearing)\|&gt;/g, '<span class="tag">清嗓</span>');
}
function escapeHtml(s) {
  return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

init();
