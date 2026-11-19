/* 设置页交互 —— 读写 /api/settings + 测试连接/探活 + 风格/Skill 管理 */
const $ = (id) => document.getElementById(id);
const PARALING_NAME = { laughter: "笑声", sigh: "叹气", breathing: "呼吸", coughing: "咳嗽", throat_clearing: "清嗓" };
let voices = [];
let styleCache = [], editingStyleId = null;

async function init() {
  await loadVoices();
  await loadStyles();
  await loadSettings();
  bind();
  await loadSkills();
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
}

async function loadStyles() {
  try {
    const data = await (await fetch("/api/styles")).json();
    styleCache = data.styles || [];
  } catch { styleCache = []; }
  $("sStyle").innerHTML = styleCache.map(s => `<option value="${escapeHtml(s.name)}">${escapeHtml(s.name)}</option>`).join("");
  renderStyleLibrary();
}

function renderStyleLibrary() {
  const el = $("styleLibrary");
  if (!el) return;
  if (!styleCache.length) {
    el.innerHTML = `<div class="empty">暂无可用风格</div>`;
    return;
  }
  el.innerHTML = styleCache.map(s => `
    <div class="style-item">
      <div class="style-item-main">
        <div class="style-item-title">
          <span class="style-item-name">${escapeHtml(s.name)}</span>
          <span class="style-item-badge${s.builtin ? " builtin" : ""}">${s.builtin ? "内置" : "自定义"}</span>
        </div>
        <div class="style-item-desc">${escapeHtml(s.desc)}</div>
      </div>
      ${s.builtin ? "" : `<div class="style-item-actions">
        <button class="ghost" data-style-edit="${s.id}">编辑</button>
        <button class="ghost danger" data-style-del="${s.id}">删除</button>
      </div>`}
    </div>`).join("");
  el.querySelectorAll("[data-style-edit]").forEach(btn =>
    btn.addEventListener("click", () => openStyleDialog(styleCache.find(s => s.id === btn.dataset.styleEdit))));
  el.querySelectorAll("[data-style-del]").forEach(btn =>
    btn.addEventListener("click", () => deleteStyle(btn.dataset.styleDel)));
}

function openStyleDialog(style) {
  editingStyleId = style ? style.id : null;
  $("styleDlgTitle").textContent = style ? `编辑风格 · ${style.name}` : "新建风格";
  $("styleName").value = style ? style.name : "";
  $("styleDesc").value = style ? style.desc : "";
  $("styleInterrupt").value = style ? style.n_interrupt : 4;
  $("styleTemplate").value = style ? (style.prompt_template || "") : "";
  $("styleError").hidden = true;
  $("styleDlg").showModal();
  setTimeout(() => $("styleName").focus(), 60);
}

