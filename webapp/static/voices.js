/* 音色库页 —— 检索/试听/角色绑定/新建/编辑/删除/音频上传（P3-4） */
const $ = (id) => document.getElementById(id);
let voices = [], defaults = { host: "", partner: "" };
let cur = null, editing = null;                 // editing: null=新建, 否则 voice 对象

async function init() {
  await refresh();
  $("q").addEventListener("input", render);
  $("btnDesign").addEventListener("click", () =>
    toast("AI 音色设计将在 P3-2 接入 MOSS-VoiceGenerator 后开放"));
  $("btnNewVoice").addEventListener("click", () => openEdit(null));
  $("dgSave").addEventListener("click", saveVoice);
  $("dgDelete").addEventListener("click", deleteVoice);
  $("dgUpload").addEventListener("click", uploadAudio);
}

async function refresh() {
  voices = await (await fetch("/api/voices")).json();
  try {
    const s = await (await fetch("/api/settings")).json();
    defaults = { host: s.defaults.host_voice, partner: s.defaults.partner_voice };
  } catch {}
  render();
}

function render() {
  const q = $("q").value.trim().toLowerCase();
  const hit = v => !q ||
    [v.name, v.gender === "female" ? "女" : "男", v.persona_name, ...(v.style_tags || [])]
      .join(" ").toLowerCase().includes(q);
  const list = voices.filter(hit);
  $("vCount").textContent = list.length;
  const HUES = [28, 205, 160, 350, 262, 96, 18, 232];   // 精选柔和色板
  $("grid").innerHTML = list.length ? list.map((v, i) => {
    const h = HUES[i % HUES.length];
    const isHost = defaults.host === v.id, isPartner = defaults.partner === v.id;
    return `<div class="vcard">
      <div class="vtop">
        <div class="vtile" style="--tb:#F0F0F0;--tb2:#E5E5E5;--bar:#525252">
          <canvas class="miniwave" data-seed="${v.id}" width="88" height="40"></canvas>
        </div>
        <div class="vmeta">
          <div class="vname">${v.name}</div>
          <div class="vsub">${v.gender === "female" ? "女声" : "男声"} · ${v.age_range}岁</div>
        </div>
      </div>
      <div class="vtags">${(v.style_tags || []).map(t => `<span class="vtag">${t}</span>`).join("")}
        ${v.prompt_audio ? "" : '<span class="vtag warn">待录参考音频</span>'}</div>
      <div class="vwave"><canvas class="fullwave" data-seed="${v.id}w" width="320" height="26"></canvas></div>
      <div class="vacts">
        <button class="vplay" data-id="${v.id}">▶ 试听</button>
        <button class="vedit" data-id="${v.id}">编辑</button>
        <div class="seg">
          <button class="segb ${isHost ? "on" : ""}" data-role="host" data-id="${v.id}">主持人</button>
          <button class="segb ${isPartner ? "on" : ""}" data-role="partner" data-id="${v.id}">搭档</button>
        </div>
      </div>
      ${(isHost || isPartner) ? `<div class="vbind"><span class="vdot"></span>${isHost ? "主持人" : "搭档"} · 跨集一致</div>` : ""}
    </div>`;
  }).join("") : `<div class="empty" style="grid-column:1/-1">没有匹配的音色，换个描述试试</div>`;

  document.querySelectorAll(".miniwave").forEach(c => drawMini(c, true));
  document.querySelectorAll(".fullwave").forEach(c => drawMini(c, false));
  document.querySelectorAll(".vplay").forEach(b => b.addEventListener("click", () => play(b.dataset.id, b)));
  document.querySelectorAll(".vedit").forEach(b => b.addEventListener("click", () =>
    openEdit(voices.find(v => v.id === b.dataset.id))));
  document.querySelectorAll(".segb").forEach(b => b.addEventListener("click", async () => {
    if (b.classList.contains("on")) return;
    const r = await (await fetch(`/api/voices/${b.dataset.id}/set-role`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role: b.dataset.role })
    })).json();
    if (r.ok) {
      defaults[b.dataset.role === "host" ? "host" : "partner"] = b.dataset.id;
      toast(b.dataset.role === "host" ? "已设为默认主持人（即时生效）" : "已设为默认搭档（即时生效）");
      render();
    }
  }));
}

