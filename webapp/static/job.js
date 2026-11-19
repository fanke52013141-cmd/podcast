/* 任务详情页 —— 流水线步骤条 / 播放器 / 后期开关 / 轮次脚本（SSE 实时） */
const $ = (id) => document.getElementById(id);
const JID = location.pathname.split("/").pop();
const STEPS = [
  ["research", "素材抽取"], ["draft", "戏剧化改写"], ["rewrite", "口语化"],
  ["validate", "校验"], ["synthesize", "语音合成"], ["postprocess", "后期混音"], ["qc", "质检"],
];
let job = null;

async function init() {
  await load();
  renderStepper(); renderTurns(); renderPlayer(); setupEdit(); setupSfx(); setupShowNotes();
  subscribe();
  // 后期开关持久化
  for (const k of ["swBgm", "swSrt", "swWm"]) {
    const v = localStorage.getItem(JID + k);
    if (v !== null) $(k).checked = v === "1";
    $(k).addEventListener("change", e => localStorage.setItem(JID + k, e.target.checked ? "1" : "0"));
  }
  document.querySelectorAll("[data-soon]").forEach(a =>
    a.addEventListener("click", e => { e.preventDefault(); toast("该页面在后续迭代中"); }));
}

async function load() {
  const r = await fetch("/api/jobs/" + JID);
  if (!r.ok) { $("jTopic").textContent = "任务不存在"; throw new Error("404"); }
  job = await r.json();
  $("jTopic").textContent = job.topic || JID;
  const map = { done: ["done", "已完成"], script_ready: ["script", "脚本就绪"],
    running: ["running", "生成中"], synthesizing: ["running", "合成中"],
    queued: ["script", "排队中"], failed: ["failed", "失败"] };
  const [cls, label] = map[job.status] || ["script", job.status];
  const st = $("jState"); st.textContent = label; st.className = "badge " + cls;
  $("turnCount").textContent = job.script ? `· ${job.script.text.length} 轮` : "";
  const cost = job.llm_cost_yuan ? ` · LLM ≈¥${job.llm_cost_yuan}` : "";
  const toks = (job.llm_tokens_in || job.llm_tokens_out)
    ? ` · tokens ${job.llm_tokens_in}+${job.llm_tokens_out}` : "";
  $("engState").textContent =
    "引擎状态：" + (job.provider === "mock" ? "Mock 联调" : "DeepSeek API")
    + ` · ${job.minutes} 分钟 · 预计 ${Math.round((job.est_seconds || job.minutes * 60) / 60)} min`
    + cost + toks;
  if (job.error) { const e = $("valErr"); e.hidden = false; e.textContent = "" + job.error; }
}

// ---------- 流水线步骤条 ----------
function stepStates() {
  const s = job.step || "", status = job.status;
  const idx = STEPS.findIndex(([k]) => k === s);
  return STEPS.map(([k, label], i) => {
    let state = "todo";
    if (status === "done" || (status === "script_ready" && i < 4)) state = "done";
    else if (status === "synthesizing" && i < 4) state = "done";
    else if (status === "synthesizing" && k === "synthesize") state = "active";
    else if (status === "running") state = i < idx ? "done" : (i === idx ? "active" : "todo");
    if (status === "failed" && i === (idx < 0 ? 4 : idx)) state = "fail";
    if (status === "script_ready" && k === "synthesize") state = "skip";
    return { k, label, state };
  });
}
function renderStepper() {
  $("stepper").innerHTML = stepStates().map((s, i) => `
    <div class="step ${s.state}">
      <div class="dot">${s.state === "done" ? "✓" : s.state === "fail" ? "✕" : i + 1}</div>
      <div class="lbl">${s.label}${s.k === "validate" && (job.retry || 0) > 1
        ? `<em class="mono">${job.retry} 次回炉</em>` : ""}</div>
      ${i < STEPS.length - 1 ? '<div class="line"></div>' : ""}
    </div>`).join("");
  const sp = job.synth;   // "3/5"
  if (job.status === "synthesizing" && sp) {
    $("synthProg").hidden = false;
    $("synthText").textContent = `第 ${sp} 段 · 正在合成`;
    const [a, b] = sp.split("/").map(Number);
    $("synthFill").style.width = (a / b * 100) + "%";
  } else if (job.status === "running" && job.step === "rewrite") {
    $("synthProg").hidden = false;
    $("synthText").textContent = "口语化改写中…";
    $("synthFill").style.width = "40%";
  } else if (job.status === "running" && job.step === "validate") {
    $("synthProg").hidden = false;
    $("synthText").textContent = "规则校验中…";
    $("synthFill").style.width = "70%";
  } else if (job.status === "running" && job.step === "draft") {
    $("synthProg").hidden = false;
    $("synthText").textContent = "戏剧化改写中…";
    $("synthFill").style.width = "20%";
  } else if (["done", "script_ready", "failed"].includes(job.status)) {
    $("synthProg").hidden = true;
  }
}

