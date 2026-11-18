/* 设置页交互 —— 读写 /api/settings + 测试连接/探活 */
const $ = (id) => document.getElementById(id);
const STYLES = ["幽默对谈","漫才","深夜电台","访谈","辩论","实战课堂","故事剧场","纪实探索","热点解读"];
const PARALING_NAME = { laughter: "笑声", sigh: "叹气", breathing: "呼吸", coughing: "咳嗽", throat_clearing: "清嗓" };
let voices = [];

async function init() {
  await loadVoices();
  await loadSettings();
  bind();
  await setupQueue();
  // 左侧导航高亮：点击立即选中；滚动时按分区位置高亮（页面触底时选中最后一项）
  const links = [...document.querySelectorAll(".setnav a")];
  const setActive = (a) => links.forEach(x => x.classList.toggle("on", x === a));
  links.forEach(a => a.addEventListener("click", () => setActive(a)));
  const onScroll = () => {
    const nearBottom = window.innerHeight + window.scrollY
      >= document.documentElement.scrollHeight - 8;
    let cur = nearBottom ? links[links.length - 1] : links[0];
    if (!nearBottom) {
      for (const a of links) {
        const sec = document.querySelector(a.getAttribute("href"));
        if (sec && sec.getBoundingClientRect().top < 140) cur = a;
      }
    }
    setActive(cur);
  };
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();
  document.querySelectorAll("[data-soon]").forEach(a =>
    a.addEventListener("click", e => { e.preventDefault(); toast("该页面在后续迭代中"); }));
}

async function loadVoices() {
  try { voices = await (await fetch("/api/voices")).json(); } catch {}
  const fill = (sel, g) => sel.innerHTML = voices.filter(v => v.gender === g)
    .map(v => `<option value="${v.id}">${v.name} · ${v.style_tags[0] || ""}</option>`).join("");
  fill($("sHost"), "female"); fill($("sPartner"), "male");
  $("sStyle").innerHTML = STYLES.map(s => `<option>${s}</option>`).join("");
}

async function loadSettings() {
  const s = await (await fetch("/api/settings")).json();
  // ① 写稿引擎
  $("sKey").placeholder = s.llm.api_key_masked || "sk-...（未配置）";
  $("keyState").textContent = s.llm.api_key_set ? "已配置 Key" : "未配置 Key";
  $("sModel").value = s.llm.model;
  $("sTemp").value = Math.round((s.llm.temperature || 0.9) * 10);
  $("tempVal").textContent = (s.llm.temperature || 0.9).toFixed(1);
  // ② 语音合成
  $("sTtsUrl").value = s.tts.url;
  $("sModelPath").value = s.tts.model_path;
  $("sSeg").value = s.tts.segment_minutes;
  $("segVal").textContent = `${s.tts.segment_minutes} 分钟/段`;
  $("sVsUrl").value = s.tts.voicestar_url || "";
  $("sVsCmd").value = s.tts.voicestar_cmd || "";
  // API 合成引擎（掩码展示，留空=不修改）
  $("mxState").textContent = s.tts.minimax.key_set ? `已配置 ${s.tts.minimax.key_masked}` : "未配置";
  $("dbState").textContent = s.tts.doubao.key_set ? `已配置 ${s.tts.doubao.key_masked}` : "未配置";
  $("paraling").innerHTML = Object.entries(s.tts.paraling).map(([k, on]) =>
    `<label class="chip${on ? " on" : ""}"><input type="checkbox" data-p="${k}" ${on ? "checked" : ""}>${PARALING_NAME[k]}</label>`).join("");
  $("paraling").querySelectorAll("input").forEach(c =>
    c.addEventListener("change", () => c.closest(".chip").classList.toggle("on", c.checked)));
  // ③ 默认值
  $("sStyle").value = s.defaults.style;
  $("sMinutes").value = s.defaults.minutes;
  $("dminVal").textContent = `${s.defaults.minutes} 分钟`;
  $("sHost").value = s.defaults.host_voice;
  $("sPartner").value = s.defaults.partner_voice;
  // ④ 目录
  $("sJobsDir").value = s.paths.jobs;
  $("sEnvPath").value = s.paths.env;
}

