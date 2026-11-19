/* 本机创作账号：新增、重命名、切换、删除；与设置页的 LLM API 账号池无关。 */
(() => {
  const state = { accounts: [], activeId: "default" };
  let root;
  let editingId = null;

  async function api(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: options.body ? { "Content-Type": "application/json", ...(options.headers || {}) } : options.headers,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "操作失败");
    return data;
  }

  function mount() {
    const topbar = document.querySelector(".topbar");
    if (!topbar || document.querySelector(".creator-account")) return;
    const logo = topbar.querySelector(".logo");
    if (logo && !logo.querySelector(".brand-icon")) {
      logo.innerHTML = `<span class="brand-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="8" y="3" width="8" height="12" rx="4"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8"/></svg></span><span>AI 播客</span><span class="brand-badge">Studio</span>`;
    }
    root = document.createElement("div");
    root.className = "creator-account";
    root.innerHTML = `
      <button type="button" class="creator-trigger" aria-haspopup="true" aria-expanded="false">
        <span class="creator-avatar" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="8" r="3.5"/><path d="M5 20c.7-4 3-6 7-6s6.3 2 7 6"/></svg>
        </span>
        <span class="creator-trigger-text"><b id="creatorName">我的账号</b></span>
        <svg class="creator-caret" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m7 10 5 5 5-5"/></svg>
      </button>
      <div class="creator-menu" hidden>
        <div class="creator-menu-head"><b>创作账号</b></div>
        <div id="creatorList" class="creator-list"></div>
        <button type="button" class="creator-add" id="creatorAdd">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>
          新增创作账号
        </button>
      </div>`;
    topbar.appendChild(root);

    const dialog = document.createElement("dialog");
    dialog.className = "creator-dialog";
    dialog.innerHTML = `
      <form id="creatorForm">
        <button type="button" class="creator-close" aria-label="关闭">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m6 6 12 12M18 6 6 18"/></svg>
        </button>
        <div class="creator-dialog-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="8" r="3.5"/><path d="M5 20c.7-4 3-6 7-6s6.3 2 7 6"/></svg>
        </div>
        <h2 id="creatorTitle">新增创作账号</h2>
        <label for="creatorInput">账号名称</label>
        <input id="creatorInput" maxlength="30" autocomplete="off" placeholder="例如：品牌实验室" required>
        <div id="creatorError" class="creator-error" hidden></div>
        <div class="creator-dialog-actions">
          <button type="button" class="ghost" id="creatorCancel">取消</button>
          <button type="submit" class="primary" id="creatorSubmit">创建并切换</button>
        </div>
      </form>`;
    document.body.appendChild(dialog);

    const trigger = root.querySelector(".creator-trigger");
    const menu = root.querySelector(".creator-menu");
    const closeMenu = () => { menu.hidden = true; trigger.setAttribute("aria-expanded", "false"); };
    trigger.addEventListener("click", event => {
      event.stopPropagation();
      menu.hidden = !menu.hidden;
      trigger.setAttribute("aria-expanded", String(!menu.hidden));
    });
    menu.addEventListener("click", event => event.stopPropagation());
    document.addEventListener("click", closeMenu);

    const openDialog = account => {
      editingId = account ? account.id : null;
      dialog.querySelector("#creatorTitle").textContent = account ? "重命名创作账号" : "新增创作账号";
      dialog.querySelector("#creatorSubmit").textContent = account ? "保存名称" : "创建并切换";
      const input = dialog.querySelector("#creatorInput");
      input.value = account ? account.name : "";
      dialog.querySelector("#creatorError").hidden = true;
      dialog.showModal();
      setTimeout(() => { input.focus(); input.select(); }, 50);
    };

    root.querySelector("#creatorAdd").addEventListener("click", () => {
      closeMenu();
      openDialog(null);
    });
    dialog.querySelector(".creator-close").addEventListener("click", () => dialog.close());
    dialog.querySelector("#creatorCancel").addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", event => {
      if (event.target === dialog) dialog.close();
    });
    dialog.querySelector("#creatorForm").addEventListener("submit", async event => {
      event.preventDefault();
      const input = dialog.querySelector("#creatorInput");
      const error = dialog.querySelector("#creatorError");
      const submit = dialog.querySelector("#creatorSubmit");
      const name = input.value.trim();
      if (!name) { error.textContent = "请输入账号名称"; error.hidden = false; return; }
      submit.disabled = true;
      submit.textContent = editingId ? "保存中…" : "创建中…";
      error.hidden = true;
      try {
        if (editingId) {
          await api(`/api/creator-accounts/${encodeURIComponent(editingId)}`,
            { method: "PATCH", body: JSON.stringify({ name }) });
        } else {
          await api("/api/creator-accounts", { method: "POST", body: JSON.stringify({ name }) });
        }
        window.location.reload();
      } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
        submit.disabled = false;
        submit.textContent = editingId ? "保存名称" : "创建并切换";
      }
    });

    root.dataset.openEditor = "1";
    root._openEditor = openDialog;
  }

  function iconButton(action, title, path) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "creator-action";
    button.dataset.act = action;
    button.title = title;
    button.setAttribute("aria-label", title);
    button.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">${path}</svg>`;
    return button;
  }

  function render() {
    if (!root) return;
    const active = state.accounts.find(account => account.id === state.activeId) || state.accounts[0];
    root.querySelector("#creatorName").textContent = active ? active.name : "我的账号";
    const list = root.querySelector("#creatorList");
    list.replaceChildren();
    state.accounts.forEach(account => {
      const row = document.createElement("div");
      row.className = `creator-item${account.id === state.activeId ? " active" : ""}`;

      const switchButton = document.createElement("button");
      switchButton.type = "button";
      switchButton.className = "creator-main";
      const initial = document.createElement("span");
      initial.className = "creator-item-avatar";
      initial.textContent = (account.name || "账").slice(0, 1);
      const name = document.createElement("span");
      name.className = "creator-item-name";
      name.textContent = account.name;
      const check = document.createElement("span");
      check.className = "creator-check";
      check.textContent = account.id === state.activeId ? "✓" : "";
      switchButton.append(initial, name, check);
      switchButton.addEventListener("click", async () => {
        if (account.id === state.activeId) return;
        switchButton.disabled = true;
        try {
          await api(`/api/creator-accounts/${encodeURIComponent(account.id)}/select`, { method: "POST" });
          window.location.reload();
        } catch (err) {
          switchButton.disabled = false;
          window.alert(err.message);
        }
      });

      const rename = iconButton("rename", "重命名", '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>');
      rename.addEventListener("click", event => {
        event.stopPropagation();
        root._openEditor(account);
      });

      row.append(switchButton, rename);
      if (!account.is_default && account.id !== "default") {
        const remove = iconButton("delete", "删除账号", '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v5M14 11v5"/>');
        remove.addEventListener("click", async event => {
          event.stopPropagation();
          if (!queryConfirm(`删除创作账号「${account.name}」？\n该账号的任务、音色和模板将不再显示；本机工作区会移入 .trash 备份。`)) return;
          try {
            await api(`/api/creator-accounts/${encodeURIComponent(account.id)}`, { method: "DELETE" });
            window.location.reload();
          } catch (err) {
            window.alert(err.message);
          }
        });
        row.append(remove);
      }
      list.appendChild(row);
    });
  }

  // 自定义确认框不可用时回落到原生 confirm
  function queryConfirm(message) {
    return window.confirm(message);
  }

  async function init() {
    mount();
    try {
      const data = await api("/api/creator-accounts");
      state.accounts = data.accounts || [];
      state.activeId = data.active_id || "default";
      render();
    } catch (err) {
      console.warn("创作账号加载失败", err);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