// ---------- 轮次脚本 ----------
function estTimestamps(turns) {
  const rate = 4.5, out = []; let t = 0;
  for (const [, txt] of turns) { out.push(t); t += Math.max(1, txt.length) / rate; }
  out.push(t); return out;
}
function fillTurnSelect(id) {
  const turns = job.script?.text || [];
  const el = $(id);
  if (!el) return;
  el.innerHTML = turns.length
    ? turns.map(([sid], i) => `<option value="${i}">第 ${i + 1} 轮 · ${sid}</option>`).join("")
    : `<option value="">暂无轮次</option>`;
  el.disabled = !turns.length;
}
function syncSfxControls() {
  const turns = job.script?.text || [];
  fillTurnSelect("sfxTurn");
  $("sfxRow").hidden = !turns.length;
  $("sfxEmpty").hidden = !!turns.length;
  $("sfxList").hidden = !turns.length;
  $("sfxRatio").disabled = !turns.length;
  $("sfxGain").disabled = !turns.length;
  $("sfxName").disabled = !turns.length;
  $("btnAddSfx").disabled = !turns.length;
}
function renderTurns() {
  const turns = job.script?.text || [];
  fillTurnSelect("gainTurn");
  syncSfxControls();
  $("btnAddGain").disabled = !turns.length;
  $("gainDb").disabled = !turns.length;
  $("gainRow").hidden = !turns.length;
  $("gainEmpty").hidden = !!turns.length;
  if (!turns.length) {
    $("gainList").textContent = "";
    $("btnApplyEdit").disabled = true;
    return;
  }
  const ts = estTimestamps(turns);
  const bad = new Set(job.qc?.bad_turns || []);
  const cerMap = {};
  for (const d of (job.qc?.cer_detail || [])) cerMap[d.turn] = d.cer;
  $("turns").innerHTML = turns.map(([sid, txt], i) => `
    <div class="trow s${sid}"${bad.has(i) ? ' style="border-color:rgba(248,113,113,.5)"' : ""}>
      <div class="who">${sid}</div>
      <div class="txt">${renderTags(escapeHtml(txt))}</div>
      ${bad.has(i) ? `<span class="badge failed">CER ${(cerMap[i] * 100).toFixed(1)}% 待重做</span>` : ""}
      <div class="tmono">${fmt(ts[i])}</div>
      <button class="ghost redo" data-i="${i}">重做本轮</button>
    </div>`).join("");
  $("turns").querySelectorAll(".redo").forEach(b =>
    b.addEventListener("click", () => toast(`第 ${+b.dataset.i + 1} 轮重做将在 P3 质检闭环接入后可用`)));
  // 质检状态
  if (job.qc && $("qcSum")) {
    const q = job.qc;
    $("qcSum").innerHTML = q.mode === "unavailable"
      ? `质检：暂不可用（${escapeHtml(q.note || "") }）`
      : q.pass
        ? `质检通过 · 平均 CER ${(q.cer_avg * 100).toFixed(2)}%（${q.mode}）`
        : `<span style="color:var(--red)">质检未过 · ${bad.size} 轮待重做 · 平均 CER ${(q.cer_avg * 100).toFixed(2)}%</span>`;
  }
  // 剪辑面板：轮次下拉已由 fillTurnSelect 统一处理
  $("btnApplyEdit").disabled = !job.paths?.wav;
  if (job.paths?.edited_mp3) {
    $("btnEditedDl").hidden = false;
    $("btnEditedDl").href = `/api/jobs/${JID}/edited`;
  }
}