function bind() {
  $("sTemp").addEventListener("input", e => $("tempVal").textContent = (e.target.value / 10).toFixed(1));
  $("sSeg").addEventListener("input", e => $("segVal").textContent = `${e.target.value} 分钟/段`);
  $("sMinutes").addEventListener("input", e => $("dminVal").textContent = `${e.target.value} 分钟`);
  $("btnShowKey").addEventListener("click", () => {
    const k = $("sKey"); k.type = k.type === "password" ? "text" : "password";
    $("btnShowKey").textContent = k.type === "password" ? "显示" : "隐藏";
  });
  $("btnTestLlm").addEventListener("click", async () => {
    const b = $("llmTest"); b.textContent = "测试中…"; b.className = "badge running";
    const r = await (await fetch("/api/settings/test-llm", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: $("sKey").value.trim(),
                             model: $("sModel").value.trim() })
    })).json();
    b.textContent = r.ok ? `连接正常 · ${r.latency_ms}ms` : `${r.error}`.slice(0, 48);
    b.className = "badge " + (r.ok ? "done" : "failed");
    if (r.ok) $("keyState").textContent = "Key 有效";
  });
  $("btnTestTts").addEventListener("click", async () => {
    const b = $("ttsTest"); b.textContent = "探活中…"; b.className = "badge running";
    const r = await (await fetch("/api/settings/test-tts", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: $("sTtsUrl").value.trim() })
    })).json();
    b.textContent = r.ok ? r.detail : `${r.error}`.slice(0, 60);
    b.className = "badge " + (r.ok ? "done" : "failed");
  });
  $("btnTestMx").addEventListener("click", async () => {
    const b = $("mxTest"); b.textContent = "测试中…"; b.className = "badge running";
    const r = await (await fetch("/api/settings/test-minimax", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: $("sMxKey").value.trim() })
    })).json();
    b.textContent = r.ok ? `连接正常 · ${r.latency_ms}ms` : `${r.error}`.slice(0, 48);
    b.className = "badge " + (r.ok ? "done" : "failed");
    if (r.ok) $("mxState").textContent = "Key 有效";
  });
  $("btnTestDb").addEventListener("click", async () => {
    const b = $("dbTest"); b.textContent = "测试中…"; b.className = "badge running";
    const r = await (await fetch("/api/settings/test-doubao", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: $("sDbKey").value.trim() })
    })).json();
    b.textContent = r.ok ? `连接正常 · ${r.latency_ms}ms` : `${r.error}`.slice(0, 48);
    b.className = "badge " + (r.ok ? "done" : "failed");
    if (r.ok) $("dbState").textContent = "Key 有效";
  });
  $("btnSave").addEventListener("click", save);
  $("btnReset").addEventListener("click", async () => {
    await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ DEFAULT_STYLE: "幽默对谈", DEFAULT_MINUTES: "10",
                             DEFAULT_HOST_VOICE: "v01_female_warm", DEFAULT_PARTNER_VOICE: "v02_male_deep",
                             SEGMENT_MINUTES: "7" }) });
    await loadSettings(); toast("已恢复默认值");
  });
  $("btnDiag").addEventListener("click", async () => {
    toast("正在打包诊断信息…");
    try {
      const r = await (await fetch("/api/diag", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({})
      })).json();
      toast(`诊断包已生成：${r.zip.split(/[\\/]/).pop()}（${r.size_kb}KB，脱敏）`);
    } catch (e) { toast("诊断包生成失败: " + e.message); }
  });
  // ⌘S 保存
  document.addEventListener("keydown", e => {
    if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); save(); }
  });
}

