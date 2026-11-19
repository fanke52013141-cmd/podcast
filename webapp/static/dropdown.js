/* 自定义下拉框 —— 原生 select 的视觉替换（保留原生行为：键盘/表单兼容）
   用法：<select class="fancy"> 自动升级；悬浮菜单样式见 dropdown.css */
(function () {
  function build(sel) {
    if (sel.dataset.fancy) return;
    sel.dataset.fancy = "1";
    sel.style.display = "none";
    const wrap = document.createElement("div");
    wrap.className = "fancy-select";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "fancy-trigger";
    btn.textContent = sel.options[sel.selectedIndex]?.text || "";
    const menu = document.createElement("div");
    menu.className = "fancy-menu";
    [...sel.options].forEach((o, i) => {
      const row = document.createElement("div");
      row.className = "fancy-option" + (i === sel.selectedIndex ? " selected" : "");
      row.textContent = o.text;
      row.addEventListener("click", () => {
        sel.selectedIndex = i;
        sel.dispatchEvent(new Event("change", { bubbles: true }));
        btn.textContent = o.text;
        menu.querySelectorAll(".fancy-option").forEach((x, j) =>
          x.classList.toggle("selected", j === i));
        close();
      });
      menu.appendChild(row);
    });
    wrap.appendChild(btn);
    wrap.appendChild(menu);

    // 打开时用 fixed 定位：逃出 overflow:hidden 祖先（卡片裁切根因），并按视口空间翻转
    const position = () => {
      if (!wrap.classList.contains("open")) return;
      const r = btn.getBoundingClientRect();
      menu.style.position = "fixed";
      menu.style.left = `${r.left}px`;
      menu.style.width = `${r.width}px`;
      const below = window.innerHeight - r.bottom - 12;
      const above = r.top - 12;
      const natural = menu.scrollHeight + 16;        // 含上下 padding 8+8
      const want = Math.min(natural, 260);
      if (below >= want || below >= above) {          // 下方放得下（或下方空间更大）：向下展开
        menu.style.top = `${r.bottom + 6}px`;
        menu.style.maxHeight = `${Math.max(140, Math.min(260, below))}px`;
      } else {                                        // 下方空间不足：向上翻转
        menu.style.top = `${Math.max(8, r.top - 6 - Math.min(natural, above))}px`;
        menu.style.maxHeight = `${Math.max(140, Math.min(260, above))}px`;
      }
    };
    const open = () => { wrap.classList.add("open"); position(); };
    const close = () => { wrap.classList.remove("open"); };
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      document.querySelectorAll(".fancy-select.open").forEach(w => w !== wrap && w.classList.remove("open"));
      wrap.classList.contains("open") ? close() : open();
    });
    document.addEventListener("click", close);
    window.addEventListener("scroll", position, { passive: true });
    window.addEventListener("resize", position);

    // 原生 select 变化（程序赋值）时同步显示
    sel.addEventListener("change", () => {
      btn.textContent = sel.options[sel.selectedIndex]?.text || "";
      menu.querySelectorAll(".fancy-option").forEach((x, j) =>
        x.classList.toggle("selected", j === sel.selectedIndex));
    });

    sel.insertAdjacentElement("afterend", wrap);
  }

  function scan() { document.querySelectorAll("select:not(.fancy-done)").forEach(s => {
    if (s.dataset.noFancy !== undefined) return;
    s.classList.add("fancy-done"); build(s);
  }); }

  // 动态选项同步：防抖 + 断开重连（防止重建触发 observer 无限循环卡死页面）
  let moTimer = null;
  const mo = new MutationObserver(() => {
    mo.disconnect();
    clearTimeout(moTimer);
    moTimer = setTimeout(() => {
      syncAll();
      mo.observe(document.body, { childList: true, subtree: true });
    }, 150);
  });

  function syncAll() {
    document.querySelectorAll("select.fancy-done").forEach(sel => {
      const wrap = sel.nextElementSibling;
      if (!wrap || !wrap.classList.contains("fancy-select")) return;
      const menu = wrap.querySelector(".fancy-menu");
      const btn = wrap.querySelector(".fancy-trigger");
      if (!menu) return;
      // 选项数量或内容变化时才重建（避免无谓 DOM 写入再触发 observer）
      const optSig = [...sel.options].map(o => o.value + "|" + o.text).join("");
      if (menu.dataset.sig !== optSig) {
        menu.dataset.sig = optSig;
        menu.innerHTML = "";
        [...sel.options].forEach((o, i) => {
          const row = document.createElement("div");
          row.className = "fancy-option" + (i === sel.selectedIndex ? " selected" : "");
          row.textContent = o.text;
          row.addEventListener("click", () => {
            sel.selectedIndex = i;
            sel.dispatchEvent(new Event("change", { bubbles: true }));
            btn.textContent = o.text;
            menu.querySelectorAll(".fancy-option").forEach((x, j) =>
              x.classList.toggle("selected", j === i));
            wrap.classList.remove("open");
          });
          menu.appendChild(row);
        });
      }
      const label = sel.options[sel.selectedIndex]?.text || "";
      if (btn.textContent !== label) btn.textContent = label;
    });
  }
  window.fancySync = syncAll;
  // 必须立即开始观察：设置页等异步填充 select 的页面依赖首次选项同步。
  mo.observe(document.body, { childList: true, subtree: true });
  setTimeout(syncAll, 0);

  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", scan)
    : scan();
})();