// ---------- 剪辑（UI-6） ----------
let turnGains = {};
function setupEdit() {
  $("btnAddGain").addEventListener("click", () => {
    const i = +$("gainTurn").value, db = +$("gainDb").value;
    turnGains[i] = db;
    $("gainList").textContent = Object.entries(turnGains)
      .map(([k, v]) => `第${+k + 1}轮 ${v > 0 ? "+" : ""}${v}dB`).join(" · ");
    toast(`已加入：第 ${i + 1} 轮 ${db}dB`);
  });
  $("btnApplyEdit").addEventListener("click", async () => {
    const btn = $("btnApplyEdit");
    btn.disabled = true; btn.textContent = "剪辑中…";
    try {
      const body = {
        speed: +$("editSpeed").value,
        turn_gains: turnGains,
        trim_start: $("trimStart").value ? +$("trimStart").value : null,
        trim_end: $("trimEnd").value ? +$("trimEnd").value : null,
      };
      const r = await fetch(`/api/jobs/${JID}/edit`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail);
      toast(`剪辑完成：${d.duration_sec}s，可下载`);
      $("btnEditedDl").hidden = false;
      $("btnEditedDl").href = `/api/jobs/${JID}/edited`;
    } catch (e) { toast("剪辑失败: " + e.message); }
    finally { btn.disabled = false; btn.textContent = "生成剪辑版"; }
  });
}
function renderTags(t) {
  const m = { laughter: "笑声", sigh: "叹气", breathing: "呼吸", coughing: "咳嗽", throat_clearing: "清嗓" };
  return t.replace(/&lt;\|(\w+)\|&gt;/g, (_, k) => m[k] ? `<span class="tag">${m[k]}</span>` : "");
}
function escapeHtml(s) { return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
function fmt(sec) { sec = Math.round(sec); return String(Math.floor(sec / 60)).padStart(2, "0") + ":" + String(sec % 60).padStart(2, "0"); }

// ---------- 播放器 ----------
let waveSeed = null;
let waveRatio = 0;
let playerBound = false;
let coverBound = false;

function renderPlayer() {
  const wav = job.paths?.wav, fin = job.paths?.final_mp3, srt = job.paths?.srt;
  $("btnExport").disabled = !(fin || wav);
  $("btnExport").textContent = fin ? "导出 MP3" : "导出 WAV（未混音）";
  $("btnExport").onclick = () => location.href = `/api/jobs/${JID}/export`;
  $("btnSrt").disabled = !srt;
  $("btnSrt").onclick = () => location.href = `/api/jobs/${JID}/srt`;
  $("btnVtt").disabled = !job.paths?.vtt;
  $("btnVtt").onclick = () => location.href = `/api/jobs/${JID}/vtt`;
  $("btnRunQc").hidden = !wav;
  $("btnRunQc").onclick = async () => {
    const b = $("btnRunQc"); b.disabled = true; b.textContent = "质检中…";
    try {
      const r = await fetch(`/api/jobs/${JID}/qc`, { method: "POST" });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail);
      job.qc = d;
      renderTurns();
      toast(d.mode === "unavailable" ? "质检暂不可用：" + (d.note || "") :
        d.pass ? "质检通过" : `质检未过：${d.bad_turns.length} 轮待重做`);
    } catch (e) { toast("质检失败: " + e.message); }
    finally { b.disabled = false; b.textContent = "运行质检"; }
  };
  $("finalBadge").hidden = !fin;
  $("btnPP").hidden = !wav;

  $("npTitle").textContent = job.topic || "未命名节目";
  $("npMeta").textContent = `${job.style || "AI 播客"} · ${job.minutes || "—"} 分钟`;
  const dur = $("durationTxt");
  dur.textContent = fin ? "MP3 成品" : (wav ? "48kHz WAV" : "");
  dur.hidden = !wav;
  $("npSep").hidden = !wav;
  const state = $("playerState");
  state.textContent = fin ? "成品 MP3" : (wav ? "可试听" : "待合成");
  state.classList.toggle("is-ready", !!wav);
  state.classList.toggle("is-idle", !wav);
  $("podPlayer").classList.toggle("is-idle", !wav);
  $("noWav").hidden = !!wav;
  $("noWav").textContent = job.status === "script_ready" ? "生成音频后即可试听" : "等待语音合成";

  const cover = $("npCover"), fallback = $("npCoverFallback");
  if (!coverBound) {
    cover.addEventListener("error", () => { cover.hidden = true; fallback.hidden = false; });
    coverBound = true;
  }
  if (job.paths?.cover) {
    cover.src = `/api/jobs/${JID}/cover`;
    cover.alt = (job.topic || "节目") + " 封面";
    cover.hidden = false; fallback.hidden = true;
  } else {
    cover.hidden = true; fallback.hidden = false;
  }

  const au = $("audio");
  const playBtn = $("playBtn");
  const speedButtons = document.querySelectorAll("#speed [data-v]");
  const available = !!wav;
  playBtn.disabled = !available;
  $("vol").disabled = !available;
  speedButtons.forEach(b => b.disabled = !available);

  if (!playerBound) {
    playerBound = true;
    const setPlaying = on => {
      playBtn.classList.toggle("is-playing", on);
      playBtn.setAttribute("aria-label", on ? "暂停" : "播放");
    };
    au.addEventListener("loadedmetadata", () => {
      $("timeTxt").textContent = "00:00 / " + fmt(au.duration);
    });
    au.addEventListener("timeupdate", () => {
      const ratio = au.duration ? au.currentTime / au.duration : 0;
      drawWave(ratio);
      $("timeTxt").textContent = fmt(au.currentTime) + " / " + fmt(au.duration);
    });
    au.addEventListener("play", () => setPlaying(true));
    au.addEventListener("pause", () => setPlaying(false));
    au.addEventListener("ended", () => setPlaying(false));
    playBtn.addEventListener("click", () => {
      if (playBtn.disabled) return;
      if (au.paused) au.play().catch(() => toast("浏览器暂未允许自动播放，请再点一次"));
      else au.pause();
    });
    speedButtons.forEach(b =>
      b.addEventListener("click", () => {
        au.playbackRate = parseFloat(b.dataset.v);
        speedButtons.forEach(x => x.classList.toggle("on", x === b));
      }));
    $("vol").addEventListener("input", e => au.volume = e.target.value / 100);
    $("wave").addEventListener("click", e => {
      if (!job.paths?.wav) return;
      const auEl = $("audio");
      if (!auEl.duration) return;
      const rect = e.currentTarget.getBoundingClientRect();
      auEl.currentTime = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)) * auEl.duration;
    });
    window.addEventListener("resize", () => drawWave(waveRatio));
    if ("ResizeObserver" in window) {
      new ResizeObserver(() => drawWave(waveRatio)).observe($("wave"));
    }
  }

  if (wav) {
    const src = `/api/jobs/${JID}/audio`;
    if (au.getAttribute("src") !== src) au.src = src;
  }
  if (!available) $("timeTxt").textContent = "00:00 / 00:00";
  requestAnimationFrame(() => drawWave(waveRatio));
}

