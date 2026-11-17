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
  renderStepper(); renderTurns(); renderPlayer(); setupEdit(); setupSfx();
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
  if (job.error) { const e = $("valErr"); e.hidden = false; e.textContent = "❌ " + job.error; }
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
function renderTurns() {
  const turns = job.script?.text || [];
  if (!turns.length) return;
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
  if (job.qc) {
    const q = job.qc;
    $("qcSum").innerHTML = q.mode === "unavailable"
      ? `质检：暂不可用（${escapeHtml(q.note || "") }）`
      : q.pass
        ? `质检通过 · 平均 CER ${(q.cer_avg * 100).toFixed(2)}%（${q.mode}）`
        : `<span style="color:var(--red)">质检未过 · ${bad.size} 轮待重做 · 平均 CER ${(q.cer_avg * 100).toFixed(2)}%</span>`;
  }
  // 剪辑面板：轮次下拉
  $("gainTurn").innerHTML = turns.map(([sid], i) =>
    `<option value="${i}">第 ${i + 1} 轮 · ${sid}</option>`).join("");
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
  if (wav) {
    const au = $("audio"); au.src = `/api/jobs/${JID}/audio`;
    au.addEventListener("timeupdate", () => {
      const r = au.duration ? au.currentTime / au.duration : 0;
      drawWave(r); $("timeTxt").textContent = fmt(au.currentTime) + " / " + fmt(au.duration);
    });
    au.addEventListener("play", () => $("playBtn").textContent = "⏸");
    au.addEventListener("pause", () => $("playBtn").textContent = "▶");
    $("playBtn").addEventListener("click", () => au.paused ? au.play() : au.pause());
    $("speed").addEventListener("change", e => au.playbackRate = parseFloat(e.target.value));
    $("vol").addEventListener("input", e => au.volume = e.target.value / 100);
    $("noWav").hidden = true;
    $("durationTxt").textContent = "48kHz WAV";
  } else {
    $("durationTxt").textContent = "—";
  }
  drawWave(0);
  $("wave").addEventListener("click", e => {
    if (!job.paths?.wav) return;
    const au = $("audio"); if (!au.duration) return;
    const rect = e.target.getBoundingClientRect();
    au.currentTime = (e.clientX - rect.left) / rect.width * au.duration;
  });
  window.addEventListener("resize", () => drawWave(0));
}
function drawWave(ratio) {
  const c = $("wave"); c.width = c.clientWidth * 2;      // 2x 清晰度
  const ctx = c.getContext("2d"), W = c.width, H = c.height, mid = H / 2;
  if (waveSeed === null) waveSeed = [...JID].reduce((a, ch) => a * 31 + ch.charCodeAt(0), 7) | 0;
  let s = waveSeed; const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
  ctx.clearRect(0, 0, W, H);
  const bw = 6, gap = 3;
  for (let x = 0; x + bw <= W; x += bw + gap) {
    const h = Math.max(8, (0.25 + rnd() * 0.75) * H * 0.9);
    ctx.fillStyle = (x / W) <= ratio ? "#F59E0B" : "#4B5563";
    ctx.fillRect(x, mid - h / 2, bw, h);
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
  const turns = job.script?.text || [];
  $("sfxTurn").innerHTML = turns.map(([sid], i) =>
    `<option value="${i}">第 ${i + 1} 轮 · ${sid}</option>`).join("");
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
        return `<div>· 第 ${a.turn + 1} 轮 ${Math.round(a.ratio * 100)}% · ${a.name} ` +
          `${a.gain_db}dB${abs !== undefined ? ` ≈ ${fmt(abs)}` : ""} ` +
          `<button class="ghost del-sfx" data-i="${i}" style="padding:1px 8px;font-size:11px">✕</button></div>`;
      }).join("")
    : `<span style="color:var(--txt2)">还没有锚点。选择轮次与句内位置后点「添加锚点」</span>`;
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
