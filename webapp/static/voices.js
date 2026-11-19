/* 音色库页 —— 检索/试听/角色绑定/新建/编辑/删除/音频上传（P3-4） */
const $ = (id) => document.getElementById(id);
let voices = [], defaults = { host: "", partner: "" };
let cur = null, editing = null;                 // editing: null=新建, 否则 voice 对象

async function init() {
  await refresh();
  $("q").addEventListener("input", render);
  $("btnDesign").addEventListener("click", () => {
    $("dgPrompt").value = ""; $("dgDName").value = ""; $("dgDGender").value = "female";
    $("dgDesignGo").textContent = "开始设计"; $("dgDesignGo").disabled = false;
    $("designDlg").showModal();
  });
  $("dgDesignGo").addEventListener("click", async () => {
    const prompt = $("dgPrompt").value.trim();
    if (!prompt) return toast("请先填写音色描述");
    const btn = $("dgDesignGo");
    btn.disabled = true; btn.textContent = "设计中…约 25 秒";
    try {
      const r = await fetch("/api/voices/design", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt,
          preview_text: $("dgPreview").value.trim() || undefined,
          name: $("dgDName").value.trim() || undefined,
          gender: $("dgDGender").value }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "设计失败");
      $("designDlg").close();
      await refresh();
      // 自动播放新音色试听
      const au = $("player"); au.src = `/api/voices/${d.voice.id}/audio`;
      au.play().catch(() => {});
      toast(`设计完成：${d.voice.name}（已入库，可直接设为主持/搭档）`);
    } catch (e) {
      toast("设计失败: " + e.message);
    } finally {
      btn.disabled = false; btn.textContent = "开始设计";
    }
  });
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
        <div class="vtile">
          <svg class="voice-icon" viewBox="0 0 32 32" aria-hidden="true">
            <rect x="4" y="13" width="3" height="6" rx="1.5"></rect>
            <rect x="9.5" y="8" width="3" height="16" rx="1.5"></rect>
            <rect x="15" y="4" width="3" height="24" rx="1.5"></rect>
            <rect x="20.5" y="8" width="3" height="16" rx="1.5"></rect>
            <rect x="26" y="13" width="3" height="6" rx="1.5"></rect>
          </svg>
        </div>
        <div class="vmeta">
          <div class="vname">${v.name}</div>
          <div class="vsub">${v.gender === "female" ? "女声" : "男声"} · ${v.age_range}岁</div>
        </div>
      </div>
      <div class="vtags">${(v.style_tags || []).map(t => `<span class="vtag">${t}</span>`).join("")}
        ${v.prompt_audio ? "" : '<span class="vtag warn">待录参考音频</span>'}</div>
      <div class="vacts">
        <button class="vplay" data-id="${v.id}">▶ 试听</button>
        <button class="vedit" data-id="${v.id}">编辑</button>
      </div>
    </div>`;
  }).join("") : `<div class="empty" style="grid-column:1/-1">没有匹配的音色，换个描述试试</div>`;

  document.querySelectorAll(".vplay").forEach(b => b.addEventListener("click", () => play(b.dataset.id, b)));
  document.querySelectorAll(".vedit").forEach(b => b.addEventListener("click", () =>
    openEdit(voices.find(v => v.id === b.dataset.id))));
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

// ---------- 试听 ----------
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