function drawWave(ratio) {
  ratio = Math.max(0, Math.min(1, ratio || 0));
  waveRatio = ratio;
  const c = $("wave");
  if (!c) return;
  const width = Math.max(1, c.clientWidth);
  const height = c.clientHeight || 82;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  if (c.width !== Math.round(width * dpr) || c.height !== Math.round(height * dpr)) {
    c.width = Math.round(width * dpr);
    c.height = Math.round(height * dpr);
  }
  const ctx = c.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  if (waveSeed === null) {
    let hash = 0;
    for (const ch of JID) hash = (hash * 33 + ch.charCodeAt(0)) >>> 0;
    waveSeed = (hash % 2147483646) + 1;
  }
  let s = waveSeed; const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
  const mid = height / 2;
  if (ratio > 0) {
    ctx.fillStyle = "rgba(249,115,22,.045)";
    ctx.fillRect(0, 0, width * ratio, height);
  }
  const bw = 3.2, gap = 4.8, step = bw + gap;
  const idle = ctx.createLinearGradient(0, 0, 0, height);
  idle.addColorStop(0, "#e9cbb2"); idle.addColorStop(.5, "#d29d75"); idle.addColorStop(1, "#e9cbb2");
  const played = ctx.createLinearGradient(0, 0, 0, height);
  played.addColorStop(0, "#fbbf7a"); played.addColorStop(.5, "#f97316"); played.addColorStop(1, "#ea580c");
  for (let x = 1; x + bw <= width; x += step) {
    const h = Math.max(7, (0.22 + rnd() * 0.78) * height * 0.86);
    const on = ratio > 0 && (x / width) <= ratio;
    ctx.fillStyle = on ? played : idle;
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(x, mid - h / 2, bw, h, bw / 2);
    else ctx.rect(x, mid - h / 2, bw, h);
    ctx.fill();
  }
  if (ratio > 0) {
    const x = Math.round(width * ratio) - 1;
    ctx.fillStyle = "#ea580c";
    ctx.fillRect(x, 1, 2, height - 2);
    ctx.beginPath();
    ctx.arc(x + 1, height - 7, 2.6, 0, Math.PI * 2);
    ctx.fill();
  }
}

