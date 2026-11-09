"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const log = $("log"), input = $("input"), send = $("send");
  const EXAMPLE_NAMES = { "4f": "4f 成像系统", dp_aom: "AOM 双通", fiber: "光纤耦合" };

  let conversation = null;
  let builds = [];      // every build of this conversation, in order
  let selected = -1;
  let busy = false;
  let agentBubble = null;
  const pending = new Map(); // tool call id -> its card, until the build arrives
  let mode3d = "live";       // "live": draggable three.js view, "pic": the rendered picture
  let viewer = null, viewerUrl = null;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function scrollLog() { log.scrollTop = log.scrollHeight; }
  function add(node) { $("hello")?.remove(); log.appendChild(node); scrollLog(); return node; }

  // ---- status ----------------------------------------------------------------------
  fetch("/api/status").then((r) => r.json()).then((s) => {
    const m = $("model");
    if (s.llm.configured) { m.textContent = `模型 ${s.llm.model}`; m.classList.add("on"); }
    else { m.textContent = "未配置模型 API（见 .env.example）"; m.title = s.llm.error || ""; }
    for (const name of s.examples) {
      const b = el("button", "", EXAMPLE_NAMES[name] || name);
      b.type = "button";
      b.addEventListener("click", () => loadExample(name, b));
      $("offline").appendChild(b);
    }
  });

  // ---- builds: cards in the chat, versions on the stage -------------------------------
  function fillCard(card, b) {
    card.className = "build " + (b.ok ? "ok" : "bad");
    card.innerHTML = "";
    const head = el("div", "head", b.example ? `示例：${b.title}` : `第 ${b.n} 次构建`);
    head.appendChild(el("span", "state", b.ok ? "检查通过" : `未通过 · ${b.errors.length} 个问题`));
    card.appendChild(head);
    if (b.errors.length || b.warnings.length) {
      const ul = el("ul");
      for (const e of b.errors) ul.appendChild(el("li", "", e));
      for (const w of b.warnings) ul.appendChild(el("li", "w", "提示：" + w));
      card.appendChild(ul);
    }
    const t = b.timings || {};
    const secs = ((t.build_check || 0) + (t.render2d || 0) + (t.render3d || 0)).toFixed(1);
    card.appendChild(el("div", "meta", `${b.board_in ? b.board_in.join("×") + " 英寸面包板 · " : ""}出图 ${secs} 秒 · 点击查看`));
    card.onclick = () => select(b.index);
  }

  function addBuild(b, card) {
    b.index = builds.length;
    b.n = builds.filter((x) => !x.example).length + (b.example ? 0 : 1);
    b.title = b.spec && b.spec.title ? b.spec.title : "";
    b.card = card || add(el("button", "build"));
    b.card.type = "button";
    builds.push(b);
    fillCard(b.card, b);
    renderVersions();
    select(b.index);
  }

  function renderVersions() {
    const box = $("versions");
    box.innerHTML = "";
    builds.forEach((b) => {
      const btn = el("button");
      btn.type = "button";
      btn.setAttribute("role", "tab");
      btn.setAttribute("aria-selected", String(b.index === selected));
      const dot = el("span", "dot" + (b.ok ? " ok" : ""));
      btn.append(dot, b.example ? b.title : `v${b.n}`);
      btn.title = b.title + (b.ok ? "（检查通过）" : "（未通过）");
      btn.addEventListener("click", () => select(b.index));
      box.appendChild(btn);
    });
  }

  function previousOk(i) {
    for (let k = i - 1; k >= 0; k--) if (builds[k].ok && !builds[k].example) return builds[k];
    return null;
  }

  function pic(src, label) {
    const f = el("figure");
    if (label) f.appendChild(el("figcaption", "", label));
    const img = el("img");
    img.src = src;
    img.alt = label || "布局图";
    img.addEventListener("click", () => { $("zoomImg").src = src; $("zoom").showModal(); });
    f.appendChild(img);
    return f;
  }

  function select(i) {
    selected = i;
    const b = builds[i];
    builds.forEach((x) => x.card && x.card.classList.toggle("sel", x.index === i));
    renderVersions();
    $("empty").hidden = true;
    $("views").hidden = false;
    const prev = previousOk(i);
    $("compareWrap").hidden = !prev || b.example;
    const cmp = $("compare").checked && prev && !b.example;
    const p2 = $("pics2d"), p3 = $("pics3d");
    p2.innerHTML = ""; p3.innerHTML = "";
    p2.classList.toggle("two", !!cmp); p3.classList.toggle("two", !!cmp);
    if (cmp) {
      p2.append(pic(prev.images.png2d, `上一版 v${prev.n}`), pic(b.images.png2d, `当前 v${b.n}`));
      p3.append(pic(prev.images.png3d, `上一版 v${prev.n}`), pic(b.images.png3d, `当前 v${b.n}`));
    } else if (b.images.png2d) {
      p2.append(pic(b.images.png2d));
    }
    show3d(b, !!cmp);
    $("cap2d").textContent = b.title + (b.board_in ? ` · ${b.board_in.join("×")} 英寸` : "");
    $("summary").textContent = b.summary || "（没有生成布局）";
    $("spec").textContent = JSON.stringify(b.spec, null, 2);
    const dl = $("downloads");
    dl.hidden = !b.images.layout;
    if (b.images.layout) { $("dlJson").href = b.images.layout; $("dlSvg").href = b.images.svg2d; }
  }
  $("compare").addEventListener("change", () => selected >= 0 && select(selected));

  // ---- 3D: the draggable view, or the rendered picture ------------------------------------
  function dropViewer() {
    if (viewer) viewer.dispose();
    viewer = null; viewerUrl = null;
  }
  function show3d(b, comparing) {
    const live = $("live3d"), p3 = $("pics3d");
    const canLive = !comparing && b.images.gltf3d && window.OptViewer;
    const useLive = canLive && mode3d === "live";
    $("mode3dLive").setAttribute("aria-pressed", String(useLive));
    $("mode3dPic").setAttribute("aria-pressed", String(!useLive));
    $("mode3dLive").disabled = !canLive;
    $("reset3d").hidden = !useLive;
    $("help3d").textContent = comparing ? "对比时显示渲染图" : useLive ? "拖动旋转 · 滚轮缩放 · 右键平移 · 悬停查看元件" : "";
    live.hidden = !useLive;
    if (useLive) {
      if (!comparing) p3.innerHTML = "";
      if (viewerUrl !== b.images.gltf3d) {
        dropViewer();
        viewerUrl = b.images.gltf3d;
        viewer = window.OptViewer.mountViewer(live, b.images.gltf3d, () => {
          dropViewer();
          mode3d = "pic";
          select(selected); // the picture instead
        });
      }
    } else {
      dropViewer();
      if (!comparing && b.images.png3d) { p3.innerHTML = ""; p3.append(pic(b.images.png3d)); }
    }
  }
  $("mode3dLive").addEventListener("click", () => { mode3d = "live"; selected >= 0 && select(selected); });
  $("mode3dPic").addEventListener("click", () => { mode3d = "pic"; selected >= 0 && select(selected); });
  $("reset3d").addEventListener("click", () => viewer && viewer.reset());
  window.addEventListener("optviewer-ready", () => selected >= 0 && select(selected));

  // ---- chat ----------------------------------------------------------------------------
  function setBusy(on) {
    busy = on;
    send.disabled = on;
    send.textContent = on ? "设计中…" : "发送";
    document.querySelectorAll("#suggest button").forEach((b) => (b.disabled = on));
  }

  async function ask(text) {
    if (busy || !text.trim()) return;
    add(el("p", "msg user", text));
    input.value = "";
    setBusy(true);
    agentBubble = null;
    const thinking = add(el("p", "thinking", "agent 正在思考…"));
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ conversation, text }),
      });
      if (!res.ok || !res.body) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.error || `服务器返回 ${res.status}`);
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let cut;
        while ((cut = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, cut);
          buf = buf.slice(cut + 2);
          const ev = /^event: (.*)$/m.exec(block)?.[1];
          const data = /^data: (.*)$/m.exec(block)?.[1];
          if (ev && data) handle(ev, JSON.parse(data), thinking);
        }
      }
    } catch (e) {
      add(el("p", "msg err", "出错了：" + e.message));
    } finally {
      thinking.remove();
      setBusy(false);
      input.focus();
    }
  }

  function handle(ev, d, thinking) {
    switch (ev) {
      case "conversation":
        conversation = d.id;
        break;
      case "text":
        thinking.textContent = "";
        if (!agentBubble) agentBubble = add(el("p", "msg agent", ""));
        agentBubble.textContent += d.delta;
        scrollLog();
        break;
      case "thinking":
        thinking.textContent = "agent 正在推理光路结构…";
        break;
      case "tool_start": {
        agentBubble = null;
        const card = add(el("button", "build pending"));
        card.innerHTML = "";
        const head = el("div", "head", `第 ${builds.filter((x) => !x.example).length + 1} 次构建`);
        head.appendChild(el("span", "state", "摆放元件、检查并出图…"));
        card.appendChild(head);
        pending.set(d.id, card);
        thinking.textContent = "";
        break;
      }
      case "build": {
        const key = [...pending.keys()][0];
        const card = key ? pending.get(key) : null;
        if (key) pending.delete(key);
        addBuild(d, card);
        break;
      }
      case "tool_end":
        pending.delete(d.id);
        break;
      case "error":
        add(el("p", "msg err", "出错了：" + d.message));
        break;
    }
  }

  async function loadExample(name, btn) {
    btn.disabled = true;
    try {
      const r = await fetch("/api/example", {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name }),
      });
      const b = await r.json();
      if (!r.ok) throw new Error(b.error);
      b.example = true;
      addBuild(b);
    } catch (e) {
      add(el("p", "msg err", "加载示例失败：" + e.message));
    } finally {
      btn.disabled = false;
    }
  }

  $("compose").addEventListener("submit", (e) => { e.preventDefault(); ask(input.value); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask(input.value); }
  });
  $("suggest").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-prompt]");
    if (b) ask(b.dataset.prompt);
  });
  $("newConv").addEventListener("click", () => {
    if (busy) return;
    conversation = null; builds = []; selected = -1;
    dropViewer();
    log.innerHTML = "";
    log.appendChild(el("p", "hint", "新对话已开始。"));
    $("versions").innerHTML = "";
    $("views").hidden = true; $("empty").hidden = false; $("downloads").hidden = true; $("compareWrap").hidden = true;
  });
  $("zoomClose").addEventListener("click", () => $("zoom").close());
  $("zoom").addEventListener("click", (e) => { if (e.target === $("zoom")) $("zoom").close(); });
})();