// ---------- 编辑 / 新建（P3-4） ----------
function openEdit(v) {
  editing = v;
  $("dgTitle").textContent = v ? `编辑音色 · ${v.name}` : "新建音色";
  $("dgId").value = v ? v.id : "（保存后自动生成）";
  $("dgName").value = v ? v.name : "";
  $("dgGender").value = v ? v.gender : "female";
  $("dgAge").value = v ? v.age_range : "26-32";
  $("dgSpeed").value = v ? v.speed_feel : "normal";
  $("dgTags").value = v ? (v.style_tags || []).join(", ") : "";
  $("dgPersona").value = v ? (v.persona_name || "") : "";
  $("dgLicense").value = v ? (v.license_status || "authorized") : "authorized";
  $("dgSource").value = v ? (v.source || "self_recorded") : "self_recorded";
  $("dgPromptText").value = v ? (v.prompt_text || "") : "";
  $("dgDelete").hidden = !v;
  $("dgUpload").disabled = !v;                 // 新建保存后才能上传音频
  $("dgAudio").disabled = !v;
  $("editDlg").showModal();
}

function formBody() {
  return {
    name: $("dgName").value.trim(),
    gender: $("dgGender").value,
    age_range: $("dgAge").value.trim(),
    speed_feel: $("dgSpeed").value,
    style_tags: $("dgTags").value.split(/[,，]/).map(s => s.trim()).filter(Boolean),
    persona_name: $("dgPersona").value.trim(),
    license_status: $("dgLicense").value,
    source: $("dgSource").value,
    prompt_text: $("dgPromptText").value.trim(),
    aliases: [],
  };
}

async function saveVoice() {
  const body = formBody();
  if (!body.name) return toast("请填写名称");
  const url = editing ? `/api/voices/${editing.id}` : "/api/voices";
  const r = await fetch(url, { method: editing ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const d = await r.json();
  if (!r.ok) return toast("保存失败: " + d.detail);
  toast(editing ? "已保存（即时生效）" : `已创建：${d.id}`);
  $("editDlg").close();
  await refresh();
}

async function deleteVoice() {
  if (!editing) return;
  if (!confirm(`确定删除音色「${editing.name}」？参考音频文件也会删除，不可恢复。`)) return;
  const r = await fetch(`/api/voices/${editing.id}`, { method: "DELETE" });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) return toast("删除失败: " + (d.detail || r.status));
  toast("已删除");
  $("editDlg").close();
  await refresh();
}

async function uploadAudio() {
  if (!editing) return;
  const f = $("dgAudio").files[0];
  if (!f) return toast("请先选择音频文件");
  const fd = new FormData();
  fd.append("file", f);
  const btn = $("dgUpload"); btn.disabled = true; btn.textContent = "上传中…";
  const r = await fetch(`/api/voices/${editing.id}/audio`, { method: "POST", body: fd });
  const d = await r.json().catch(() => ({}));
  btn.disabled = false; btn.textContent = "上传";
  if (!r.ok) return toast("上传失败: " + (d.detail || r.status));
  toast("参考音频已上传");
  editing.prompt_audio = d.path;
  await refresh();
}

// ---------- 试听 / 声纹（瓷砖 + 细波，柔和陶瓷色） ----------
function drawMini(c, tile) {
  const ctx = c.getContext("2d");
  let s = [...c.dataset.seed].reduce((a, ch) => a * 31 + ch.charCodeAt(0), 11) | 0;
  const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
  const W = c.width, H = c.height;
  ctx.clearRect(0, 0, W, H);
  const barColor = tile
    ? (getComputedStyle(c.parentElement).getPropertyValue("--bar") || "#525252").trim()
    : "rgba(23,23,23,.25)";
  const n = tile ? 9 : 48, bw = tile ? 5 : 3, gap = tile ? 4.2 : 2.4;
  const total = n * bw + (n - 1) * gap;
  let x = (W - total) / 2;
  for (let i = 0; i < n; i++) {
    const h = Math.max(tile ? 8 : 4, rnd() * (H - (tile ? 10 : 6)));
    ctx.fillStyle = barColor;
    ctx.globalAlpha = tile ? 0.9 : 0.35 + rnd() * 0.5;
    roundBar(ctx, x, (H - h) / 2, bw, h, bw / 2);
    x += bw + gap;
  }
  ctx.globalAlpha = 1;
}
function roundBar(ctx, x, y, w, h, r) {
  if (ctx.roundRect) { ctx.beginPath(); ctx.roundRect(x, y, w, h, r); ctx.fill(); }
  else ctx.fillRect(x, y, w, h);
}

async function play(id, btn) {
  const au = $("player");
  if (cur === id && !au.paused) { au.pause(); btn.textContent = "▶ 试听"; return; }
  cur = id;
  au.src = `/api/voices/${id}/audio`;
  try {
    await au.play();
    btn.textContent = "⏸ 播放中";
    au.onended = () => { btn.textContent = "▶ 试听"; };
    au.onerror = () => { btn.textContent = "▶ 试听"; toast("该音色的参考音频尚未录制（P1-8）"); };
  } catch { toast("该音色的参考音频尚未录制（P1-8）"); }
}

function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(t._h); t._h = setTimeout(() => t.hidden = true, 2600);
}

init();