// ---------- SSE 实时刷新 ----------
function subscribe() {
  if (["done", "failed", "script_ready"].includes(job.status)) return;
  const es = new EventSource(`/api/jobs/${JID}/stream`);
  es.onmessage = async (e) => {
    const d = JSON.parse(e.data);
    if (d.status === "done" || d.status === "script_ready") { es.close(); location.reload(); return; }
    await load(); renderStepper(); renderTurns(); renderPlayer();
  };
  es.onerror = () => es.close();
}

// ---------- 音效/转场（P2-6） ----------
let sfxAnchors = [];
function setupSfx() {
  syncSfxControls();
  $("sfxRatio").addEventListener("input", e =>
    $("sfxRatioVal").textContent = e.target.value + "%");
  $("btnAddSfx").addEventListener("click", async () => {
    const r = await fetch(`/api/jobs/${JID}/sfx`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ turn: +$("sfxTurn").value,
                             ratio: +$("sfxRatio").value / 100,
                             name: $("sfxName").value,
                             gain_db: +$("sfxGain").value }) });
    const d = await r.json();
    if (!r.ok) return toast("添加失败: " + d.detail);
    applySfxData(d); toast("锚点已添加");
  });
  $("btnApplySfx").addEventListener("click", async () => {
    const btn = $("btnApplySfx"); btn.disabled = true; btn.textContent = "混入中…";
    const r = await fetch(`/api/jobs/${JID}/apply-sfx`, { method: "POST" });
    const d = await r.json();
    btn.disabled = false; btn.textContent = "应用音效";
    if (!r.ok) return toast("应用失败: " + d.detail);
    toast(`已混入 ${d.count} 个音效（${d.duration_sec}s）`);
    $("btnSfxDl").hidden = false;
    $("btnSfxDl").href = `/api/jobs/${JID}/sfx-audio`;
  });
  loadSfx();
}

async function loadSfx() {
  try {
    const d = await (await fetch(`/api/jobs/${JID}/sfx`)).json();
    applySfxData(d);
  } catch {}
}

