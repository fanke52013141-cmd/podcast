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
  const hue = i => (i * 47) % 360;
  $("grid").innerHTML = list.length ? list.map((v, i) => {
    const role = defaults.host === v.id ? "主持人 · 跨集一致"
      : defaults.partner === v.id ? "搭档 · 角色绑定" : "";
    const hasAudio = !!v.prompt_audio;
    return `<div class="vcard card">
      <div class="vhead">
        <div class="avatar" style="background:hsl(${hue(i)},45%,28%);border-color:hsl(${hue(i)},70%,55%)">
          <canvas class="miniwave" data-seed="${v.id}" width="72" height="36"></canvas></div>
        <div class="vname"><b>${v.name}</b>
          <span class="small">${v.gender === "female" ? "女声" : "男声"} · ${v.age_range}岁</span></div>
        ${v.license_status === "authorized" ? '<span class="badge done">授权</span>'
          : v.license_status === "generated" ? '<span class="badge script">AI生成</span>'
          : '<span class="badge script">公开</span>'}
      </div>
      <div class="vtags">${(v.style_tags || []).map(t => `<span class="chip on">${t}</span>`).join("")}
        ${hasAudio ? "" : '<span class="chip" title="参考音频未上传">⚠ 无音频</span>'}</div>
      ${role ? `<div class="roleline"><span class="badge done">${role}</span></div>` : ""}
      <div class="vacts">
        <button class="ghost play" data-id="${v.id}">▶ 试听</button>
        <button class="ghost editbtn" data-id="${v.id}">编辑</button>
        <button class="ghost ${defaults.host === v.id ? "amber" : ""}" data-role="host" data-id="${v.id}"
          ${defaults.host === v.id ? "disabled" : ""}>${defaults.host === v.id ? "✓ 主持人" : "设为主持"}</button>
        <button class="ghost ${defaults.partner === v.id ? "amber" : ""}" data-role="partner" data-id="${v.id}"
          ${defaults.partner === v.id ? "disabled" : ""}>${defaults.partner === v.id ? "✓ 搭档" : "设为搭档"}</button>
      </div>
    </div>`;
  }).join("") : `<div class="empty" style="grid-column:1/-1">没有匹配的音色，换个描述试试</div>`;

  document.querySelectorAll(".miniwave").forEach(drawMini);
  document.querySelectorAll(".play").forEach(b => b.addEventListener("click", () => play(b.dataset.id, b)));
  document.querySelectorAll(".editbtn").forEach(b => b.addEventListener("click", () =>
    openEdit(voices.find(v => v.id === b.dataset.id))));
  document.querySelectorAll("[data-role]").forEach(b => b.addEventListener("click", async () => {
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

// ---------- 试听 / 声纹 ----------
function drawMini(c) {
  const ctx = c.getContext("2d");
  let s = [...c.dataset.seed].reduce((a, ch) => a * 31 + ch.charCodeAt(0), 11) | 0;
  const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
  ctx.clearRect(0, 0, 72, 36);
  for (let x = 2; x < 70; x += 5) {
    const h = 4 + rnd() * 28;
    ctx.fillStyle = "#F59E0B";
    ctx.globalAlpha = 0.55 + rnd() * 0.45;
    ctx.fillRect(x, (36 - h) / 2, 3, h);
  }
  ctx.globalAlpha = 1;
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