async function save() {
  const paraling = [...document.querySelectorAll("#paraling input:checked")]
    .map(c => c.dataset.p).join(",");
  const body = {
    DEEPSEEK_MODEL: $("sModel").value.trim(),
    TTS_URL: $("sTtsUrl").value.trim(),
    SOULX_MODEL_PATH: $("sModelPath").value.trim(),
    SEGMENT_MINUTES: $("sSeg").value,
    VOICESTAR_URL: $("sVsUrl").value.trim(),
    VOICESTAR_CMD: $("sVsCmd").value.trim(),
    PARALING_TAGS: paraling,
    DEFAULT_STYLE: $("sStyle").value,
    DEFAULT_MINUTES: $("sMinutes").value,
    DEFAULT_HOST_VOICE: $("sHost").value,
    DEFAULT_PARTNER_VOICE: $("sPartner").value,
  };
  const key = $("sKey").value.trim();
  if (key && !key.includes("•")) body.DEEPSEEK_API_KEY = key;   // 留空/掩码 = 不修改
  const mxKey = $("sMxKey").value.trim();
  if (mxKey && !mxKey.includes("•")) body.MINIMAX_API_KEY = mxKey;
  const dbKey = $("sDbKey").value.trim();
  if (dbKey && !dbKey.includes("•")) body.DOUBAO_API_KEY = dbKey;
  const r = await (await fetch("/api/settings", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  })).json();
  toast(`已保存 ${r.saved} 项配置（即时生效）`);
  await loadSettings();                                          // 重新加载掩码
  $("sKey").value = "";
  $("sMxKey").value = "";
  $("sDbKey").value = "";                                          // 清空明文输入
}

// ---------- 夜间批量队列（P3-5） ----------
let qTemplates = [];
async function setupQueue() {
  try { qTemplates = await (await fetch("/api/templates")).json(); } catch {}
  $("qTpl").innerHTML = `<option value="">（不使用模板）</option>` +
    qTemplates.map(t => `<option value="${t.id}">${t.name}</option>`).join("");
  $("qMin").addEventListener("input", e => $("qMinVal").textContent = e.target.value + " 分钟");
  $("btnAddSchedule").addEventListener("click", async () => {
    const tpl = qTemplates.find(t => t.id === $("qTpl").value);
    const topics = $("qTopics").value.split(/\n/).map(s => s.trim()).filter(Boolean);
    if (!topics.length) return toast("请至少填写一个主题");
    const body = {
      time: $("qTime").value,
      topics,
      minutes: tpl ? tpl.minutes : +$("qMin").value,
      style: tpl ? tpl.style : "幽默对谈",
      host_voice: tpl ? tpl.host_voice : ($("sHost").value || "v01_female_warm"),
      partner_voice: tpl ? tpl.partner_voice : ($("sPartner").value || "v02_male_deep"),
      bgm: tpl ? tpl.bgm : "",
    };
    const r = await fetch("/api/schedules", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) });
    const d = await r.json();
    if (!r.ok) return toast("添加失败: " + d.detail);
    toast(`队列已添加：每天 ${d.time} · ${d.topics.length} 个主题`);
    await loadQueue();
  });
  await loadQueue();
}

async function loadQueue() {
  const list = await (await fetch("/api/schedules")).json().catch(() => []);
  const el = $("qList");
  if (!list.length) { el.innerHTML = `<span class="small" style="color:var(--txt2)">还没有定时队列</span>`; return; }
  el.innerHTML = list.map(s => `
    <div class="task" style="cursor:default">
      <div class="info">
        <div class="title mono">每天 ${s.time} · ${s.style} · ${s.minutes} 分钟</div>
        <div class="qtopics">${s.topics.map(escapeHtml).join(" / ")}${s.last_run ? `<br>上次执行：${s.last_run}` : ""}</div>
      </div>
      <button class="ghost runnow" data-id="${s.id}">立即执行</button>
      <button class="ghost delq" data-id="${s.id}" style="color:var(--red)">删除</button>
    </div>`).join("");
  el.querySelectorAll(".runnow").forEach(b => b.addEventListener("click", async () => {
    b.disabled = true; b.textContent = "创建中…";
    const r = await (await fetch(`/api/schedules/${b.dataset.id}/run-now`, { method: "POST" })).json();
    toast(`已创建 ${r.count} 个任务（排队生成中）`);
    b.disabled = false; b.textContent = "立即执行";
    await loadQueue();
  }));
  el.querySelectorAll(".delq").forEach(b => b.addEventListener("click", async () => {
    if (!confirm("删除该定时队列？")) return;
    await fetch(`/api/schedules/${b.dataset.id}`, { method: "DELETE" });
    toast("已删除"); await loadQueue();
  }));
}

function escapeHtml(s) {
  return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(t._h); t._h = setTimeout(() => t.hidden = true, 2600);
}

init();
