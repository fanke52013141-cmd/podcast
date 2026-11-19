/* 工作台交互 —— 零构建原生 JS */
const $ = (id) => document.getElementById(id);
let stylesCache = [];
let curStyle = "幽默对谈";
let voices = [];
const taskPlayer = new Audio();
taskPlayer.preload = "none";
let playingJob = "";

// ---------- 初始化 ----------
async function init() {
  await loadStyles();
  try { voices = await (await fetch("/api/voices")).json(); } catch {}
  fillVoices($("hostVoice"), "female");
  fillVoices($("partnerVoice"), "male");
  try {                                            // 应用设置页里的任务默认值
    const s = await (await fetch("/api/settings")).json();
    if (stylesCache.some(x => x.name === s.defaults.style)) { curStyle = s.defaults.style; renderStyles(); }
    $("minutes").value = s.defaults.minutes;
    $("minutesVal").textContent = s.defaults.minutes + " 分钟";
    // 默认音色在下拉中不存在时自动回落第一项（防止下拉空白）
    for (const [sel, want] of [[$("hostVoice"), s.defaults.host_voice],
                               [$("partnerVoice"), s.defaults.partner_voice]]) {
      if ([...sel.options].some(o => o.value === want)) sel.value = want;
      else if (sel.options.length) sel.selectedIndex = 0;
    }
  } catch {}
  $("minutes").addEventListener("input", e => $("minutesVal").textContent = e.target.value + " 分钟");
  setupDrop();
  $("create").addEventListener("click", createJob);
  $("topic").addEventListener("keydown", e => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) createJob();
  });
  refreshTasks(); setInterval(refreshTasks, 2500);
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
    if (stylesCache.some(x => x.name === curTpl.style)) { curStyle = curTpl.style; renderStyles(); }
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
  window.fancySync && window.fancySync();
}

// ---------- RSS 批量（P1-14，已下线） ----------
function toastMsg(msg) {                         // 工作台轻提示（无 toast 元素时退化为 badge）
  const el = document.getElementById("createErr");
  el.textContent = msg; el.hidden = false; el.style.color = "var(--green)";
  setTimeout(() => { el.hidden = true; el.style.color = ""; }, 3200);
}

async function loadStyles() {
  try {
    const data = await (await fetch("/api/styles")).json();
    stylesCache = data.styles || [];
  } catch { stylesCache = []; }
  renderStyles();
}

function renderStyles() {
  const el = $("styles");
  if (!el) return;
  if (!stylesCache.some(s => s.name === curStyle) && stylesCache.length) {
    curStyle = stylesCache[0].name;
  }
  el.innerHTML = stylesCache.map(s =>
    `<button class="${s.name === curStyle ? "on" : ""}" data-s="${escapeHtml(s.name)}"
      title="${escapeHtml(s.desc || s.name)}" aria-pressed="${s.name === curStyle}">${escapeHtml(s.name)}</button>`).join("")
    + `<a class="style-manage" href="/settings#secStyles">管理</a>`;
  el.querySelectorAll("button[data-s]").forEach(b =>
    b.addEventListener("click", () => { curStyle = b.dataset.s; renderStyles(); }));
}

function fillVoices(sel, gender) {
  sel.innerHTML = voices.filter(v => v.gender === gender)
    .map(v => `<option value="${v.id}">${v.name} · ${v.style_tags[0] || ""}</option>`).join("");
  window.fancySync && window.fancySync();          // 自定义下拉同步触发文本
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
function jobTimeFull(j) {                        // 从 created / job_id 解析完整创建时间
  const m = String(j.created || "").match(/^(\d{4})(\d{2})(\d{2})[ -](\d{2})(\d{2})(\d{2})/);
  if (m) return `${m[1]}.${m[2]}.${m[3]} ${m[4]}:${m[5]}:${m[6]}`;
  try {
    const p = j.job_id.split("-");               // YYYYMMDD-HHMMSS-xxxx
    return `${p[0].slice(0,4)}.${p[0].slice(4,6)}.${p[0].slice(6,8)} ${p[1].slice(0,2)}:${p[1].slice(2,4)}:${p[1].slice(4,6)}`;
  } catch { return ""; }
}
function taskRow(j, withActions) {
  const [cls, label] = STATUS_BADGE[j.status] || ["script", j.status];
  const step = j.status === "running" && j.step ? ` · ${stepLabel(j.step)}` : "";
  const meta = j.error ? escapeHtml(j.error).slice(0, 50)
    : `${j.minutes} 分钟 · ${j.style}${step}`;
  return `<div class="task" onclick="location.href='/job/${j.job_id}'">
    <button class="tplay ${playingJob === j.job_id ? "playing" : ""}" data-id="${j.job_id}"
      onclick="toggleTaskAudio(event,'${j.job_id}',this)" title="播放" aria-label="播放">
      <svg class="ic-play" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round"><path d="M7 5l12 7-12 7z" fill="currentColor" stroke="none"/></svg>
      <svg class="ic-pause" viewBox="0 0 24 24" fill="currentColor"><rect x="7" y="5" width="3.5" height="14" rx=".8"/><rect x="13.5" y="5" width="3.5" height="14" rx=".8"/></svg>
    </button>
    <div class="info"><div class="title">${escapeHtml(j.topic)}</div>
      <div class="meta">${meta}</div></div>
    ${withActions ? `<span class="badge ${cls}">${label}${j.status === "running" ? " ⟳" : ""}</span>
      <a class="detail-link" onclick="event.stopPropagation();location.href='/job/${j.job_id}'">查看详情 →</a>` : ""}
    <span class="ttime mono">${jobTimeFull(j)}</span>
  </div>`;
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
    taskToast("该任务暂无可播放音频（音频尚未生成）");
  });
}
taskPlayer.addEventListener("ended", () => {
  playingJob = ""; syncPlayingButtons();
});
function taskToast(msg) {
  const t = $("taskToast");
  if (!t) return toastMsg(msg);
  t.textContent = msg; t.hidden = false;
  clearTimeout(t._h); t._h = setTimeout(() => t.hidden = true, 3000);
}

const STATUS_BADGE = {
  done: ["done", "已完成"], script_ready: ["script", "脚本就绪"],
  running: ["running", "生成中"], synthesizing: ["running", "合成中"],
  queued: ["script", "排队中"], failed: ["failed", "失败"],
};
async function refreshTasks() {
  let jobs = [];
  try { jobs = await (await fetch("/api/jobs")).json(); } catch { return; }
  const el = $("tasks");
  if (!jobs.length) {
    el.innerHTML = `<div class="empty">当前账号还没有任务，从上方开始创作吧</div>`;
    return;
  }
  jobs = jobs.slice(0, 5);                       // 工作台只显示最近 5 个，全部见任务中心
  el.innerHTML = jobs.map(j => taskRow(j, false)).join("");
  syncPlayingButtons();
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
