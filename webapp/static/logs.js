/* 日志查看器 —— 过滤/搜索/rid-jid 联动/自动刷新 */
const $ = (id) => document.getElementById(id);
let timer = null, openRid = null;

async function refresh() {
  const p = new URLSearchParams({
    source: $("fSource").value, level: $("fLevel").value,
    q: $("fQ").value.trim(), rid: $("fRid").value.trim(),
    jid: $("fJid").value.trim(), limit: $("fLimit").value,
  });
  let d;
  try {
    d = await (await fetch(`/api/logs?${p}`)).json();
  } catch { return; }
  if (d.files) {
    $("fileChips").innerHTML = Object.entries(d.files)
      .filter(([, f]) => f.exists)
      .map(([k, f]) => `<span class="lchip">${k}.jsonl · <b>${(f.size / 1024).toFixed(0)} KB</b></span>`)
      .join("");
  }
  const el = $("list");
  if (!d.items.length) { el.innerHTML = `<div class="empty">无匹配日志</div>`; return; }
  el.innerHTML = d.items.map((r, i) => {
    const ctxJson = r.ctx ? JSON.stringify(r.ctx, null, 1) : "";
    return `<div class="lrow${openRid && r.request_id === openRid ? " open" : ""}" data-i="${i}">
      <span class="ts">${(r.ts || "").slice(5)}</span>
      <span class="lv ${r.level}">${r.level}</span>
      <span class="src">${r.logger || ""}</span>
      <span class="ev">${esc(r.event || "")}</span>
      <span class="msg">${esc(r.message || "")}${r.exc ? `<span class="exc">${esc(r.exc)}</span>` : ""}
        <div class="ctx">${esc(ctxJson)}${r.request_id ? `\nrequest_id: ${r.request_id}` : ""}${r.job_id ? `\njob_id: ${r.job_id}` : ""}</div></span>
      <span class="rid">${r.request_id ? r.request_id.slice(0, 8) : ""}</span>
    </div>`;
  }).join("");
  el.querySelectorAll(".lrow").forEach(row =>
    row.addEventListener("click", () => {
      const r = d.items[+row.dataset.i];
      openRid = openRid === r.request_id ? null : (r.request_id || null);
      row.classList.toggle("open", !!openRid);
      const c = row.querySelector(".ctx");
      if (c) c.style.display = row.classList.contains("open") ? "block" : "none";
    }));
}

function esc(s) {
  return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function bind() {
  for (const id of ["fSource", "fLevel", "fLimit"]) $(id).addEventListener("change", refresh);
  let deb;
  $("fQ").addEventListener("input", () => { clearTimeout(deb); deb = setTimeout(refresh, 300); });
  $("fRid").addEventListener("input", () => { clearTimeout(deb); deb = setTimeout(refresh, 300); });
  $("fJid").addEventListener("input", () => { clearTimeout(deb); deb = setTimeout(refresh, 300); });
  $("btnRefresh").addEventListener("click", refresh);
  $("btnAuto").addEventListener("click", () => {
    const on = $("btnAuto").textContent.includes("关");
    $("btnAuto").textContent = `自动 5s: ${on ? "开" : "关"}`;
    if (on) timer = setInterval(refresh, 5000);
    else { clearInterval(timer); timer = null; }
  });
}

bind();
refresh();