function applySfxData(d) {
  sfxAnchors = d.anchors || [];
  const resolved = d.resolved || [];
  $("btnApplySfx").hidden = !sfxAnchors.length;
  $("sfxList").innerHTML = sfxAnchors.length
    ? sfxAnchors.map((a, i) => {
        const abs = resolved[i]?.abs_time;
        return `<div class="sfxrow">
          <span class="sfxdot"></span>
          <span class="mono">第 ${a.turn + 1} 轮 · ${Math.round(a.ratio * 100)}% · ${a.name} · ${a.gain_db}dB${abs !== undefined ? ` · ≈${fmt(abs)}` : ""}</span>
          <button class="del-sfx" data-i="${i}" title="删除">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/></svg>
          </button></div>`;
      }).join("")
    : "";
  $("sfxList").querySelectorAll(".del-sfx").forEach(b =>
    b.addEventListener("click", async () => {
      const r = await fetch(`/api/jobs/${JID}/sfx/${b.dataset.i}`, { method: "DELETE" });
      const d = await r.json();
      if (r.ok) { applySfxData(d); toast("已删除"); }
    }));
  if (job?.paths?.final_sfx) {
    $("btnSfxDl").hidden = false;
    $("btnSfxDl").href = `/api/jobs/${JID}/sfx-audio`;
  }
}

// ---------- Show Notes（要点摘要+时间轴） ----------
function renderSnMd(md) {
  // 轻量渲染：标题 / 引言 / 加粗（内容来自自家模板与 LLM，先转义再替换）
  const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return md.split("\n").map(raw => {
    const l = esc(raw);
    if (l.startsWith("# ")) return `<h4 style="margin:0 0 8px;font-size:17px">${l.slice(2)}</h4>`;
    if (l.startsWith("> ")) return `<div style="color:var(--txt2);font-size:13px;margin-bottom:10px">${l.slice(2)}</div>`;
    if (l.startsWith("## ")) return `<div style="font-weight:700;margin:14px 0 6px;font-size:14px">${l.slice(3)}</div>`;
    const rich = l.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
    if (rich.startsWith("- ")) return `<div style="margin:3px 0">${rich.slice(2)}</div>`;
    return rich.trim() ? `<div style="margin:3px 0">${rich}</div>` : "<div style='height:6px'></div>";
  }).join("");
}

async function setupShowNotes() {
  const card = $("snCard"), body = $("snBody"), dl = $("btnSnDl"), regen = $("btnSnRegen");
  if (!card) return;
  const has = !!(job.paths || {}).show_notes;
  if (has) {
    try {
      const r = await fetch(`/api/jobs/${JID}/shownotes`);
      if (r.ok) body.innerHTML = renderSnMd(await r.text());
      dl.hidden = false;
      dl.href = `/api/jobs/${JID}/shownotes?dl=1`;
    } catch (e) { /* 预览失败仍可下载 */ }
    regen.textContent = "重新生成";
    regen.hidden = false;
    card.hidden = false;
  } else if (job.status === "done") {
    // 存量 done 任务：给一键补生成入口
    body.textContent = "尚未生成 Show Notes（要点摘要 + 时间轴）。";
    regen.textContent = "生成 Show Notes";
    regen.hidden = false;
    card.hidden = false;
  } else return;
  regen.onclick = async () => {
    regen.disabled = true; regen.textContent = "生成中…";
    try {
      const r = await fetch(`/api/jobs/${JID}/shownotes`, { method: "POST" });
      if (!r.ok) throw new Error((await r.json()).detail || "生成失败");
      const rr = await fetch(`/api/jobs/${JID}/shownotes`);
      if (rr.ok) body.innerHTML = renderSnMd(await rr.text());
      dl.hidden = false; dl.href = `/api/jobs/${JID}/shownotes?dl=1`;
      regen.textContent = "重新生成";
      toast("Show Notes 已生成");
    } catch (e) { toast("Show Notes 失败: " + e.message); regen.textContent = "生成 Show Notes"; }
    regen.disabled = false;
  };
}

// ---------- toast ----------
function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(t._h); t._h = setTimeout(() => t.hidden = true, 2600);
  return false;
}

// ---------- P2-4 手动重跑后期 ----------
async function rerunPP() {
  $("btnPP").disabled = true; $("btnPP").textContent = "后期处理中…";
  try {
    const r = await fetch(`/api/jobs/${JID}/postprocess`, { method: "POST" });
    if (!r.ok) throw new Error((await r.json()).detail);
    toast("后期完成：final.mp3 + 字幕已生成");
    setTimeout(() => location.reload(), 800);
  } catch (e) { toast("后期失败: " + e.message); $("btnPP").disabled = false; $("btnPP").textContent = "重跑后期"; }
}

init();