async function saveStyle() {
  const body = { name: $("styleName").value.trim(), desc: $("styleDesc").value.trim(),
                 n_interrupt: +$("styleInterrupt").value || 4,
                 prompt_template: $("styleTemplate").value.trim() };
  const error = $("styleError");
  if (!body.name) { error.textContent = "请填写风格名称"; error.hidden = false; return; }
  const url = editingStyleId ? `/api/styles/${editingStyleId}` : "/api/styles";
  const r = await fetch(url, { method: editingStyleId ? "PATCH" : "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { error.textContent = data.detail || "保存失败"; error.hidden = false; return; }
  $("styleDlg").close();
  toast(editingStyleId ? "风格已更新" : "风格已创建");
  await loadStyles();
  await loadSettings();
}

async function deleteStyle(id) {
  const style = styleCache.find(s => s.id === id);
  if (!style || !confirm(`删除风格「${style.name}」？`)) return;
  const r = await fetch(`/api/styles/${id}`, { method: "DELETE" });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) return toast(data.detail || "删除失败");
  toast("风格已删除");
  await loadStyles();
  await loadSettings();
}

async function loadSkills() {
  let data = { builtin: [], custom: [] };
  try { data = await (await fetch("/api/agent-skills")).json(); } catch {}
  $("skillBuiltin").innerHTML = (data.builtin || []).map(s => `
    <div class="skill-chip" title="${escapeHtml(s.desc)}">
      <span class="skill-chip-dot"></span>
      <span>${escapeHtml(s.label)}</span>
      <small>${escapeHtml(s.name)}</small>
    </div>`).join("") || `<span class="small">内置工具未加载</span>`;
  const custom = data.custom || [];
  $("skillCustom").innerHTML = custom.length ? custom.map(s => `
    <div class="skill-row">
      <div class="skill-row-icon">MD</div>
      <div class="skill-row-main">
        <div class="skill-row-name">${escapeHtml(s.name)}</div>
        <div class="skill-row-meta">${s.size < 1024 ? `${s.size || 0} B` : `${((s.size || 0) / 1024).toFixed(1)} KB`} · ${escapeHtml((s.created_at || "").replace("T", " ").replace(/-/g, "."))}</div>
      </div>
      <button class="ghost danger skill-del" data-skill-id="${escapeHtml(s.id)}">移除</button>
    </div>`).join("") : `<div class="skill-empty">还没有上传技能手册。支持 .md / .txt，内容会注入当前账号的写稿 Agent。</div>`;
  $("skillCustom").querySelectorAll(".skill-del").forEach(btn =>
    btn.addEventListener("click", async () => {
      if (!confirm(`移除技能手册「${btn.closest(".skill-row").querySelector(".skill-row-name").textContent}」？`)) return;
      const r = await fetch(`/api/agent-skills/${encodeURIComponent(btn.dataset.skillId)}`, { method: "DELETE" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) return toast(d.detail || "移除失败");
      toast("技能手册已移除");
      loadSkills();
    }));
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
  // 账号池 + 写稿提示词
  renderAccounts();
  $("pSystem").value = s.prompts.system_editor || "";
  $("pStepA").value = s.prompts.step_a || "";
  $("pStepB").value = s.prompts.step_b || "";
  $("paraling").innerHTML = Object.entries(s.tts.paraling).map(([k, on]) =>
    `<label class="chip${on ? " on" : ""}"><input type="checkbox" data-p="${k}" ${on ? "checked" : ""}>${PARALING_NAME[k]}</label>`).join("");
  $("paraling").querySelectorAll("input").forEach(c =>
    c.addEventListener("change", () => c.closest(".chip").classList.toggle("on", c.checked)));
  // ③ 默认值
  if (styleCache.some(x => x.name === s.defaults.style)) $("sStyle").value = s.defaults.style;
  else if (styleCache.length) $("sStyle").selectedIndex = 0;
  $("sMinutes").value = s.defaults.minutes;
  $("dminVal").textContent = `${s.defaults.minutes} 分钟`;
  $("sHost").value = s.defaults.host_voice;
  $("sPartner").value = s.defaults.partner_voice;
  // ④ 目录
  $("sJobsDir").value = s.paths.jobs;
  $("sEnvPath").value = s.paths.env;
}

function bind() {
  $("btnNewStyle").addEventListener("click", () => openStyleDialog(null));
  $("styleClose").addEventListener("click", () => $("styleDlg").close());
  $("styleDlg").addEventListener("click", e => {
    if (e.target === $("styleDlg")) $("styleDlg").close();
  });
  $("styleSave").addEventListener("click", saveStyle);
  $("btnUploadSkill").addEventListener("click", () => $("skillFile").click());
  $("skillFile").addEventListener("change", async e => {
    const file = e.target.files[0];
    if (!file) return;
    const button = $("btnUploadSkill");
    button.disabled = true; button.textContent = "上传中…";
    try {
      const form = new FormData();
      form.append("file", file);
      const r = await fetch("/api/agent-skills", { method: "POST", body: form });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) toast(data.detail || "上传失败");
      else { toast(`技能「${data.name}」已上传`); await loadSkills(); }
    } finally {
      button.disabled = false; button.textContent = "上传 Skill 手册";
      e.target.value = "";
    }
  });
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
  // 账号池走独立 /api/llm-accounts 接口（不再经 settings 保存）
  // 提示词：留空=恢复内置（存空串即视为未覆盖）
  body.PROMPT_SYSTEM_EDITOR = $("pSystem").value;
  body.PROMPT_STEP_A = $("pStepA").value;
  body.PROMPT_STEP_B = $("pStepB").value;
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


// ---------- LLM 账号池（借鉴 PPT_presentation_video：可编辑/切换/至少留一个） ----------
let accCache = [], accActiveId = "", accEditing = null;

async function renderAccounts() {
  try {
    const d = await (await fetch("/api/llm-accounts")).json();
    accCache = d.accounts; accActiveId = d.active_id;
  } catch { return; }
  $("accList").innerHTML = accCache.map(a => `
    <div style="display:flex;align-items:center;gap:12px;padding:10px 14px;
      border:1px solid var(--line);border-radius:10px;background:${a.id === accActiveId ? "var(--surface2)" : "#fff"}">
      <span class="mono" style="font-weight:600">${a.name}</span>
      <span class="mono small">${a.model || ""}</span>
      <span class="small mono">${a.base_url}</span>
      ${a.id === accActiveId
        ? '<span class="badge done" style="margin-left:auto">当前使用</span>'
        : `<button class="ghost" style="margin-left:auto" data-act="select" data-id="${a.id}">切换到此账号</button>`}
      <button class="ghost" data-act="edit" data-id="${a.id}">编辑</button>
      ${accCache.length > 1 ? `<button class="ghost" data-act="del" data-id="${a.id}" style="color:var(--red)">删除</button>` : ""}
    </div>`).join("");
  $("accList").querySelectorAll("[data-act]").forEach(b =>
    b.addEventListener("click", () => accAction(b.dataset.act, b.dataset.id)));
}

async function accAction(act, id) {
  if (act === "select") {
    await fetch(`/api/llm-accounts/${id}/select`, { method: "POST" });
    toast("已切换账号（即时生效）"); renderAccounts();
  } else if (act === "del") {
    if (!confirm("删除这个账号？")) return;
    const r = await fetch(`/api/llm-accounts/${id}`, { method: "DELETE" });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) return toast(d.detail || "删除失败");
    toast("已删除"); renderAccounts();
  } else if (act === "edit") {
    const a = accCache.find(x => x.id === id);
    accEditing = id;
    $("accDlgTitle").textContent = `编辑账号 · ${a.name}`;
    $("accName").value = a.name; $("accModel").value = a.model || "";
    $("accUrl").value = a.base_url || ""; $("accKey").value = "";   // 掩码：留空不改
    $("accDlg").showModal();
  }
}

$("btnAddAcc").addEventListener("click", () => {
  accEditing = null;
  $("accDlgTitle").textContent = "添加账号";
  $("accName").value = ""; $("accModel").value = "";
  $("accUrl").value = ""; $("accKey").value = "";
  $("accDlg").showModal();
});
$("accSave").addEventListener("click", async () => {
  const body = { name: $("accName").value.trim(),
                 base_url: $("accUrl").value.trim(),
                 api_key: $("accKey").value.trim(),
                 model: $("accModel").value.trim() };
  if (!body.name) return toast("请填账号名称");
  const r = await fetch(accEditing ? `/api/llm-accounts/${accEditing}` : "/api/llm-accounts",
    { method: accEditing ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) return toast(d.detail || "保存失败");
  $("accDlg").close();
  toast(accEditing ? "账号已更新（若为激活账号即时生效）" : "账号已添加");
  renderAccounts();
});
