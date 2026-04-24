// ============================================================
//         作业辅助工具 v3 前端 —— 个性化多学生版
// ============================================================

// ===================== 公共工具 =====================
const $  = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

function showToast(msg, ms = 2000) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => t.classList.add("hidden"), ms);
}

function showLoading(text = "处理中...") {
  $("#loading-text").textContent = text;
  $("#loading-mask").classList.remove("hidden");
}
function hideLoading() { $("#loading-mask").classList.add("hidden"); }

async function apiGet(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text().catch(() => "")}`);
  return res.json();
}
async function apiJson(url, method, body) {
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body == null ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text().catch(() => "")}`);
  return res.json();
}
async function apiForm(url, formData) {
  const res = await fetch(url, { method: "POST", body: formData });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text().catch(() => "")}`);
  return res.json();
}

function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ===================== 极简 Markdown 渲染（供 AI 讲解用） =====================
function renderMarkdown(md) {
  if (!md) return "";
  const esc = (s) => escapeHtml(s);
  let src = md.replace(/\r\n/g, "\n");

  // fenced code ```
  src = src.replace(/```([\s\S]*?)```/g, (_, c) => `<pre><code>${esc(c)}</code></pre>`);

  // 分段处理
  const lines = src.split("\n");
  const out = [];
  let inList = false, listType = null;
  const closeList = () => {
    if (inList) { out.push(listType === "ul" ? "</ul>" : "</ol>"); inList = false; listType = null; }
  };
  for (let raw of lines) {
    const line = raw;
    if (/^<pre><code>[\s\S]*$/.test(line) && /<\/code><\/pre>$/.test(line)) {
      closeList(); out.push(line); continue;
    }
    // 标题
    let m;
    if ((m = line.match(/^#{1,6}\s+(.*)$/))) {
      closeList();
      const level = line.match(/^#+/)[0].length;
      out.push(`<h${level}>${inlineMd(esc(m[1]))}</h${level}>`);
      continue;
    }
    // 无序列表
    if ((m = line.match(/^\s*[-*]\s+(.*)$/))) {
      if (!inList || listType !== "ul") { closeList(); out.push("<ul>"); inList = true; listType = "ul"; }
      out.push(`<li>${inlineMd(esc(m[1]))}</li>`);
      continue;
    }
    // 有序列表
    if ((m = line.match(/^\s*\d+\.\s+(.*)$/))) {
      if (!inList || listType !== "ol") { closeList(); out.push("<ol>"); inList = true; listType = "ol"; }
      out.push(`<li>${inlineMd(esc(m[1]))}</li>`);
      continue;
    }
    // 空行 → 段落分隔
    if (/^\s*$/.test(line)) {
      closeList();
      out.push("");
      continue;
    }
    // 直通（段落）
    closeList();
    out.push(`<p>${inlineMd(esc(line))}</p>`);
  }
  closeList();
  return out.filter(Boolean).join("\n");

  function inlineMd(s) {
    // **bold**
    s = s.replace(/\*\*([^*]+?)\*\*/g, "<strong>$1</strong>");
    // *italic*
    s = s.replace(/\*([^*]+?)\*/g, "<em>$1</em>");
    // `code`
    s = s.replace(/`([^`]+?)`/g, "<code>$1</code>");
    return s;
  }
}

// ===================== 全局状态 =====================
const STATE = {
  currentStudent: null,   // {id, name, avatar, ...}
  students: [],
  currentView: "login",
};

function saveCurrentStudent(s) {
  STATE.currentStudent = s;
  if (s) localStorage.setItem("currentStudentId", s.id);
  else localStorage.removeItem("currentStudentId");
  renderCurrentStudentBar();
}

function renderCurrentStudentBar() {
  const s = STATE.currentStudent;
  const bar = $("#btn-current-student");
  if (!s) { bar.classList.add("hidden"); return; }
  bar.classList.remove("hidden");
  const av = $("#cs-avatar");
  if (s.avatar && s.avatar.startsWith("/")) {
    av.innerHTML = `<img src="${s.avatar}" />`;
  } else {
    av.textContent = s.avatar || "🧑‍🎓";
  }
  $("#cs-name").textContent = s.name || "-";
}

// 点击当前学生 → 回到登录页
$("#btn-current-student").addEventListener("click", () => goto("login"));

// ===================== 路由 =====================
const VIEW_TITLES = {
  login: "作业辅助工具",
  home: "学生工作台",
  batch: "批量批改",
  realtime: "实时批改",
  errors: "错题本",
  knowledge: "知识图谱",
  ability: "能力雷达",
  practice: "练习生成",
  manage: "我的题库",
  global: "全局题库",
  teacher: "教师面板",
  "teacher-student": "学生详情",
  settings: "大模型设置",
};

// 需要「已选学生」才能进入的视图
const NEEDS_STUDENT = new Set([
  "home", "batch", "realtime", "errors", "knowledge",
  "ability", "practice", "manage",
]);

function goto(view) {
  if (NEEDS_STUDENT.has(view) && !STATE.currentStudent) {
    showToast("请先选择一个学生");
    view = "login";
  }
  $$(".view").forEach(v => v.classList.remove("active"));
  const target = $(`#view-${view}`);
  if (target) target.classList.add("active");
  $("#btn-back").classList.toggle("hidden", view === "login");
  $("#page-title").textContent = VIEW_TITLES[view] || "作业辅助工具";
  STATE.currentView = view;

  // 离开实时模式时关摄像头
  if (view !== "realtime") stopCamera();

  // 进入视图时的数据加载
  switch (view) {
    case "login":     refreshStudents(); break;
    case "home":      refreshDashboard(); break;
    case "errors":    refreshErrors(); break;
    case "knowledge": refreshKnowledge(); break;
    case "ability":   refreshAbility(); break;
    case "practice":  refreshPractice(); break;
    case "manage":    refreshBanks(); break;
    case "global":    refreshGlobalBanks(); break;
    case "teacher":   refreshTeacher(); break;
    case "teacher-student": refreshTeacherStudent(); break;
    case "settings":  refreshSettings(); break;
  }
}

$("#btn-back").addEventListener("click", () => {
  if (STATE.currentView === "teacher-student") {
    goto("teacher");
    return;
  }
  if (STATE.currentView === "home" || STATE.currentView === "settings") {
    goto(STATE.currentStudent ? "home" : "login");
    if (STATE.currentView === "settings") goto(STATE.currentStudent ? "home" : "login");
    return;
  }
  goto(STATE.currentStudent ? "home" : "login");
});
$$("[data-goto]").forEach(b => b.addEventListener("click", () => goto(b.dataset.goto)));
$("#btn-top-settings").addEventListener("click", () => goto("settings"));
$("#btn-goto-settings").addEventListener("click", () => goto("settings"));
$("#btn-goto-global").addEventListener("click", () => goto("global"));
$("#btn-goto-teacher").addEventListener("click", () => goto("teacher"));

// ============================================================
//                         学生管理
// ============================================================
async function refreshStudents() {
  const grid = $("#student-grid");
  try {
    const r = await apiGet("/api/students");
    STATE.students = r.items || [];
    if (!STATE.students.length) {
      grid.innerHTML = `
        <div class="placeholder" style="grid-column: 1/-1;">
          还没有学生，点下方「+ 添加学生」开始吧
        </div>`;
      return;
    }
    grid.innerHTML = "";
    STATE.students.forEach(s => grid.appendChild(renderStudentCard(s)));
  } catch (e) {
    grid.innerHTML = `<div class="placeholder">加载失败: ${escapeHtml(e.message)}</div>`;
  }
}

function renderStudentCard(s) {
  const card = document.createElement("div");
  card.className = "student-card";
  const avatarHtml = (s.avatar || "").startsWith("/")
    ? `<img src="${s.avatar}" />`
    : `<span>${s.avatar || "🧑‍🎓"}</span>`;
  const st = s.stats || {};
  const acc = st.accuracy == null ? "-" : st.accuracy + "%";
  card.innerHTML = `
    <div class="sc-avatar">${avatarHtml}</div>
    <div class="sc-name">${escapeHtml(s.name || "")}</div>
    <div class="sc-sub">${escapeHtml([s.grade, s.subject].filter(Boolean).join(" · "))}</div>
    <div class="sc-mini">
      <span>错题 ${st.error_count || 0}</span>
      <span>知识点 ${st.knowledge_count || 0}</span>
      <span>正确率 ${acc}</span>
    </div>
    <div class="sc-actions">
      <button class="btn btn-primary" data-pick="${s.id}">进入</button>
      <button class="btn btn-sm" data-edit="${s.id}">编辑</button>
    </div>
  `;
  card.querySelector("[data-pick]").addEventListener("click", () => {
    saveCurrentStudent(s);
    goto("home");
  });
  card.querySelector("[data-edit]").addEventListener("click", () => {
    openStudentModal(s);
  });
  return card;
}

$("#btn-add-student").addEventListener("click", () => openStudentModal(null));

// ---------- 学生编辑弹窗 ----------
let editingStudent = null;
let pendingAvatarEmoji = "🧑‍🎓";
let pendingAvatarFile = null;

function openStudentModal(s) {
  editingStudent = s;
  pendingAvatarFile = null;
  $("#sm-title").textContent = s ? "编辑学生" : "添加学生";
  $("#sm-name").value    = s?.name    || "";
  $("#sm-grade").value   = s?.grade   || "";
  $("#sm-subject").value = s?.subject || "";
  $("#sm-note").value    = s?.note    || "";
  const preview = $("#sm-avatar-preview");
  if (s?.avatar && s.avatar.startsWith("/")) {
    preview.innerHTML = `<img src="${s.avatar}" />`;
    pendingAvatarEmoji = null;
  } else {
    const emo = s?.avatar || "🧑‍🎓";
    pendingAvatarEmoji = emo;
    preview.innerHTML = `<span>${emo}</span>`;
  }
  $("#sm-delete").style.display = s ? "" : "none";
  $("#student-modal").classList.remove("hidden");
}
function closeStudentModal() { $("#student-modal").classList.add("hidden"); }

$("#sm-close").addEventListener("click", closeStudentModal);
$("#sm-cancel").addEventListener("click", closeStudentModal);

$$(".sm-emoji").forEach(b => b.addEventListener("click", () => {
  pendingAvatarEmoji = b.dataset.emoji;
  pendingAvatarFile = null;
  $("#sm-avatar-preview").innerHTML = `<span>${b.dataset.emoji}</span>`;
}));

$("#sm-pick-avatar").addEventListener("click", () => $("#sm-avatar-file").click());
$("#sm-avatar-file").addEventListener("change", (e) => {
  const f = e.target.files[0];
  if (!f) return;
  pendingAvatarFile = f;
  pendingAvatarEmoji = null;
  const url = URL.createObjectURL(f);
  $("#sm-avatar-preview").innerHTML = `<img src="${url}" />`;
});

$("#sm-save").addEventListener("click", async () => {
  const name = $("#sm-name").value.trim();
  if (!name) return showToast("请输入姓名");
  const payload = {
    name,
    grade: $("#sm-grade").value,
    subject: $("#sm-subject").value,
    note: $("#sm-note").value,
    avatar: pendingAvatarEmoji || (editingStudent?.avatar || ""),
  };
  try {
    showLoading("保存中...");
    let sid;
    if (editingStudent) {
      await apiJson(`/api/students/${editingStudent.id}`, "PUT", payload);
      sid = editingStudent.id;
    } else {
      const r = await apiJson("/api/students", "POST", payload);
      sid = r.item.id;
    }
    // 如果选了文件，再上传头像
    if (pendingAvatarFile) {
      const fd = new FormData();
      fd.append("file", pendingAvatarFile);
      await apiForm(`/api/students/${sid}/avatar`, fd);
    }
    closeStudentModal();
    await refreshStudents();
    // 如果正在编辑的是当前学生，更新 STATE
    if (STATE.currentStudent && STATE.currentStudent.id === sid) {
      const g = await apiGet(`/api/students/${sid}`);
      saveCurrentStudent(g.item);
    }
    showToast("已保存");
  } catch (e) {
    showToast("保存失败: " + e.message, 3000);
  } finally {
    hideLoading();
  }
});

$("#sm-delete").addEventListener("click", async () => {
  if (!editingStudent) return;
  if (!confirm(`确定要删除「${editingStudent.name}」的全部数据吗？此操作不可恢复！`)) return;
  try {
    showLoading("删除中...");
    await fetch(`/api/students/${editingStudent.id}`, { method: "DELETE" });
    if (STATE.currentStudent && STATE.currentStudent.id === editingStudent.id) {
      saveCurrentStudent(null);
    }
    closeStudentModal();
    await refreshStudents();
    goto("login");
    showToast("已删除");
  } catch (e) {
    showToast("删除失败: " + e.message, 3000);
  } finally {
    hideLoading();
  }
});

// ============================================================
//                         仪表盘
// ============================================================
async function refreshDashboard() {
  const s = STATE.currentStudent;
  if (!s) return;
  // 头像/名字
  const av = $("#dash-avatar");
  if (s.avatar && s.avatar.startsWith("/")) av.innerHTML = `<img src="${s.avatar}" />`;
  else av.textContent = s.avatar || "🧑‍🎓";
  $("#dash-name").textContent = s.name || "";
  $("#dash-sub").textContent = [s.grade, s.subject].filter(Boolean).join(" · ") || "";

  // 拉最新统计
  try {
    const r = await apiGet(`/api/students/${s.id}`);
    saveCurrentStudent(r.item);
    const st = r.item.stats || {};
    $("#dash-stats").innerHTML = `
      <div class="stat-tile"><div class="st-num">${st.history_count || 0}</div><div class="st-label">已批改</div></div>
      <div class="stat-tile"><div class="st-num">${st.error_count || 0}</div><div class="st-label">错题</div></div>
      <div class="stat-tile"><div class="st-num">${st.knowledge_count || 0}</div><div class="st-label">知识点</div></div>
      <div class="stat-tile"><div class="st-num">${st.accuracy == null ? "-" : st.accuracy + "%"}</div><div class="st-label">正确率</div></div>
    `;
  } catch (e) {
    $("#dash-stats").innerHTML = `<div class="placeholder">统计加载失败</div>`;
  }

  // 底部状态栏
  try {
    const h = await apiGet("/api/health");
    $("#status-bar").innerHTML =
      `供应商: <b>${providerLabel(h.active_provider)}</b> · ` +
      `视觉: <b>${escapeHtml(h.vision_model || "-")}</b> · ` +
      `文本: <b>${escapeHtml(h.text_model || "-")}</b> · ` +
      `全局题库: 短期 <b>${h.global_short_term_count ?? 0}</b> / 长期 <b>${h.global_rag_count ?? 0}</b>`;
  } catch {
    $("#status-bar").textContent = "⚠ 服务未响应";
  }
}

function providerLabel(name) {
  return ({ ollama:"Ollama", custom:"自定义", deepseek:"DeepSeek",
           doubao:"豆包", qwen:"千问",
           // 兼容：历史数据里可能出现过 gemini
           gemini:"自定义"})[name] || name || "-";
}

// ============================================================
//                      批量批改模式
// ============================================================
const queue = [];
let currentIdx = -1;

$("#btn-pick").addEventListener("click", () => $("#batch-files").click());
$("#batch-files").addEventListener("change", (e) => {
  const files = [...e.target.files];
  files.forEach(f => {
    const id = Date.now() + "_" + Math.random().toString(36).slice(2, 6);
    queue.push({
      id, file: f, url: URL.createObjectURL(f),
      questions: null, results: null, status: "pending",
    });
  });
  e.target.value = "";
  // Bug 1 修复：上传文件后自动选中第一张，否则 currentIdx 永远是 -1，
  // 导致批改过程中所有渲染条件 (queue.indexOf(item) === currentIdx) 都为 false，
  // 流式 token 全部被丢弃，结果"一次性"出现。
  if (currentIdx < 0 && queue.length > 0) {
    currentIdx = 0;
    renderCurrent();
  }
  renderQueue();
});

$("#btn-clear-queue").addEventListener("click", () => {
  queue.forEach(q => URL.revokeObjectURL(q.url));
  queue.length = 0;
  currentIdx = -1;
  renderQueue();
  renderCurrent();
});

function renderQueue() {
  const strip = $("#queue-strip");
  strip.innerHTML = "";
  queue.forEach((q, i) => {
    const div = document.createElement("div");
    div.className = "queue-thumb" + (i === currentIdx ? " active" : "") +
                    (q.status === "done" ? " done" : "");
    div.innerHTML = `<img src="${q.url}" alt="" /><span class="rm" data-rm="${i}">×</span>`;
    div.addEventListener("click", (e) => {
      if (e.target.classList.contains("rm")) return;
      currentIdx = i; renderQueue(); renderCurrent();
    });
    strip.appendChild(div);
  });
  strip.querySelectorAll("[data-rm]").forEach(el => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      const i = +el.dataset.rm;
      URL.revokeObjectURL(queue[i].url);
      queue.splice(i, 1);
      if (currentIdx >= queue.length) currentIdx = queue.length - 1;
      renderQueue(); renderCurrent();
    });
  });
  $("#queue-count").textContent = `队列: ${queue.length}`;
  $("#btn-start-grade").disabled =
    queue.length === 0 || queue.every(q => q.status === "done");
}

function renderCurrent() {
  const stage = $("#image-stage");
  const list  = $("#grade-list");
  if (currentIdx < 0 || !queue[currentIdx]) {
    stage.innerHTML = '<div class="placeholder">这里会显示当前照片和识别框</div>';
    list.innerHTML = '<div class="placeholder">批改结果将显示在这里</div>';
    $("#page-indicator").textContent = "0 / 0";
    return;
  }
  const item = queue[currentIdx];
  stage.innerHTML = `<img id="cur-img" src="${item.url}" /><div class="box-layer" id="cur-layer"></div>`;
  $("#page-indicator").textContent = `${currentIdx + 1} / ${queue.length}`;
  const img = $("#cur-img");
  img.onload = () => drawBoxes(item);
  if (img.complete) drawBoxes(item);
  renderGradeList(list, item);
}

function drawBoxes(item) {
  const img = $("#cur-img");
  const layer = $("#cur-layer");
  if (!img || !layer || !item.questions) return;
  const rect = img.getBoundingClientRect();
  const stageRect = $("#image-stage").getBoundingClientRect();
  layer.style.left = (rect.left - stageRect.left) + "px";
  layer.style.top  = (rect.top - stageRect.top) + "px";
  layer.style.width = rect.width + "px";
  layer.style.height = rect.height + "px";
  layer.innerHTML = "";
  item.questions.forEach((q, idx) => {
    if (q._detect_failed) return;   // 识别失败不画框
    const r = item.results ? item.results[idx] : null;
    const [x1, y1, x2, y2] = q.bbox;
    const box = document.createElement("div");
    box.className = "bbox";
    if (r) {
      if (r.is_correct === true) box.classList.add("correct");
      else if (r.is_correct === false) box.classList.add("wrong");
    }
    box.style.left = (x1 / 1000 * 100) + "%";
    box.style.top  = (y1 / 1000 * 100) + "%";
    box.style.width  = ((x2 - x1) / 1000 * 100) + "%";
    box.style.height = ((y2 - y1) / 1000 * 100) + "%";
    const typeTag = typeBadge(q.type);
    box.innerHTML = `<span class="bbox-label">#${q.index}${typeTag ? " " + typeTag : ""}</span>`;
    layer.appendChild(box);
  });
}

function typeBadge(t) {
  if (t === "multiple_choice") return "选";
  if (t === "fill_blank") return "填";
  return "";
}
function typeName(t) {
  if (t === "multiple_choice") return "选择题";
  if (t === "fill_blank") return "填空题";
  return "普通题";
}

function renderQuestionCard(q, r) {
  // 识别失败的占位题：直接展示清晰的错误卡，不展示题干
  if (q._detect_failed || r?._detect_failed) {
    const card = document.createElement("div");
    card.className = "grade-card detect-failed";
    card.innerHTML = `
      <div class="grade-head">
        <div class="grade-idx">识别失败</div>
        <div class="grade-verdict wrong">⚠</div>
      </div>
      <div class="grade-field" style="margin-top:8px;line-height:1.6;">
        ${escapeHtml(q.question_text || r?.explanation || "视觉模型未能识别出题目。")}
      </div>
      <div class="grade-field hint" style="margin-top:6px;">
        建议：拍照时让整页清晰、光线充足、避免反光；或前往「设置」切换到更强的视觉模型（如 千问-VL / 豆包视觉，或用"自定义"接入其它视觉模型）。
      </div>
    `;
    return card;
  }

  let cls = "unknown", label = "待批改";
  if (r) {
    if (r.is_correct === true)  { cls = "correct"; label = "正确"; }
    else if (r.is_correct === false) { cls = "wrong"; label = "错误"; }
    else { cls = "unknown"; label = "无法判断"; }
  }
  const t = q.type || "normal";
  const typeText = typeName(t);
  let bodyHtml = "";

  if (t === "multiple_choice") {
    const options = q.options || [];
    const sChoice = (r && r.student_choice) || (r && r.student_answer) || q.student_choice || "";
    const cChoice = (r && r.correct_choice) || (r && r.correct_answer) || "";
    const sUp = (sChoice || "").toUpperCase();
    const cUp = (cChoice || "").toUpperCase();
    let optsHtml = "";
    if (options.length) {
      optsHtml = '<div class="mc-options">';
      for (const opt of options) {
        const lbl = (opt.label || "").toUpperCase();
        const classes = ["mc-opt"];
        if (lbl && lbl === cUp) classes.push("mc-correct");
        if (lbl && lbl === sUp && sUp !== cUp) classes.push("mc-wrong");
        if (lbl && lbl === sUp && sUp === cUp) classes.push("mc-chosen-ok");
        let tag = "";
        if (lbl === cUp && cUp) tag = '<span class="mc-tag ok">正确</span>';
        if (lbl === sUp && sUp && sUp !== cUp) tag = '<span class="mc-tag bad">学生选</span>';
        if (lbl === sUp && lbl === cUp && sUp) tag = '<span class="mc-tag ok">学生选 ✓</span>';
        optsHtml += `<div class="${classes.join(" ")}"><span class="mc-label">${escapeHtml(lbl)}</span><span class="mc-text">${escapeHtml(opt.text || "")}</span>${tag}</div>`;
      }
      optsHtml += "</div>";
    }
    bodyHtml = `
      ${optsHtml}
      ${r ? `
        <div class="grade-field"><strong>学生所选:</strong>${escapeHtml(sUp || "未选")}</div>
        <div class="grade-field"><strong>正确选项:</strong>${escapeHtml(cUp || "-")}</div>
        ${r.error_reason ? `<div class="grade-field"><strong>错因:</strong>${escapeHtml(r.error_reason)}</div>` : ""}
        ${r.explanation ? `<div class="grade-field"><strong>解析:</strong>${escapeHtml(r.explanation)}</div>` : ""}
        ${renderKpTags(r)}
      ` : '<div class="grade-field">（点击「开始批改」）</div>'}`;
  } else if (t === "fill_blank") {
    const blanks = (r && r.blanks && r.blanks.length) ? r.blanks :
                   (q.blanks || []).map(b => ({
                     index: b.index, student_fill: b.student_fill,
                     correct_fill: "", is_correct: null, note: "",
                   }));
    let blanksHtml = "";
    if (blanks.length) {
      blanksHtml = '<div class="fill-blanks">';
      for (const b of blanks) {
        let bcls = "fb-unknown", bIcon = "？";
        if (b.is_correct === true) { bcls = "fb-correct"; bIcon = "✓"; }
        else if (b.is_correct === false) { bcls = "fb-wrong"; bIcon = "✗"; }
        blanksHtml += `
          <div class="fb-row ${bcls}">
            <div class="fb-head"><span class="fb-index">第 ${escapeHtml(b.index)} 空</span><span class="fb-icon">${bIcon}</span></div>
            <div class="fb-line"><span class="fb-key">学生:</span><span>${escapeHtml(b.student_fill || "（空）")}</span></div>
            ${b.correct_fill ? `<div class="fb-line"><span class="fb-key">正确:</span><span>${escapeHtml(b.correct_fill)}</span></div>` : ""}
            ${b.note ? `<div class="fb-note">${escapeHtml(b.note)}</div>` : ""}
          </div>`;
      }
      blanksHtml += "</div>";
    }
    bodyHtml = `${blanksHtml}
      ${r ? `
        ${r.error_reason ? `<div class="grade-field"><strong>错因:</strong>${escapeHtml(r.error_reason)}</div>` : ""}
        ${r.explanation ? `<div class="grade-field"><strong>解析:</strong>${escapeHtml(r.explanation)}</div>` : ""}
        ${renderKpTags(r)}
      ` : '<div class="grade-field">（点击「开始批改」）</div>'}`;
  } else {
    bodyHtml = `
      ${r ? `
        ${r.student_answer ? `<div class="grade-field"><strong>学生答:</strong>${escapeHtml(r.student_answer)}</div>` : ""}
        ${r.correct_answer ? `<div class="grade-field"><strong>正确答案:</strong>${escapeHtml(r.correct_answer)}</div>` : ""}
        ${r.error_reason ? `<div class="grade-field"><strong>错因:</strong>${escapeHtml(r.error_reason)}</div>` : ""}
        ${r.explanation ? `<div class="grade-field"><strong>解析:</strong>${escapeHtml(r.explanation)}</div>` : ""}
        ${renderKpTags(r)}
      ` : '<div class="grade-field">（点击「开始批改」）</div>'}`;
  }

  const card = document.createElement("div");
  card.className = "grade-card " + cls;
  card.innerHTML = `
    <div class="grade-head">
      <div class="grade-idx">第 ${q.index} 题<span class="type-pill ${t}">${typeText}</span></div>
      <div class="grade-verdict ${cls}">${label}</div>
    </div>
    <div class="grade-q">${escapeHtml(q.question_text || "(未识别题目)")}</div>
    ${renderThinkPanel(r)}
    ${bodyHtml}`;
  return card;
}

// v5：思考区渲染（流式中 / 完成后）
// 两种情况：
//   1) 正在流式（没有 r）：显示"AI 思考中..."占位 + 活体文本区 + 闪烁光标
//   2) 已完成（r 存在，且 r.thinking）：显示可折叠的"AI 思考过程"
function renderThinkPanel(r) {
  if (r && r.thinking) {
    const safeTxt = escapeHtml(r.thinking).replace(/\n/g, "<br/>");
    return `<details class="think-panel think-done">
      <summary><span class="tp-icon">🧠</span>AI 思考过程 <span class="tp-hint">点击展开</span></summary>
      <div class="tp-body">${safeTxt}</div>
    </details>`;
  }
  if (r) return "";  // 已完成但没 thinking，不显示
  // 占位：流式用
  return `<div class="think-panel think-live" data-live="1">
    <div class="tp-head"><span class="tp-icon">💭</span>AI 正在推导<span class="tp-dots"><i>.</i><i>.</i><i>.</i></span></div>
    <div class="tp-body tp-stream"></div>
    <span class="tp-cursor">▍</span>
  </div>`;
}

function renderKpTags(r) {
  const kps = r.knowledge_points || [];
  const cats = r.error_categories || [];
  if (!kps.length && !cats.length) return "";
  const kpHtml = kps.map(k => `<span class="tag tag-kp">${escapeHtml(k)}</span>`).join("");
  const catHtml = cats.map(c => `<span class="tag tag-cat">${escapeHtml(c)}</span>`).join("");
  return `<div class="grade-tags">${kpHtml}${catHtml}</div>`;
}

function renderGradeList(listEl, item) {
  if (!item.questions) { listEl.innerHTML = '<div class="placeholder">等待批改...</div>'; return; }
  listEl.innerHTML = "";
  item.questions.forEach((q, idx) => {
    const r = item.results ? item.results[idx] : null;
    listEl.appendChild(renderQuestionCard(q, r));
  });
}

// --- 小工具：解析 SSE data: JSON 行 ---
async function streamSSE(url, formData, onEvent, onError) {
  const res = await fetch(url, { method: "POST", body: formData });
  if (!res.ok || !res.body) {
    const txt = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status}: ${txt.slice(0, 200)}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    // 用 \n\n 分事件
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      for (const line of chunk.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (!payload) continue;
        try {
          onEvent(JSON.parse(payload));
        } catch (e) {
          console.warn("SSE 解析失败", e, payload);
        }
      }
    }
  }
  // 收尾
  if (buf.trim()) {
    for (const line of buf.split("\n")) {
      if (line.startsWith("data:")) {
        try { onEvent(JSON.parse(line.slice(5).trim())); }
        catch {}
      }
    }
  }
}

// 状态条：简单的 toast 替代品，长驻左下角
function setBatchStatus(text, mode = "info") {
  let el = $("#batch-status-bar");
  if (!el) {
    el = document.createElement("div");
    el.id = "batch-status-bar";
    el.className = "status-pill";
    document.body.appendChild(el);
  }
  el.textContent = text;
  el.dataset.mode = mode;
  el.classList.remove("hidden");
  clearTimeout(setBatchStatus._t);
  if (mode === "done" || mode === "error") {
    setBatchStatus._t = setTimeout(() => el.classList.add("hidden"), 2500);
  }
}

$("#btn-start-grade").addEventListener("click", async () => {
  if (!STATE.currentStudent) return showToast("请先选择学生");
  const pending = queue.filter(q => q.status !== "done");
  if (pending.length === 0) return showToast("队列为空或已全部批改");

  // Bug 1 双保险：若用户跳过点缩略图直接点批改，这里兜底设置 currentIdx
  if (currentIdx < 0 && queue.length > 0) {
    currentIdx = 0;
    renderCurrent();
    renderQueue();
  }

  // 不再弹全屏 loading，改为持续状态条 + 逐题渲染
  let pageDone = 0;
  for (const item of pending) {
    try {
      item.status = "processing";
      setBatchStatus(`(${pageDone + 1}/${pending.length}) 识别中...`, "info");

      // Bug 2 修复：原来先 await /api/detect（阻塞 5-20s）再开流式批改，
      // 用户感知是"上传完等一段时间才出卡片"。
      // 改用 /api/realtime_stream 把检测+批改合并进同一个 SSE 流：
      //   detected 事件一到立刻渲染卡片骨架（含流式思考占位），
      //   后续 token/verdict 事件和原来完全一致。
      const fd = new FormData();
      fd.append("file", item.file);
      fd.append("student_id", STATE.currentStudent.id);
      // 不传 last_hash，确保每次都执行检测+批改

      let doneCnt = 0;
      await streamSSE("/api/realtime_stream", fd, (ev) => {
        if (ev.type === "detected") {
          // 检测完成：立刻出卡片骨架，不再等批改
          item.questions = ev.questions || [];
          item.results = new Array(item.questions.length).fill(null);
          if (queue.indexOf(item) === currentIdx) renderCurrent();
          if (!item.questions.length) return;
          setBatchStatus(`(${pageDone + 1}/${pending.length}) 批改中 0/${item.questions.length}`, "info");
        } else if (ev.type === "token") {
          if (queue.indexOf(item) === currentIdx) {
            appendThinkToken("#grade-list", ev.q_idx, ev.delta || "");
          }
        } else if (ev.type === "thinking_done") {
          if (queue.indexOf(item) === currentIdx) {
            markThinkingDone("#grade-list", ev.q_idx);
          }
        } else if (ev.type === "verdict") {
          const qi = (ev.q_idx !== undefined) ? ev.q_idx : ev.index;
          if (!item.results) return;
          item.results[qi] = ev.result;
          doneCnt++;
          if (queue.indexOf(item) === currentIdx) {
            updateGradeCard(item, qi);
            drawBoxes(item);
          }
          setBatchStatus(
            `(${pageDone + 1}/${pending.length}) 批改中 ${doneCnt}/${(item.questions || []).length}`,
            "info"
          );
        } else if (ev.type === "verdicts_done") {
          setBatchStatus(`(${pageDone + 1}/${pending.length}) 知识点抽取中...`, "info");
        } else if (ev.type === "enriching") {
          setBatchStatus(`(${pageDone + 1}/${pending.length}) 知识点抽取中...`, "info");
        } else if (ev.type === "enriched") {
          (ev.items || []).forEach(x => {
            if (!item.questions) return;
            const idx = item.questions.findIndex(q => q.index === x.index);
            if (idx >= 0 && item.results[idx]) {
              item.results[idx].knowledge_points = x.knowledge_points || [];
              item.results[idx].error_categories = x.error_categories || [];
              if (queue.indexOf(item) === currentIdx) updateGradeCard(item, idx);
            }
          });
        } else if (ev.type === "enrich_timeout") {
          // 静默
        } else if (ev.type === "error") {
          showToast("批改出错: " + ev.message, 3000);
        }
      });
      item.status = "done";
      pageDone++;
      if (queue.indexOf(item) === currentIdx) renderCurrent();
      renderQueue();
    } catch (e) {
      item.status = "error";
      setBatchStatus("批改失败: " + e.message, "error");
    }
  }
  setBatchStatus(`✓ 完成 ${pageDone}/${pending.length}`, "done");
  if (currentIdx < 0 && queue.length) currentIdx = 0;
  renderCurrent(); renderQueue();
});

// 只重绘某一题的卡片（避免整页重绘抖动）
function updateGradeCard(item, qIdx) {
  const list = $("#grade-list");
  const cards = list.querySelectorAll(".grade-card");
  if (!cards[qIdx]) { renderGradeList(list, item); return; }
  const newCard = renderQuestionCard(item.questions[qIdx], item.results[qIdx]);
  cards[qIdx].replaceWith(newCard);
}

// v5：往第 qIdx 题的"AI 推导中"活体区追加 token
function appendThinkToken(listSelector, qIdx, delta) {
  const list = $(listSelector);
  if (!list) return;
  const card = list.querySelectorAll(".grade-card")[qIdx];
  if (!card) return;
  const live = card.querySelector(".think-panel.think-live .tp-stream");
  if (!live) return;
  // 纯文本追加（不做 HTML，避免 <script> 等）
  const safe = delta
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/\n/g, "<br/>");
  live.insertAdjacentHTML("beforeend", safe);
  // 自动滚动让最新内容可见
  live.scrollTop = live.scrollHeight;
}

// v5：思路流结束 —— 把活体区变成"准备中"，等 verdict 到达后再 replace 整卡
function markThinkingDone(listSelector, qIdx) {
  const list = $(listSelector);
  if (!list) return;
  const card = list.querySelectorAll(".grade-card")[qIdx];
  if (!card) return;
  const live = card.querySelector(".think-panel.think-live");
  if (!live) return;
  live.classList.add("think-finalizing");
  const head = live.querySelector(".tp-head");
  if (head) head.innerHTML = '<span class="tp-icon">✍️</span>整理答案中<span class="tp-dots"><i>.</i><i>.</i><i>.</i></span>';
}

$("#btn-prev").addEventListener("click", () => {
  if (currentIdx > 0) { currentIdx--; renderCurrent(); renderQueue(); }
});
$("#btn-next").addEventListener("click", () => {
  if (currentIdx < queue.length - 1) { currentIdx++; renderCurrent(); renderQueue(); }
});
window.addEventListener("resize", () => {
  if (currentIdx >= 0) {
    const item = queue[currentIdx];
    if (item) drawBoxes(item);
  }
});

// ============================================================
//                      实时批改模式
// ============================================================
let rtStream = null, rtTimer = null, rtBusy = false, rtLastHash = null;
const RT_INTERVAL = 3500;

$("#btn-cam-start").addEventListener("click", startCamera);
$("#btn-cam-stop").addEventListener("click", stopCamera);

async function startCamera() {
  if (!STATE.currentStudent) return showToast("请先选择学生");
  try {
    const facingMode = $("#cam-facing").checked ? "environment" : "user";
    rtStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode }, audio: false });
    const v = $("#video");
    v.srcObject = rtStream;
    await v.play();
    $("#btn-cam-start").disabled = true;
    $("#btn-cam-stop").disabled = false;
    $("#rt-status").textContent = "已开启，等待首次分析...";
    rtLastHash = null;
    clearInterval(rtTimer);
    rtTimer = setInterval(rtTick, RT_INTERVAL);
    rtTick();
  } catch (e) {
    showToast("摄像头启动失败: " + e.message, 3000);
  }
}

function stopCamera() {
  if (rtTimer) { clearInterval(rtTimer); rtTimer = null; }
  if (rtStream) { rtStream.getTracks().forEach(t => t.stop()); rtStream = null; }
  const v = $("#video");
  if (v) v.srcObject = null;
  $("#btn-cam-start").disabled = false;
  $("#btn-cam-stop").disabled = true;
  rtBusy = false;
  const ov = $("#overlay");
  if (ov && ov.getContext) {
    const ctx = ov.getContext("2d");
    ctx.clearRect(0, 0, ov.width, ov.height);
  }
}

async function rtTick() {
  if (rtBusy || !rtStream) return;
  rtBusy = true;
  try {
    const v = $("#video");
    if (!v.videoWidth) { rtBusy = false; return; }
    const cv = document.createElement("canvas");
    cv.width = v.videoWidth; cv.height = v.videoHeight;
    cv.getContext("2d").drawImage(v, 0, 0);
    const blob = await new Promise(r => cv.toBlob(r, "image/jpeg", 0.8));
    const fd = new FormData();
    fd.append("file", blob, "frame.jpg");
    if (rtLastHash) fd.append("last_hash", rtLastHash);
    if (STATE.currentStudent) fd.append("student_id", STATE.currentStudent.id);

    $("#rt-status").textContent = "分析中...";
    let rtQuestions = [];
    let rtResults = [];

    await streamSSE("/api/realtime_stream", fd, (ev) => {
      if (ev.type === "hash") {
        rtLastHash = ev.hash;
        if (!ev.changed) $("#rt-status").textContent = "画面未变化";
      } else if (ev.type === "detected") {
        rtQuestions = ev.questions || [];
        rtResults = new Array(rtQuestions.length).fill(null);
        drawOverlayBoxes(rtQuestions, rtResults);
        renderRealtimeList(rtQuestions, rtResults);
        $("#rt-status").textContent = rtQuestions.length
          ? `识别 ${rtQuestions.length} 题，批改中...`
          : "未识别到题目";
      } else if (ev.type === "token") {
        // v5：流式思路
        appendThinkToken("#rt-grade-list", ev.q_idx, ev.delta || "");
      } else if (ev.type === "thinking_done") {
        markThinkingDone("#rt-grade-list", ev.q_idx);
      } else if (ev.type === "verdict") {
        const qi = (ev.q_idx !== undefined) ? ev.q_idx : ev.index;
        rtResults[qi] = ev.result;
        drawOverlayBoxes(rtQuestions, rtResults);
        const list = $("#rt-grade-list");
        const cards = list.querySelectorAll(".grade-card");
        if (cards[qi]) {
          cards[qi].replaceWith(
            renderQuestionCard(rtQuestions[qi], ev.result)
          );
        } else {
          renderRealtimeList(rtQuestions, rtResults);
        }
      } else if (ev.type === "verdicts_done") {
        $("#rt-status").textContent = "整理中...";
      } else if (ev.type === "enriching") {
        $("#rt-status").textContent = "抽取知识点...";
      } else if (ev.type === "enriched") {
        (ev.items || []).forEach(x => {
          const idx = rtQuestions.findIndex(q => q.index === x.index);
          if (idx >= 0 && rtResults[idx]) {
            rtResults[idx].knowledge_points = x.knowledge_points || [];
            rtResults[idx].error_categories = x.error_categories || [];
            const list = $("#rt-grade-list");
            const cards = list.querySelectorAll(".grade-card");
            if (cards[idx]) {
              cards[idx].replaceWith(
                renderQuestionCard(rtQuestions[idx], rtResults[idx])
              );
            }
          }
        });
        $("#rt-status").textContent = `完成：${rtQuestions.length} 题`;
      } else if (ev.type === "enrich_timeout") {
        $("#rt-status").textContent = `完成：${rtQuestions.length} 题`;
      } else if (ev.type === "done") {
        if ($("#rt-status").textContent.startsWith("识别")
            || $("#rt-status").textContent.startsWith("整理")
            || $("#rt-status").textContent.startsWith("抽取")) {
          $("#rt-status").textContent = `完成：${rtQuestions.length} 题`;
        }
      } else if (ev.type === "error") {
        $("#rt-status").textContent = "出错: " + ev.message;
      }
    });
  } catch (e) {
    $("#rt-status").textContent = "出错: " + e.message;
  } finally { rtBusy = false; }
}

function drawOverlayBoxes(questions, results) {
  const video = $("#video");
  const ov = $("#overlay");
  ov.width = video.clientWidth;
  ov.height = video.clientHeight;
  const ctx = ov.getContext("2d");
  ctx.clearRect(0, 0, ov.width, ov.height);
  questions.forEach((q, i) => {
    const [x1, y1, x2, y2] = q.bbox;
    const r = results[i];
    let color = "#38bdf8";
    if (r?.is_correct === true) color = "#22c55e";
    if (r?.is_correct === false) color = "#ef4444";
    const X = x1 / 1000 * ov.width;
    const Y = y1 / 1000 * ov.height;
    const W = (x2 - x1) / 1000 * ov.width;
    const H = (y2 - y1) / 1000 * ov.height;
    ctx.strokeStyle = color; ctx.lineWidth = 3;
    ctx.strokeRect(X, Y, W, H);
    ctx.fillStyle = color;
    const tag = "#" + q.index + (typeBadge(q.type) ? " " + typeBadge(q.type) : "");
    ctx.fillRect(X, Y - 18, Math.max(40, tag.length * 9), 18);
    ctx.fillStyle = "#fff";
    ctx.font = "12px sans-serif";
    ctx.fillText(tag, X + 4, Y - 4);
  });
}

function renderRealtimeList(questions, results) {
  const list = $("#rt-grade-list");
  if (!questions.length) { list.innerHTML = '<div class="placeholder">未识别到题目</div>'; return; }
  list.innerHTML = "";
  questions.forEach((q, i) => list.appendChild(renderQuestionCard(q, results[i])));
}

// ============================================================
//                          错题本
// ============================================================
async function refreshErrors() {
  const s = STATE.currentStudent;
  if (!s) return;
  const hide = $("#eb-hide-mastered").checked;
  const list = $("#eb-list");
  list.innerHTML = '<div class="placeholder">加载中...</div>';
  try {
    const r = await apiGet(`/api/students/${s.id}/errors?include_mastered=${!hide}`);
    renderErrorSummary(r.summary || {});
    if (!r.items.length) {
      list.innerHTML = '<div class="placeholder">暂无错题 🎉</div>';
      return;
    }
    list.innerHTML = "";
    r.items.forEach(it => list.appendChild(renderErrorCard(it)));
  } catch (e) {
    list.innerHTML = `<div class="placeholder">加载失败: ${escapeHtml(e.message)}</div>`;
  }
}

function renderErrorSummary(sum) {
  const box = $("#eb-summary");
  const kps = sum.frequent_knowledge_points || [];
  const cats = sum.frequent_error_categories || [];
  const types = sum.frequent_question_types || [];
  const typeLabel = { multiple_choice: "选择题", fill_blank: "填空题", normal: "解答题" };
  if (!kps.length && !cats.length && !types.length) { box.innerHTML = ""; return; }
  box.innerHTML = `
    ${kps.length ? `<div class="sum-row"><span class="sum-label">高频知识点：</span>${
      kps.map(x => `<span class="tag tag-kp">${escapeHtml(x.name)} ×${x.count}</span>`).join("")
    }</div>` : ""}
    ${cats.length ? `<div class="sum-row"><span class="sum-label">错因分布：</span>${
      cats.map(x => `<span class="tag tag-cat">${escapeHtml(x.category)} ×${x.count}</span>`).join("")
    }</div>` : ""}
    ${types.length ? `<div class="sum-row"><span class="sum-label">题型分布：</span>${
      types.map(x => `<span class="tag">${escapeHtml(typeLabel[x.type] || x.type)} ×${x.count}</span>`).join("")
    }</div>` : ""}
  `;
}

function renderErrorCard(it) {
  const div = document.createElement("div");
  div.className = "error-card" + (it.mastered ? " mastered" : "");
  const kpHtml = (it.knowledge_points || []).map(k => `<span class="tag tag-kp">${escapeHtml(k)}</span>`).join("");
  const catHtml = (it.error_categories || []).map(c => `<span class="tag tag-cat">${escapeHtml(c)}</span>`).join("");
  div.innerHTML = `
    <div class="err-head">
      <span class="err-time">${escapeHtml(it.created_at || "")}</span>
      <span class="err-type">${escapeHtml(typeName(it.question_type))}</span>
      ${it.mastered ? '<span class="err-tag ok">已掌握</span>' : '<span class="err-tag bad">错题</span>'}
    </div>
    <div class="err-q">${escapeHtml(it.question_text)}</div>
    <div class="err-row"><strong>学生答:</strong> ${escapeHtml(it.student_answer || "（空）")}</div>
    <div class="err-row"><strong>正确答:</strong> ${escapeHtml(it.correct_answer || "-")}</div>
    ${it.error_reason ? `<div class="err-row"><strong>错因:</strong> ${escapeHtml(it.error_reason)}</div>` : ""}
    ${it.explanation ? `<div class="err-row"><strong>解析:</strong> ${escapeHtml(it.explanation)}</div>` : ""}
    ${(kpHtml || catHtml) ? `<div class="err-tags">${kpHtml}${catHtml}</div>` : ""}
    <div class="err-actions">
      <button class="btn btn-sm btn-primary" data-gen="${it.id}">✍️ 生成类似题</button>
      <button class="btn btn-sm" data-mastered="${it.id}" data-cur="${it.mastered ? 1 : 0}">
        ${it.mastered ? "取消已掌握" : "标为已掌握"}
      </button>
      <button class="btn btn-sm btn-ghost" data-del="${it.id}">删除</button>
    </div>`;
  div.querySelector("[data-gen]").addEventListener("click", () => openErrModal(it));
  div.querySelector("[data-mastered]").addEventListener("click", async (e) => {
    const mastered = e.target.dataset.cur === "0";
    await apiJson(`/api/students/${STATE.currentStudent.id}/errors/${it.id}/mastered`,
      "PUT", { mastered });
    refreshErrors();
  });
  div.querySelector("[data-del]").addEventListener("click", async () => {
    if (!confirm("确认删除此错题？")) return;
    await fetch(`/api/students/${STATE.currentStudent.id}/errors/${it.id}`, { method: "DELETE" });
    refreshErrors();
  });
  return div;
}

$("#btn-eb-refresh").addEventListener("click", refreshErrors);
$("#eb-hide-mastered").addEventListener("change", refreshErrors);
$("#btn-eb-clear").addEventListener("click", async () => {
  if (!confirm("确认清空整个错题本？")) return;
  await fetch(`/api/students/${STATE.currentStudent.id}/errors`, { method: "DELETE" });
  refreshErrors();
});

// --- 生成类似题弹窗 ---
let errModalTarget = null;
function openErrModal(err) {
  errModalTarget = err;
  $("#err-preview").innerHTML = `
    <div class="err-preview-q"><strong>原题:</strong> ${escapeHtml(err.question_text)}</div>
    <div class="err-preview-kp">${(err.knowledge_points || [])
      .map(k => `<span class="tag tag-kp">${escapeHtml(k)}</span>`).join("")}</div>`;
  $("#err-modal").classList.remove("hidden");
}
function closeErrModal() { $("#err-modal").classList.add("hidden"); errModalTarget = null; }
$("#err-close").addEventListener("click", closeErrModal);
$("#err-cancel").addEventListener("click", closeErrModal);
$("#err-gen").addEventListener("click", async () => {
  if (!errModalTarget) return;
  const count = +$("#err-count").value || 3;
  const difficulty = $("#err-difficulty").value || "same";
  try {
    showLoading("AI 正在命题中...");
    await apiJson(`/api/students/${STATE.currentStudent.id}/practice/from_error`, "POST", {
      error_id: errModalTarget.id, count, difficulty,
    });
    closeErrModal();
    showToast("已生成，进入「练习生成」查看");
    goto("practice");
  } catch (e) {
    showToast("生成失败: " + e.message, 3000);
  } finally { hideLoading(); }
});

// ============================================================
//                        知识图谱
// ============================================================
let kgData = null;

async function refreshKnowledge() {
  const s = STATE.currentStudent;
  if (!s) return;
  try {
    const r = await apiGet(`/api/students/${s.id}/knowledge`);
    kgData = r.graph || { nodes: [], edges: [] };
    renderKnowledgeGraph(kgData);
    renderWeakPoints(r.weak_points || []);
  } catch (e) {
    showToast("加载知识图谱失败: " + e.message, 3000);
  }
}

$("#btn-kg-refresh").addEventListener("click", refreshKnowledge);
$("#kg-show-labels").addEventListener("change", () => {
  if (kgData) renderKnowledgeGraph(kgData);
});

function masteryColor(m) {
  if (m >= 85) return "#22c55e";  // great
  if (m >= 70) return "#84cc16";  // good
  if (m >= 50) return "#eab308";  // mid
  return "#ef4444";               // weak
}

function renderKnowledgeGraph(g) {
  const svg = $("#kg-svg");
  const stage = $("#kg-stage");
  const W = stage.clientWidth;
  const H = Math.max(420, stage.clientHeight);
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", W);
  svg.setAttribute("height", H);
  svg.innerHTML = "";
  if (!g.nodes.length) {
    svg.innerHTML = `<text x="${W/2}" y="${H/2}" text-anchor="middle" fill="#94a3b8" font-size="14">
      还没有知识点。批改一些题目后就会自动生成～
    </text>`;
    return;
  }

  // 构建 node map 并做简单力导向布局
  const nodes = g.nodes.map((n, i) => ({
    ...n,
    x: W/2 + Math.cos(i / g.nodes.length * Math.PI * 2) * Math.min(W, H) / 3 + (Math.random() - 0.5) * 30,
    y: H/2 + Math.sin(i / g.nodes.length * Math.PI * 2) * Math.min(W, H) / 3 + (Math.random() - 0.5) * 30,
    vx: 0, vy: 0,
  }));
  const idMap = Object.fromEntries(nodes.map(n => [n.id, n]));
  const edges = g.edges
    .map(e => ({ source: idMap[e.source], target: idMap[e.target], type: e.type }))
    .filter(e => e.source && e.target);

  // 简单力导向迭代
  const K_REP  = 4000;
  const K_ATR  = 0.02;
  const K_CEN  = 0.01;
  const DAMP   = 0.82;
  const DESIRED = 110;
  const ITERS = 260;

  for (let iter = 0; iter < ITERS; iter++) {
    // 斥力
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx*dx + dy*dy; if (d2 < 1) d2 = 1;
        const f = K_REP / d2;
        const d = Math.sqrt(d2);
        a.vx += (dx / d) * f; a.vy += (dy / d) * f;
        b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
      }
    }
    // 引力（边）
    edges.forEach(e => {
      const a = e.source, b = e.target;
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx*dx + dy*dy) || 1;
      const delta = d - DESIRED;
      const f = K_ATR * delta;
      a.vx += (dx / d) * f; a.vy += (dy / d) * f;
      b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
    });
    // 中心吸引
    nodes.forEach(n => {
      n.vx += (W/2 - n.x) * K_CEN;
      n.vy += (H/2 - n.y) * K_CEN;
    });
    // 应用
    nodes.forEach(n => {
      n.vx *= DAMP; n.vy *= DAMP;
      n.x += n.vx; n.y += n.vy;
      n.x = Math.max(40, Math.min(W - 40, n.x));
      n.y = Math.max(40, Math.min(H - 40, n.y));
    });
  }

  // 画边
  let svgHtml = "";
  edges.forEach(e => {
    svgHtml += `<line x1="${e.source.x}" y1="${e.source.y}" x2="${e.target.x}" y2="${e.target.y}" stroke="#334155" stroke-width="1.5" opacity="0.5" />`;
  });
  // 画节点
  const showLabels = $("#kg-show-labels").checked;
  nodes.forEach(n => {
    const r = 14 + Math.min(18, (n.total || 0) * 1.2);
    const color = masteryColor(n.mastery);
    svgHtml += `<g class="kg-node" data-name="${escapeHtml(n.name)}" style="cursor:pointer">
      <circle cx="${n.x}" cy="${n.y}" r="${r}" fill="${color}" fill-opacity="0.25" stroke="${color}" stroke-width="2" />
      <text x="${n.x}" y="${n.y + 4}" text-anchor="middle" fill="#fff" font-size="11" font-weight="600">${Math.round(n.mastery)}</text>
      ${showLabels ? `<text x="${n.x}" y="${n.y + r + 14}" text-anchor="middle" fill="#cbd5e1" font-size="12">${escapeHtml(n.name.length > 10 ? n.name.slice(0, 10) + "…" : n.name)}</text>` : ""}
    </g>`;
  });
  svg.innerHTML = svgHtml;

  // 点击弹窗
  svg.querySelectorAll(".kg-node").forEach(el => {
    el.addEventListener("click", () => openKpModal(el.dataset.name));
  });
}

function renderWeakPoints(list) {
  const box = $("#kg-weak");
  if (!list.length) { box.innerHTML = '<div class="placeholder">暂无</div>'; return; }
  box.innerHTML = "";
  list.forEach(p => {
    const row = document.createElement("div");
    row.className = "kg-weak-row";
    row.innerHTML = `
      <div class="kw-top">
        <span class="kw-name">${escapeHtml(p.name)}</span>
        <span class="kw-score" style="color:${masteryColor(p.mastery)}">${p.mastery}</span>
      </div>
      <div class="kw-sub">对 ${p.correct} · 错 ${p.wrong}</div>`;
    row.addEventListener("click", () => openKpModal(p.name));
    box.appendChild(row);
  });
}

// --- 知识点讲解弹窗 ---
let currentKp = null;
async function openKpModal(name) {
  currentKp = name;
  $("#kp-title").textContent = name;
  // 填统计
  const node = (kgData?.nodes || []).find(n => n.name === name);
  if (node) {
    $("#kp-stats").innerHTML = `
      <span class="tag" style="background:${masteryColor(node.mastery)}22;color:${masteryColor(node.mastery)};border:1px solid ${masteryColor(node.mastery)}">掌握度 ${node.mastery}/100</span>
      <span class="tag">做对 ${node.correct}</span>
      <span class="tag">做错 ${node.wrong}</span>`;
  } else $("#kp-stats").innerHTML = "";
  $("#kp-explain").innerHTML = '<div class="placeholder">AI 讲解加载中...</div>';
  $("#kp-modal").classList.remove("hidden");
  await loadKpExplain(name);
}

async function loadKpExplain(name) {
  try {
    const r = await apiGet(`/api/students/${STATE.currentStudent.id}/knowledge/${encodeURIComponent(name)}/explain`);
    $("#kp-explain").innerHTML = renderMarkdown(r.markdown || "");
  } catch (e) {
    $("#kp-explain").innerHTML = `<div class="placeholder">加载失败: ${escapeHtml(e.message)}</div>`;
  }
}

$("#kp-close").addEventListener("click", () => $("#kp-modal").classList.add("hidden"));
$("#kp-regen").addEventListener("click", () => {
  if (currentKp) {
    $("#kp-explain").innerHTML = '<div class="placeholder">重新生成中...</div>';
    loadKpExplain(currentKp);
  }
});
$("#kp-practice").addEventListener("click", async () => {
  if (!currentKp) return;
  try {
    showLoading("AI 正在命题...");
    await apiJson(`/api/students/${STATE.currentStudent.id}/practice/from_kp`, "POST", {
      point: currentKp, count: 3, difficulty: "same", qtype: "normal",
    });
    $("#kp-modal").classList.add("hidden");
    showToast("已生成");
    goto("practice");
  } catch (e) {
    showToast("生成失败: " + e.message, 3000);
  } finally { hideLoading(); }
});

// ============================================================
//                       能力雷达
// ============================================================
async function refreshAbility() {
  const s = STATE.currentStudent;
  if (!s) return;
  try {
    const r = await apiGet(`/api/students/${s.id}/ability`);
    renderRadar(r.radar);
  } catch (e) {
    showToast("加载失败: " + e.message, 3000);
  }
}

function renderRadar(rd) {
  const dims = rd.dimensions || [];
  const svg = $("#ab-radar");
  const CX = 200, CY = 200, R = 140;
  const N = dims.length || 1;
  svg.innerHTML = "";

  // 网格层（4 圈）
  let grid = "";
  for (let k = 1; k <= 4; k++) {
    const rr = (R / 4) * k;
    const pts = [];
    for (let i = 0; i < N; i++) {
      const a = -Math.PI / 2 + (i / N) * Math.PI * 2;
      pts.push(`${CX + Math.cos(a) * rr},${CY + Math.sin(a) * rr}`);
    }
    grid += `<polygon points="${pts.join(" ")}" fill="none" stroke="#334155" stroke-width="1" />`;
  }
  // 轴线
  let axes = "";
  for (let i = 0; i < N; i++) {
    const a = -Math.PI / 2 + (i / N) * Math.PI * 2;
    axes += `<line x1="${CX}" y1="${CY}" x2="${CX + Math.cos(a) * R}" y2="${CY + Math.sin(a) * R}" stroke="#334155" stroke-width="1" />`;
  }
  // 数据多边形
  const dataPts = [];
  dims.forEach((d, i) => {
    const a = -Math.PI / 2 + (i / N) * Math.PI * 2;
    const rr = R * (Math.max(0, Math.min(100, d.score)) / 100);
    dataPts.push(`${CX + Math.cos(a) * rr},${CY + Math.sin(a) * rr}`);
  });
  const dataPoly = `<polygon points="${dataPts.join(" ")}" fill="#38bdf8" fill-opacity="0.25" stroke="#38bdf8" stroke-width="2" />`;
  // 数据点
  let dots = "";
  dims.forEach((d, i) => {
    const a = -Math.PI / 2 + (i / N) * Math.PI * 2;
    const rr = R * (Math.max(0, Math.min(100, d.score)) / 100);
    dots += `<circle cx="${CX + Math.cos(a) * rr}" cy="${CY + Math.sin(a) * rr}" r="3.5" fill="#38bdf8" />`;
  });
  // 维度标签
  let labels = "";
  dims.forEach((d, i) => {
    const a = -Math.PI / 2 + (i / N) * Math.PI * 2;
    const lx = CX + Math.cos(a) * (R + 22);
    const ly = CY + Math.sin(a) * (R + 22) + 4;
    const anchor = Math.abs(Math.cos(a)) < 0.3 ? "middle" : (Math.cos(a) > 0 ? "start" : "end");
    labels += `<text x="${lx}" y="${ly}" text-anchor="${anchor}" fill="#f1f5f9" font-size="12">${escapeHtml(d.name)}</text>`;
    labels += `<text x="${lx}" y="${ly + 14}" text-anchor="${anchor}" fill="#94a3b8" font-size="11">${d.score}</text>`;
  });
  svg.innerHTML = grid + axes + dataPoly + dots + labels;

  // 图例
  const legend = $("#ab-radar-legend");
  legend.innerHTML = dims.map(d => `
    <div class="ab-legend-row">
      <span>${escapeHtml(d.name)}</span>
      <span class="ab-score">${d.score}</span>
      <span class="hint">对 ${d.correct} · 错 ${d.wrong}</span>
    </div>`).join("");

  // 已有建议
  if (rd.advice) {
    $("#ab-advice-body").innerHTML = renderMarkdown(rd.advice);
    $("#ab-advice-time").textContent = rd.advice_at ? `更新于 ${rd.advice_at}` : "";
  } else {
    $("#ab-advice-body").innerHTML = '<div class="placeholder">点击「生成改进建议」让 AI 分析你的画像</div>';
    $("#ab-advice-time").textContent = "";
  }
}

$("#btn-ab-refresh").addEventListener("click", refreshAbility);
$("#btn-ab-advice").addEventListener("click", async () => {
  try {
    showLoading("AI 分析中...（可能需要几秒）");
    const r = await apiJson(`/api/students/${STATE.currentStudent.id}/ability/advice`, "POST");
    $("#ab-advice-body").innerHTML = renderMarkdown(r.markdown || "");
    $("#ab-advice-time").textContent = "刚刚生成";
    showToast("已生成");
  } catch (e) {
    showToast("生成失败: " + e.message, 3000);
  } finally { hideLoading(); }
});
$("#btn-ab-reset").addEventListener("click", async () => {
  if (!confirm("确认重置能力雷达？（只影响雷达统计，不影响错题本和知识图谱）")) return;
  await fetch(`/api/students/${STATE.currentStudent.id}/ability`, { method: "DELETE" });
  refreshAbility();
});

// ============================================================
//                      练习生成
// ============================================================
async function refreshPractice() {
  const s = STATE.currentStudent;
  if (!s) return;
  const list = $("#practice-list");
  list.innerHTML = '<div class="placeholder">加载中...</div>';
  try {
    const r = await apiGet(`/api/students/${s.id}/practice`);
    if (!r.items.length) {
      list.innerHTML = '<div class="placeholder">还没有生成过练习。可以在上方选知识点，或在错题本里选错题生成。</div>';
      return;
    }
    list.innerHTML = "";
    r.items.forEach(b => list.appendChild(renderPracticeBatch(b)));
  } catch (e) {
    list.innerHTML = `<div class="placeholder">加载失败: ${escapeHtml(e.message)}</div>`;
  }
}

function renderPracticeBatch(b) {
  const kps = (b.source_knowledge_points || []).map(k => `<span class="tag tag-kp">${escapeHtml(k)}</span>`).join("");
  const diff = { easy: "简单", same: "相当", hard: "偏难" }[b.difficulty] || b.difficulty;
  const div = document.createElement("div");
  div.className = "practice-batch";
  const itemsHtml = (b.items || []).map((it, i) => {
    const optsHtml = (it.options || []).length
      ? `<div class="pb-options">${(it.options).map(o =>
          `<div class="pb-opt"><span class="pb-lbl">${escapeHtml(o.label || "")}.</span> ${escapeHtml(o.text || "")}</div>`
        ).join("")}</div>`
      : "";
    return `
      <div class="pb-item">
        <div class="pb-idx">第 ${i + 1} 题</div>
        <div class="pb-q">${escapeHtml(it.question || "")}</div>
        ${optsHtml}
        <details class="pb-details">
          <summary>查看答案与解析</summary>
          <div class="pb-ans"><strong>答案:</strong> ${escapeHtml(it.answer || "-")}</div>
          <div class="pb-exp"><strong>解析:</strong> ${escapeHtml(it.explanation || "-")}</div>
        </details>
      </div>`;
  }).join("");
  const failHint = (!b.items || !b.items.length)
    ? `<div class="placeholder">解析失败。原始输出：${escapeHtml((b.raw || "").slice(0, 300))}</div>`
    : "";
  div.innerHTML = `
    <div class="pb-head">
      <div>
        <div class="pb-meta">${escapeHtml(b.created_at || "")} · ${b.count} 题 · 难度 ${escapeHtml(diff)}</div>
        <div class="pb-src">原题: ${escapeHtml((b.source_base_question || "").slice(0, 80))}</div>
        <div class="pb-kps">${kps}</div>
      </div>
      <button class="btn btn-sm btn-ghost" data-del="${b.id}">删除</button>
    </div>
    ${itemsHtml || failHint}`;
  div.querySelector("[data-del]").addEventListener("click", async () => {
    if (!confirm("删除这组练习？")) return;
    await fetch(`/api/students/${STATE.currentStudent.id}/practice/${b.id}`, { method: "DELETE" });
    refreshPractice();
  });
  return div;
}

$("#btn-pf-gen").addEventListener("click", async () => {
  const kp = $("#pf-kp").value.trim();
  if (!kp) return showToast("请填写知识点");
  try {
    showLoading("AI 正在命题...");
    await apiJson(`/api/students/${STATE.currentStudent.id}/practice/from_kp`, "POST", {
      point: kp,
      qtype: $("#pf-qtype").value,
      difficulty: $("#pf-difficulty").value,
      count: +$("#pf-count").value || 3,
    });
    $("#pf-kp").value = "";
    showToast("已生成");
    refreshPractice();
  } catch (e) {
    showToast("生成失败: " + e.message, 3000);
  } finally { hideLoading(); }
});

$("#btn-pf-goto-errors").addEventListener("click", () => goto("errors"));

// ============================================================
//                      题库管理（按学生）
// ============================================================
$$(".tab").forEach(t => t.addEventListener("click", () => {
  $$(".tab").forEach(x => x.classList.remove("active"));
  $$(".tab-panel").forEach(x => x.classList.remove("active"));
  t.classList.add("active");
  $("#tab-" + t.dataset.tab).classList.add("active");
}));

async function refreshBanks() {
  await Promise.all([refreshShort(), refreshLong()]);
}
async function refreshShort() {
  const s = STATE.currentStudent; if (!s) return;
  try {
    const r = await apiGet(`/api/students/${s.id}/short_term`);
    renderBankList($("#bank-list-short"), r.items, "short");
  } catch { showToast("短期题库加载失败"); }
}
async function refreshLong() {
  const s = STATE.currentStudent; if (!s) return;
  try {
    const r = await apiGet(`/api/students/${s.id}/rag`);
    renderBankList($("#bank-list-long"), r.items, "long");
  } catch { showToast("长期题库加载失败"); }
}

function renderBankList(el, items, type) {
  if (!items?.length) { el.innerHTML = '<div class="placeholder">暂无内容</div>'; return; }
  el.innerHTML = "";
  items.forEach(it => {
    const div = document.createElement("div");
    div.className = "bank-item";
    const scopeTag = it.scope === "global" ? '<span class="bi-scope global">全局</span>' : "";
    div.innerHTML = `
      <div class="bi-head">
        <span class="bi-meta">${scopeTag}${escapeHtml(it.created_at || "")} · ${escapeHtml(it.id)}</span>
        <button class="btn btn-sm btn-ghost" data-del="${it.id}">删除</button>
      </div>
      <div class="bi-q">${escapeHtml(it.question)}</div>
      ${it.solution ? `<div class="bi-s">${escapeHtml(it.solution)}</div>` : ""}
      ${it.note ? `<div class="bi-n">📝 ${escapeHtml(it.note)}</div>` : ""}`;
    div.querySelector("[data-del]").addEventListener("click", async () => {
      if (!confirm("确认删除此题？")) return;
      let url;
      const sid = STATE.currentStudent?.id;
      if (type === "short")      url = `/api/students/${sid}/short_term/${it.id}`;
      else if (type === "long")  url = `/api/students/${sid}/rag/${it.id}`;
      else if (type === "gshort") url = `/api/global/short_term/${it.id}`;
      else if (type === "glong")  url = `/api/global/rag/${it.id}`;
      await fetch(url, { method: "DELETE" });
      if (type === "short")      refreshShort();
      else if (type === "long")  refreshLong();
      else if (type === "gshort") refreshGShort();
      else if (type === "glong")  refreshGLong();
    });
    el.appendChild(div);
  });
}

$("#btn-st-add").addEventListener("click", async () => {
  if (!STATE.currentStudent) return showToast("请先选择学生");
  const q = $("#st-question").value.trim();
  if (!q) return showToast("题目不能为空");
  await apiJson(`/api/students/${STATE.currentStudent.id}/short_term`, "POST", {
    question: q,
    solution: $("#st-solution").value,
    note: $("#st-note").value,
  });
  $("#st-question").value = $("#st-solution").value = $("#st-note").value = "";
  refreshShort();
  showToast("已添加");
});

$("#btn-lt-add").addEventListener("click", async () => {
  if (!STATE.currentStudent) return showToast("请先选择学生");
  const q = $("#lt-question").value.trim();
  if (!q) return showToast("题目不能为空");
  showLoading("计算向量并入库...");
  try {
    await apiJson(`/api/students/${STATE.currentStudent.id}/rag`, "POST", {
      question: q,
      solution: $("#lt-solution").value,
      note: $("#lt-note").value,
    });
    $("#lt-question").value = $("#lt-solution").value = $("#lt-note").value = "";
    refreshLong();
    showToast("已添加到长期题库");
  } catch (e) {
    showToast("失败: " + e.message, 3000);
  } finally { hideLoading(); }
});

$("#btn-st-clear").addEventListener("click", async () => {
  if (!confirm("确认清空短期题库？")) return;
  await fetch(`/api/students/${STATE.currentStudent.id}/short_term`, { method: "DELETE" });
  refreshShort();
});
$("#btn-lt-clear").addEventListener("click", async () => {
  if (!confirm("确认清空长期题库？")) return;
  await fetch(`/api/students/${STATE.currentStudent.id}/rag`, { method: "DELETE" });
  refreshLong();
});

function bindPhotoAdd(btnId, inputId, prefix) {
  $(btnId).addEventListener("click", () => $(inputId).click());
  $(inputId).addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    showLoading("识别题目中...");
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await apiForm("/api/extract_for_bank", fd);
      const ex = r.extracted || {};
      $(`#${prefix}-question`).value = ex.question || "";
      $(`#${prefix}-solution`).value = ex.solution || "";
      $(`#${prefix}-note`).value = ex.note || "";
      showToast("已提取，检查后点「添加」");
    } catch (e) {
      showToast("提取失败: " + e.message, 3000);
    } finally { hideLoading(); }
  });
}
bindPhotoAdd("#btn-st-photo", "#st-photo-input", "st");
bindPhotoAdd("#btn-lt-photo", "#lt-photo-input", "lt");

// ============================================================
//                      全局题库（对所有学生生效）
// ============================================================
$$("[data-gtab]").forEach(t => t.addEventListener("click", () => {
  $$("[data-gtab]").forEach(x => x.classList.remove("active"));
  $$("#view-global .tab-panel").forEach(x => x.classList.remove("active"));
  t.classList.add("active");
  $("#gtab-" + t.dataset.gtab).classList.add("active");
}));

async function refreshGlobalBanks() {
  await Promise.all([refreshGShort(), refreshGLong()]);
}
async function refreshGShort() {
  try {
    const r = await apiGet("/api/global/short_term");
    renderBankList($("#bank-list-gshort"), r.items, "gshort");
  } catch { showToast("全局短期题库加载失败"); }
}
async function refreshGLong() {
  try {
    const r = await apiGet("/api/global/rag");
    renderBankList($("#bank-list-glong"), r.items, "glong");
  } catch { showToast("全局长期题库加载失败"); }
}

// 改造 renderBankList 以支持 scope 显示和全局删除路由
// （沿用之前的 renderBankList：现在它会按 type 决定 DELETE 路径）

$("#btn-gst-add").addEventListener("click", async () => {
  const q = $("#gst-question").value.trim();
  if (!q) return showToast("题目不能为空");
  await apiJson("/api/global/short_term", "POST", {
    question: q,
    solution: $("#gst-solution").value,
    note: $("#gst-note").value,
  });
  $("#gst-question").value = $("#gst-solution").value = $("#gst-note").value = "";
  refreshGShort();
  showToast("已添加到全局短期题库");
});

$("#btn-glt-add").addEventListener("click", async () => {
  const q = $("#glt-question").value.trim();
  if (!q) return showToast("题目不能为空");
  showLoading("计算向量并入库...");
  try {
    await apiJson("/api/global/rag", "POST", {
      question: q,
      solution: $("#glt-solution").value,
      note: $("#glt-note").value,
    });
    $("#glt-question").value = $("#glt-solution").value = $("#glt-note").value = "";
    refreshGLong();
    showToast("已添加到全局长期题库");
  } catch (e) {
    showToast("失败: " + e.message, 3000);
  } finally { hideLoading(); }
});

$("#btn-gst-clear").addEventListener("click", async () => {
  if (!confirm("确认清空【全局】短期题库？此操作影响所有学生。")) return;
  await fetch("/api/global/short_term", { method: "DELETE" });
  refreshGShort();
});
$("#btn-glt-clear").addEventListener("click", async () => {
  if (!confirm("确认清空【全局】长期题库？此操作影响所有学生。")) return;
  await fetch("/api/global/rag", { method: "DELETE" });
  refreshGLong();
});

bindPhotoAdd("#btn-gst-photo", "#gst-photo-input", "gst");
bindPhotoAdd("#btn-glt-photo", "#glt-photo-input", "glt");

// ============================================================
//                        教师面板 —— v4 新增
// ============================================================
let TEACHER_DATA = null;
let TEACHER_SORT = "accuracy";

async function refreshTeacher(force = false) {
  const meta = $("#teacher-meta");
  meta.textContent = "加载中...";
  try {
    const r = await apiGet("/api/teacher/overview" + (force ? "?refresh=1" : ""));
    TEACHER_DATA = r.data;
    renderTeacherAll();
  } catch (e) {
    meta.textContent = "加载失败: " + e.message;
  }
}

function renderTeacherAll() {
  if (!TEACHER_DATA) return;
  const d = TEACHER_DATA;
  const s = d.summary || {};
  $("#teacher-meta").innerHTML =
    `生成于 ${escapeHtml(d.generated_at || "")} · ` +
    `耗时 ${d.elapsed_ms || 0} ms ` +
    (d._cache_hit ? `· <span class="tag">缓存命中</span>` : "");

  // 顶部 Tile
  $("#tch-tiles").innerHTML = `
    <div class="tch-tile"><div class="tt-num">${s.total_students || 0}</div><div class="tt-label">学生总数</div></div>
    <div class="tch-tile"><div class="tt-num">${s.students_with_data || 0}</div><div class="tt-label">有数据</div></div>
    <div class="tch-tile"><div class="tt-num">${s.total_graded || 0}</div><div class="tt-label">累计批改</div></div>
    <div class="tch-tile"><div class="tt-num">${s.class_accuracy == null ? "-" : s.class_accuracy + "%"}</div><div class="tt-label">班级正确率</div></div>
    <div class="tch-tile"><div class="tt-num">${s.total_errors || 0}</div><div class="tt-label">未掌握错题</div></div>
  `;

  renderClassRadar(d.class_radar || []);
  renderBucketChart(d.accuracy_buckets || []);
  renderTrendChart(d.day_trend || {labels:[], graded:[], errors:[]});
  renderCatBars(d.error_categories || []);
  renderHeatmap(d.kp_heatmap || []);
  renderKPList($("#tch-weak"), d.weak_kps || [], "mastery-asc");
  renderKPList($("#tch-strong"), d.strong_kps || [], "mastery-desc");
  renderStudentList();

  // 已有建议暂不自动生成（由按钮触发）
}

// ---- 班级能力雷达 ----
function renderClassRadar(dims) {
  const svg = $("#tch-radar");
  const CX = 200, CY = 200, R = 140;
  const N = dims.length || 1;
  svg.innerHTML = "";
  // 网格
  let g = "";
  for (let k = 1; k <= 4; k++) {
    const rr = (R / 4) * k;
    const pts = [];
    for (let i = 0; i < N; i++) {
      const a = -Math.PI / 2 + (i / N) * Math.PI * 2;
      pts.push(`${CX + Math.cos(a) * rr},${CY + Math.sin(a) * rr}`);
    }
    g += `<polygon points="${pts.join(" ")}" fill="none" stroke="#334155" stroke-width="1" />`;
  }
  let axes = "";
  for (let i = 0; i < N; i++) {
    const a = -Math.PI / 2 + (i / N) * Math.PI * 2;
    axes += `<line x1="${CX}" y1="${CY}" x2="${CX + Math.cos(a) * R}" y2="${CY + Math.sin(a) * R}" stroke="#334155" stroke-width="1" />`;
  }
  // 平均
  const avgPts = dims.map((d, i) => {
    const a = -Math.PI / 2 + (i / N) * Math.PI * 2;
    const rr = R * (Math.max(0, Math.min(100, d.score)) / 100);
    return `${CX + Math.cos(a) * rr},${CY + Math.sin(a) * rr}`;
  }).join(" ");
  // 最低分学生 overlay
  const lowPts = dims.map((d, i) => {
    const a = -Math.PI / 2 + (i / N) * Math.PI * 2;
    const lowest = d.weakest_students?.[0]?.score;
    const rr = R * (Math.max(0, Math.min(100, lowest == null ? d.score : lowest)) / 100);
    return `${CX + Math.cos(a) * rr},${CY + Math.sin(a) * rr}`;
  }).join(" ");

  // 标签
  let labels = "";
  dims.forEach((d, i) => {
    const a = -Math.PI / 2 + (i / N) * Math.PI * 2;
    const lx = CX + Math.cos(a) * (R + 22);
    const ly = CY + Math.sin(a) * (R + 22) + 4;
    const anchor = Math.abs(Math.cos(a)) < 0.3 ? "middle" : (Math.cos(a) > 0 ? "start" : "end");
    labels += `<text x="${lx}" y="${ly}" text-anchor="${anchor}" fill="#f1f5f9" font-size="12">${escapeHtml(d.name)}</text>`;
    labels += `<text x="${lx}" y="${ly + 14}" text-anchor="${anchor}" fill="#94a3b8" font-size="11">${d.score}</text>`;
  });

  svg.innerHTML = g + axes +
    `<polygon points="${lowPts}" fill="#ef4444" fill-opacity="0.15" stroke="#ef4444" stroke-width="1.5" stroke-dasharray="4 3" />` +
    `<polygon points="${avgPts}" fill="#38bdf8" fill-opacity="0.28" stroke="#38bdf8" stroke-width="2" />` +
    labels +
    // 图例
    `<g><rect x="20" y="370" width="12" height="12" fill="#38bdf8" fill-opacity="0.28" stroke="#38bdf8" /><text x="38" y="380" fill="#cbd5e1" font-size="11">班级平均</text><rect x="110" y="370" width="12" height="12" fill="#ef4444" fill-opacity="0.15" stroke="#ef4444" stroke-dasharray="3 2" /><text x="128" y="380" fill="#cbd5e1" font-size="11">最低同学</text></g>`;
}

// ---- 正确率分布柱图 ----
function renderBucketChart(buckets) {
  const svg = $("#tch-bucket");
  svg.innerHTML = "";
  const W = 400, H = 260, PAD_L = 36, PAD_B = 36, PAD_T = 16, PAD_R = 12;
  const n = buckets.length || 1;
  const maxC = Math.max(1, ...buckets.map(b => b.count));
  const chartW = W - PAD_L - PAD_R;
  const chartH = H - PAD_T - PAD_B;
  const bw = chartW / n * 0.65;
  const gap = chartW / n * 0.35;
  let html = "";
  // 网格
  for (let k = 0; k <= 4; k++) {
    const y = PAD_T + chartH * (1 - k / 4);
    html += `<line x1="${PAD_L}" y1="${y}" x2="${W - PAD_R}" y2="${y}" stroke="#1e293b" stroke-width="1" />`;
    html += `<text x="${PAD_L - 6}" y="${y + 4}" text-anchor="end" fill="#94a3b8" font-size="10">${Math.round(maxC * k / 4)}</text>`;
  }
  buckets.forEach((b, i) => {
    const h = chartH * (b.count / maxC);
    const x = PAD_L + i * (chartW / n) + gap / 2;
    const y = PAD_T + chartH - h;
    const color = ["#ef4444", "#f59e0b", "#eab308", "#22c55e"][i] || "#38bdf8";
    html += `<rect x="${x}" y="${y}" width="${bw}" height="${h}" rx="4" fill="${color}" fill-opacity="0.75" stroke="${color}" />`;
    html += `<text x="${x + bw / 2}" y="${y - 4}" text-anchor="middle" fill="#f1f5f9" font-size="11" font-weight="600">${b.count}</text>`;
    html += `<text x="${x + bw / 2}" y="${H - PAD_B + 14}" text-anchor="middle" fill="#cbd5e1" font-size="11">${escapeHtml(b.label)}%</text>`;
  });
  svg.innerHTML = html;
}

// ---- 7 日趋势线图 ----
function renderTrendChart(t) {
  const svg = $("#tch-trend");
  svg.innerHTML = "";
  const labels = t.labels || [];
  const graded = t.graded || [];
  const errs = t.errors || [];
  const W = 500, H = 240, PAD_L = 36, PAD_B = 34, PAD_T = 16, PAD_R = 14;
  const n = labels.length || 1;
  const max = Math.max(1, ...graded, ...errs);
  const chartW = W - PAD_L - PAD_R;
  const chartH = H - PAD_T - PAD_B;

  let html = "";
  for (let k = 0; k <= 4; k++) {
    const y = PAD_T + chartH * (1 - k / 4);
    html += `<line x1="${PAD_L}" y1="${y}" x2="${W - PAD_R}" y2="${y}" stroke="#1e293b" stroke-width="1" />`;
    html += `<text x="${PAD_L - 6}" y="${y + 4}" text-anchor="end" fill="#94a3b8" font-size="10">${Math.round(max * k / 4)}</text>`;
  }
  const xs = (i) => PAD_L + (chartW / Math.max(1, n - 1)) * i;
  const ys = (v) => PAD_T + chartH * (1 - v / max);

  function path(series, color, fill) {
    let p = "";
    series.forEach((v, i) => { p += (i === 0 ? "M" : "L") + xs(i) + "," + ys(v) + " "; });
    const area = p + `L${xs(series.length - 1)},${PAD_T + chartH} L${xs(0)},${PAD_T + chartH} Z`;
    let out = `<path d="${area}" fill="${fill}" stroke="none" />`;
    out += `<path d="${p}" fill="none" stroke="${color}" stroke-width="2" />`;
    series.forEach((v, i) => {
      out += `<circle cx="${xs(i)}" cy="${ys(v)}" r="3" fill="${color}" />`;
    });
    return out;
  }
  html += path(graded, "#38bdf8", "rgba(56,189,248,0.12)");
  html += path(errs, "#ef4444", "rgba(239,68,68,0.12)");

  labels.forEach((lb, i) => {
    html += `<text x="${xs(i)}" y="${H - PAD_B + 14}" text-anchor="middle" fill="#cbd5e1" font-size="10">${escapeHtml(lb)}</text>`;
  });
  // 图例
  html += `<g><circle cx="${W - 150}" cy="${PAD_T + 6}" r="4" fill="#38bdf8" /><text x="${W - 140}" y="${PAD_T + 10}" fill="#cbd5e1" font-size="11">已批改</text><circle cx="${W - 80}" cy="${PAD_T + 6}" r="4" fill="#ef4444" /><text x="${W - 70}" y="${PAD_T + 10}" fill="#cbd5e1" font-size="11">错题</text></g>`;
  svg.innerHTML = html;
}

// ---- 错因横向条 ----
function renderCatBars(cats) {
  const box = $("#tch-cats");
  if (!cats.length) { box.innerHTML = '<div class="placeholder">暂无错因数据</div>'; return; }
  const max = Math.max(1, ...cats.map(c => c.count));
  box.innerHTML = cats.slice(0, 8).map(c => {
    const w = Math.round(100 * c.count / max);
    return `<div class="cat-row">
      <div class="cat-name">${escapeHtml(c.name)}</div>
      <div class="cat-bar"><div class="cat-bar-fill" style="width:${w}%"></div></div>
      <div class="cat-count">${c.count}</div>
    </div>`;
  }).join("");
}

// ---- 知识点热力图 ----
function renderHeatmap(kps) {
  const box = $("#tch-heatmap");
  if (!kps.length) { box.innerHTML = '<div class="placeholder">暂无知识点数据</div>'; return; }
  box.innerHTML = kps.map(k => {
    const m = k.mastery;
    const c = masteryColor(m);
    const size = Math.min(1, 0.55 + k.total / 40);
    return `<div class="hm-cell" style="background:${c}22;border:1px solid ${c};opacity:${size}" title="${escapeHtml(k.name)} · 掌握度 ${m} · 涉及 ${k.student_count} 名学生, 共 ${k.total} 次">
      <div class="hm-name">${escapeHtml(k.name.length > 8 ? k.name.slice(0, 8) + "…" : k.name)}</div>
      <div class="hm-score" style="color:${c}">${m}</div>
      <div class="hm-sub">${k.student_count}人 · ${k.total}次</div>
    </div>`;
  }).join("");
}

// ---- 知识点列表（薄弱 / 稳固） ----
function renderKPList(el, kps, _sortMode) {
  if (!kps.length) { el.innerHTML = '<div class="placeholder">暂无</div>'; return; }
  el.innerHTML = kps.map(k => `
    <div class="tl-row">
      <div class="tl-name">${escapeHtml(k.name)}</div>
      <div class="tl-right">
        <span class="tl-score" style="color:${masteryColor(k.mastery)}">${k.mastery}</span>
        <span class="tl-sub">${k.student_count}人</span>
      </div>
    </div>`).join("");
}

// ---- 学生列表 ----
function renderStudentList() {
  if (!TEACHER_DATA) return;
  const box = $("#tch-students");
  let list = [];
  if (TEACHER_SORT === "accuracy") {
    list = TEACHER_DATA.leaderboard_accuracy || [];
  } else if (TEACHER_SORT === "errors") {
    list = TEACHER_DATA.leaderboard_errors || [];
  } else {
    list = TEACHER_DATA.activity || [];
  }
  if (!list.length) { box.innerHTML = '<div class="placeholder">暂无学生</div>'; return; }
  box.innerHTML = list.map(s => {
    const av = (s.avatar || "").startsWith("/")
      ? `<img src="${s.avatar}" />` : `<span>${s.avatar || "🧑‍🎓"}</span>`;
    let metric = "";
    if (TEACHER_SORT === "accuracy") {
      metric = `<span class="ts-metric">正确率 <b>${s.accuracy == null ? "-" : s.accuracy + "%"}</b></span>
                <span class="ts-sub">已批 ${s.graded || 0} · 错题 ${s.errors || 0}</span>`;
    } else if (TEACHER_SORT === "errors") {
      metric = `<span class="ts-metric">错题 <b>${s.errors || 0}</b></span>
                <span class="ts-sub">已批 ${s.graded || 0} · 正确率 ${s.accuracy == null ? "-" : s.accuracy + "%"}</span>`;
    } else {
      metric = `<span class="ts-metric">近 7 日 <b>${s.recent_graded || 0}</b> 题</span>
                <span class="ts-sub">错题 ${s.recent_errors || 0}</span>`;
    }
    return `<div class="ts-row" data-sid="${s.id}">
      <div class="ts-av">${av}</div>
      <div class="ts-main">
        <div class="ts-name">${escapeHtml(s.name || "")}</div>
        ${metric}
      </div>
      <div class="ts-arrow">›</div>
    </div>`;
  }).join("");
  box.querySelectorAll(".ts-row").forEach(r => {
    r.addEventListener("click", () => openTeacherStudent(r.dataset.sid));
  });
}

// ---- 单学生详情 —— v5 改为全页 ----
let TEACHER_STUDENT_DATA = null;
let TEACHER_STUDENT_ID = null;
let TEACHER_STUDENT_TAB = "overview";

async function openTeacherStudent(sid) {
  TEACHER_STUDENT_ID = sid;
  TEACHER_STUDENT_DATA = null;
  TEACHER_STUDENT_TAB = "overview";
  goto("teacher-student");
}

async function refreshTeacherStudent() {
  const sid = TEACHER_STUDENT_ID;
  if (!sid) { goto("teacher"); return; }
  // 重置 UI
  $("#tsv-name").textContent = "加载中...";
  $("#tsv-sub").textContent = "";
  $("#tsv-stats").innerHTML = "";
  $("#tsv-history-list").innerHTML = '<div class="placeholder">加载中...</div>';
  $("#tsv-errors-list").innerHTML = '<div class="placeholder">加载中...</div>';
  $("#tsv-kp-list").innerHTML = '<div class="placeholder">加载中...</div>';
  $("#tsv-dims").innerHTML = '<div class="placeholder">加载中...</div>';
  // 默认 tab 回到 overview
  switchTeacherStudentTab(TEACHER_STUDENT_TAB);

  try {
    const r = await apiGet(`/api/teacher/student/${sid}/full`);
    const d = r.data || {};
    TEACHER_STUDENT_DATA = d;

    // ---- 顶栏 ----
    $("#tsv-avatar").innerHTML =
      (d.avatar || "").startsWith("/")
        ? `<img src="${d.avatar}" />`
        : `<span>${d.avatar || "🧑‍🎓"}</span>`;
    $("#tsv-name").textContent = d.name || "未命名";
    $("#tsv-sub").textContent = [d.grade, d.subject].filter(Boolean).join(" · ")
                                || d.note || "";

    // ---- stats tiles ----
    $("#tsv-stats").innerHTML = `
      <div class="tsv-tile"><div class="tt-num">${d.graded_count || 0}</div><div class="tt-label">已批改</div></div>
      <div class="tsv-tile"><div class="tt-num">${d.accuracy == null ? "-" : d.accuracy + "%"}</div><div class="tt-label">正确率</div></div>
      <div class="tsv-tile"><div class="tt-num">${d.error_count || 0}</div><div class="tt-label">未掌握错题</div></div>
      <div class="tsv-tile"><div class="tt-num">${d.error_mastered || 0}</div><div class="tt-label">已掌握错题</div></div>
      <div class="tsv-tile"><div class="tt-num">${d.knowledge_count || 0}</div><div class="tt-label">知识点</div></div>
    `;

    // ---- 渲染各 tab 的内容 ----
    renderTsvDims(d);
    renderTsvHistory();
    renderTsvErrors();
    renderTsvKnowledge();
  } catch (e) {
    $("#tsv-name").textContent = "加载失败";
    $("#tsv-sub").textContent = e.message;
  }
}

function renderTsvDims(d) {
  const box = $("#tsv-dims");
  const dims = d.ability_dimensions || {};
  const entries = Object.entries(dims);
  if (!entries.length) { box.innerHTML = '<div class="placeholder">暂无数据</div>'; return; }
  box.innerHTML = entries.map(([name, v]) => {
    const s = v.score || 0;
    const c = masteryColor(s);
    return `<div class="tsv-dim">
      <div class="td-head"><span>${escapeHtml(name)}</span><span class="td-score" style="color:${c}">${s}</span></div>
      <div class="td-bar"><div class="td-bar-fill" style="width:${s}%;background:${c}"></div></div>
      <div class="td-sub hint">对 ${v.correct || 0} · 错 ${v.wrong || 0}</div>
    </div>`;
  }).join("");
}

function renderTsvHistory() {
  const d = TEACHER_STUDENT_DATA || {};
  const hist = d.history_all || [];
  const kw = ($("#tsv-h-filter").value || "").trim().toLowerCase();
  const ft = $("#tsv-h-filter-type").value || "";
  const fv = $("#tsv-h-filter-verdict").value || "";
  let list = hist;
  if (kw) list = list.filter(h =>
    (h.question_text || "").toLowerCase().includes(kw)
    || (h.knowledge_points || []).some(k => (k||"").toLowerCase().includes(kw))
  );
  if (ft) list = list.filter(h => (h.question_type || "normal") === ft);
  if (fv === "true")  list = list.filter(h => h.is_correct === true);
  if (fv === "false") list = list.filter(h => h.is_correct === false);
  $("#tsv-h-count").textContent = `共 ${list.length} 条 / 总 ${hist.length} 条`;
  const box = $("#tsv-history-list");
  if (!list.length) {
    box.innerHTML = '<div class="placeholder">暂无数据</div>';
    return;
  }
  box.innerHTML = list.map(h => {
    const cls = h.is_correct === true ? "ok"
              : h.is_correct === false ? "bad" : "unk";
    const icon = h.is_correct === true ? "✓"
               : h.is_correct === false ? "✗" : "?";
    const tName = typeName(h.question_type);
    const src = h.source === "realtime" ? "实时" : h.source === "batch" ? "批量" : (h.source || "");
    const kps = (h.knowledge_points || []).map(k =>
      `<span class="tag tag-kp">${escapeHtml(k)}</span>`).join("");
    const cats = (h.error_categories || []).map(c =>
      `<span class="tag tag-cat">${escapeHtml(c)}</span>`).join("");
    return `<div class="tsv-hist ${cls}">
      <div class="th-line">
        <span class="th-tag">${icon}</span>
        <span class="th-time">${escapeHtml(h.created_at || "")}</span>
        <span class="th-type">${escapeHtml(tName)}</span>
        ${src ? `<span class="th-src">${escapeHtml(src)}</span>` : ""}
      </div>
      <div class="th-q">${escapeHtml(h.question_text || "")}</div>
      ${(kps || cats) ? `<div class="th-kps">${kps}${cats}</div>` : ""}
    </div>`;
  }).join("");
}

function renderTsvErrors() {
  const d = TEACHER_STUDENT_DATA || {};
  const errs = d.errors || [];
  const kw = ($("#tsv-e-filter").value || "").trim().toLowerCase();
  const hideM = $("#tsv-e-hide-mastered").checked;
  let list = errs.slice();
  list.sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
  if (hideM) list = list.filter(e => !e.mastered);
  if (kw) list = list.filter(e =>
    (e.question_text || "").toLowerCase().includes(kw)
    || (e.knowledge_points || []).some(k => (k||"").toLowerCase().includes(kw))
    || (e.error_reason || "").toLowerCase().includes(kw)
  );
  $("#tsv-e-count").textContent = `共 ${list.length} 条 / 总 ${errs.length} 条`;
  const box = $("#tsv-errors-list");
  if (!list.length) {
    box.innerHTML = '<div class="placeholder">暂无错题</div>';
    return;
  }
  box.innerHTML = list.map(e => {
    const kps = (e.knowledge_points || []).map(k =>
      `<span class="tag tag-kp">${escapeHtml(k)}</span>`).join("");
    const cats = (e.error_categories || []).map(c =>
      `<span class="tag tag-cat">${escapeHtml(c)}</span>`).join("");
    return `<div class="tsv-err ${e.mastered ? 'mastered' : ''}">
      <div class="te-head">
        <span class="te-type">${escapeHtml(typeName(e.question_type))}</span>
        <span class="te-time">${escapeHtml(e.created_at || "")}</span>
        ${e.mastered ? '<span class="te-badge">✓ 已掌握</span>' : ''}
      </div>
      <div class="te-q">${escapeHtml(e.question_text || "")}</div>
      ${e.student_answer ? `<div class="te-line"><b>学生答:</b> ${escapeHtml(e.student_answer)}</div>` : ""}
      ${e.correct_answer ? `<div class="te-line"><b>正确答案:</b> ${escapeHtml(e.correct_answer)}</div>` : ""}
      ${e.error_reason ? `<div class="te-line te-reason"><b>错因:</b> ${escapeHtml(e.error_reason)}</div>` : ""}
      ${e.explanation ? `<div class="te-line te-expl"><b>解析:</b> ${escapeHtml(e.explanation)}</div>` : ""}
      ${(kps || cats) ? `<div class="te-tags">${kps}${cats}</div>` : ""}
    </div>`;
  }).join("");
}

function renderTsvKnowledge() {
  const d = TEACHER_STUDENT_DATA || {};
  const all = (d.knowledge_points || []).slice();
  const kw = ($("#tsv-k-filter").value || "").trim().toLowerCase();
  const sort = $("#tsv-k-sort").value;
  let list = all;
  if (kw) list = list.filter(p => (p.name || "").toLowerCase().includes(kw));
  if (sort === "weak")        list.sort((a, b) => a.mastery - b.mastery);
  else if (sort === "strong") list.sort((a, b) => b.mastery - a.mastery);
  else                        list.sort((a, b) => b.total - a.total);
  $("#tsv-k-count").textContent = `共 ${list.length} / 总 ${all.length}`;
  const box = $("#tsv-kp-list");
  if (!list.length) {
    box.innerHTML = '<div class="placeholder">暂无知识点</div>';
    return;
  }
  box.innerHTML = list.map(p => {
    const c = masteryColor(p.mastery);
    return `<div class="tsv-kp">
      <div class="tk-head">
        <div class="tk-name">${escapeHtml(p.name)}</div>
        <div class="tk-score" style="color:${c}">${p.mastery}</div>
      </div>
      <div class="tk-bar"><div class="tk-fill" style="width:${p.mastery}%;background:${c}"></div></div>
      <div class="tk-sub hint">对 ${p.correct} · 错 ${p.wrong} · 共 ${p.total} 次</div>
    </div>`;
  }).join("");
}

function switchTeacherStudentTab(tab) {
  TEACHER_STUDENT_TAB = tab;
  $$(".tsv-tab").forEach(t =>
    t.classList.toggle("active", t.dataset.tab === tab));
  $$(".tsv-panel").forEach(p =>
    p.classList.toggle("active", p.dataset.panel === tab));
}

// ---- 绑定新页面的事件 ----
$$(".tsv-tab").forEach(t => t.addEventListener("click", () => {
  switchTeacherStudentTab(t.dataset.tab);
}));
$("#btn-tsv-refresh").addEventListener("click", () => refreshTeacherStudent());
$("#btn-tsv-advice").addEventListener("click", async () => {
  if (!TEACHER_STUDENT_ID) return;
  const body = $("#tsv-advice-body");
  body.innerHTML = '<div class="placeholder">AI 分析中...（可能需要几秒）</div>';
  switchTeacherStudentTab("overview");
  try {
    const r = await apiJson(`/api/teacher/student/${TEACHER_STUDENT_ID}/advice`, "POST");
    body.innerHTML = renderMarkdown(r.markdown || "");
    $("#tsv-advice-time").textContent = "刚刚生成";
  } catch (e) {
    body.innerHTML = `<div class="placeholder">生成失败: ${escapeHtml(e.message)}</div>`;
  }
});
$("#tsv-h-filter").addEventListener("input", () => renderTsvHistory());
$("#tsv-h-filter-type").addEventListener("change", () => renderTsvHistory());
$("#tsv-h-filter-verdict").addEventListener("change", () => renderTsvHistory());
$("#tsv-e-filter").addEventListener("input", () => renderTsvErrors());
$("#tsv-e-hide-mastered").addEventListener("change", () => renderTsvErrors());
$("#tsv-k-filter").addEventListener("input", () => renderTsvKnowledge());
$("#tsv-k-sort").addEventListener("change", () => renderTsvKnowledge());

$("#tcs-close").addEventListener("click", () => $("#tch-stu-modal").classList.add("hidden"));
$("#tcs-close2").addEventListener("click", () => $("#tch-stu-modal").classList.add("hidden"));

$("#btn-tch-refresh").addEventListener("click", () => refreshTeacher(true));
$("#btn-tch-advice").addEventListener("click", async () => {
  const body = $("#tch-advice-body");
  body.innerHTML = '<div class="placeholder">AI 分析中...（可能需要几秒）</div>';
  try {
    const r = await apiJson("/api/teacher/advice", "POST");
    body.innerHTML = renderMarkdown(r.markdown || "");
    $("#tch-advice-time").textContent = "刚刚生成";
  } catch (e) {
    body.innerHTML = `<div class="placeholder">生成失败: ${escapeHtml(e.message)}</div>`;
  }
});

$$(".tch-tab").forEach(t => t.addEventListener("click", () => {
  $$(".tch-tab").forEach(x => x.classList.remove("active"));
  t.classList.add("active");
  TEACHER_SORT = t.dataset.st;
  renderStudentList();
}));

// ============================================================
//                   大模型设置（保持不变）
// ============================================================
let settingsCache = null;
let selectedProvider = null;

async function refreshSettings() {
  try {
    const r = await apiGet("/api/llm_config");
    settingsCache = r.config;
    if (!selectedProvider) selectedProvider = settingsCache.active_provider || "ollama";
    renderProviderPicker();
    renderProviderEditor();
    $("#fb-vision").checked = !!settingsCache.fallback_vision_to_ollama;
    $("#fb-embed").checked  = !!settingsCache.fallback_embed_to_ollama;
  } catch (e) { showToast("读取设置失败: " + e.message, 3000); }
}

$("#fb-vision").addEventListener("change", async (e) => {
  try {
    await apiJson("/api/llm_config", "PUT", { fallback_vision_to_ollama: e.target.checked });
    showToast("已更新");
  } catch (err) { showToast("失败: " + err.message, 3000); }
});
$("#fb-embed").addEventListener("change", async (e) => {
  try {
    await apiJson("/api/llm_config", "PUT", { fallback_embed_to_ollama: e.target.checked });
    showToast("已更新");
  } catch (err) { showToast("失败: " + err.message, 3000); }
});

function renderProviderPicker() {
  const box = $("#provider-picker");
  box.innerHTML = "";
  const meta = settingsCache.providers_meta || {};
  const active = settingsCache.active_provider;
  ["ollama", "custom", "deepseek", "doubao", "qwen"].forEach(key => {
    const m = meta[key] || {};
    const p = (settingsCache.providers || {})[key] || {};
    const isActive = active === key;
    const isSelected = selectedProvider === key;
    const hasKey = !m.needs_api_key || p.api_key_set;
    const card = document.createElement("div");
    card.className = "pp-card" + (isActive ? " active" : "") + (isSelected ? " selected" : "");
    card.innerHTML = `
      <div class="pp-top">
        <div class="pp-name">${escapeHtml(m.name || key)}</div>
        ${isActive ? '<span class="pp-badge">使用中</span>' : ""}
      </div>
      <div class="pp-caps">
        ${m.supports_vision ? '<span class="cap ok">视觉</span>' : '<span class="cap no">视觉</span>'}
        ${m.supports_text   ? '<span class="cap ok">文本</span>' : '<span class="cap no">文本</span>'}
        ${m.supports_embed  ? '<span class="cap ok">嵌入</span>' : '<span class="cap no">嵌入</span>'}
        ${m.needs_api_key
          ? (hasKey ? '<span class="cap ok">已配置 Key</span>' : '<span class="cap no">未配置 Key</span>')
          : '<span class="cap ok">无需 Key</span>'}
      </div>
      <div class="pp-actions">
        <button class="btn btn-sm" data-select="${key}">编辑</button>
        ${isActive
          ? '<button class="btn btn-sm btn-ghost" disabled>当前</button>'
          : `<button class="btn btn-sm btn-primary" data-activate="${key}">启用</button>`}
      </div>`;
    box.appendChild(card);
  });
  box.querySelectorAll("[data-select]").forEach(b =>
    b.addEventListener("click", () => {
      selectedProvider = b.dataset.select;
      renderProviderPicker(); renderProviderEditor();
    }));
  box.querySelectorAll("[data-activate]").forEach(b =>
    b.addEventListener("click", async () => {
      try {
        await apiJson("/api/llm_config", "PUT", { active_provider: b.dataset.activate });
        showToast("已切换为: " + providerLabel(b.dataset.activate));
        await refreshSettings();
      } catch (e) { showToast("切换失败: " + e.message, 3000); }
    }));
}

function renderProviderEditor() {
  const box = $("#provider-editor");
  if (!selectedProvider || !settingsCache) {
    box.innerHTML = '<div class="placeholder">请选择一个供应商</div>'; return;
  }
  const meta = (settingsCache.providers_meta || {})[selectedProvider] || {};
  const p = (settingsCache.providers || {})[selectedProvider] || {};
  box.innerHTML = `
    <div class="pe-head">
      <div>
        <h3 class="pe-title">${escapeHtml(meta.name || selectedProvider)}</h3>
        ${meta.tips ? `<div class="pe-tips">${escapeHtml(meta.tips)}</div>` : ""}
        ${meta.docs ? `<div class="pe-docs">文档: <a href="${meta.docs}" target="_blank" rel="noopener">${escapeHtml(meta.docs)}</a></div>` : ""}
      </div>
    </div>
    <div class="pe-grid">
      <label class="pe-field">
        <span>Base URL</span>
        <input id="pe-base_url" value="${escapeHtml(p.base_url || meta.default_base_url || "")}" />
      </label>
      ${meta.needs_api_key ? `
        <label class="pe-field">
          <span>API Key</span>
          <div class="pe-key-row">
            <input id="pe-api_key" type="password" autocomplete="off"
                   placeholder="${p.api_key_set ? '已保存：' + escapeHtml(p.api_key_masked || '') + '（留空不覆盖）' : '在此粘贴 API Key'}" />
            ${p.api_key_set ? '<button class="btn btn-sm btn-ghost" id="pe-key-clear">清空 Key</button>' : ""}
          </div>
        </label>` : ""}
      <label class="pe-field">
        <span>视觉模型 ${meta.supports_vision ? "" : "<em class='dim'>(不支持)</em>"}</span>
        <input id="pe-vision_model" value="${escapeHtml(p.vision_model || "")}" ${meta.supports_vision ? "" : "disabled"} />
      </label>
      <label class="pe-field">
        <span>文本模型</span>
        <input id="pe-text_model" value="${escapeHtml(p.text_model || "")}" />
      </label>
      <label class="pe-field">
        <span>嵌入模型 ${meta.supports_embed ? "" : "<em class='dim'>(不支持)</em>"}</span>
        <input id="pe-embed_model" value="${escapeHtml(p.embed_model || "")}" ${meta.supports_embed ? "" : "disabled"} />
      </label>
    </div>
    <div class="pe-actions">
      <button class="btn btn-primary" id="pe-save">保存</button>
      <button class="btn" id="pe-test-text">测试文本</button>
      <button class="btn" id="pe-test-vision" ${meta.supports_vision ? "" : "disabled"}>测试视觉</button>
      <button class="btn" id="pe-test-embed" ${meta.supports_embed ? "" : "disabled"}>测试嵌入</button>
    </div>
    <div class="pe-result" id="pe-result"></div>`;
  $("#pe-save").addEventListener("click", saveCurrentProvider);
  const kc = $("#pe-key-clear");
  if (kc) kc.addEventListener("click", async () => {
    if (!confirm("确认清空此供应商的 API Key？")) return;
    try {
      await apiJson(`/api/llm_config/provider/${selectedProvider}`, "PUT", { api_key: "__clear__" });
      showToast("Key 已清空"); refreshSettings();
    } catch (e) { showToast("失败: " + e.message, 3000); }
  });
  $("#pe-test-text").addEventListener("click",  () => testProvider("text"));
  $("#pe-test-vision").addEventListener("click",() => testProvider("vision"));
  $("#pe-test-embed").addEventListener("click", () => testProvider("embed"));
}

async function saveCurrentProvider() {
  const patch = {
    base_url:     $("#pe-base_url")?.value.trim() || "",
    vision_model: $("#pe-vision_model")?.value.trim() || "",
    text_model:   $("#pe-text_model")?.value.trim() || "",
    embed_model:  $("#pe-embed_model")?.value.trim() || "",
  };
  const keyInput = $("#pe-api_key");
  if (keyInput && keyInput.value && keyInput.value.length) patch.api_key = keyInput.value;
  try {
    showLoading("保存中...");
    await apiJson(`/api/llm_config/provider/${selectedProvider}`, "PUT", patch);
    showToast("已保存"); await refreshSettings();
  } catch (e) { showToast("保存失败: " + e.message, 3000); }
  finally { hideLoading(); }
}

async function testProvider(capability) {
  const box = $("#pe-result");
  box.innerHTML = `<div class="pe-loading">测试 ${capability} 中...</div>`;
  try {
    const r = await apiJson("/api/llm_config/test", "POST",
      { provider: selectedProvider, capability });
    const ok = r.result?.ok, msg = r.result?.message || "", ms = r.result?.elapsed_ms;
    box.innerHTML = `<div class="pe-result-box ${ok ? "ok" : "bad"}">
      <div class="pe-r-head">${ok ? "✓ 成功" : "✗ 失败"} <span class="pe-r-ms">${ms ? ms + " ms" : ""}</span></div>
      <div class="pe-r-msg">${escapeHtml(msg)}</div>
    </div>`;
  } catch (e) {
    box.innerHTML = `<div class="pe-result-box bad">
      <div class="pe-r-head">✗ 请求失败</div>
      <div class="pe-r-msg">${escapeHtml(e.message)}</div>
    </div>`;
  }
}

// ============================================================
//                        日志面板
// ============================================================
$("#btn-logs").addEventListener("click", () => {
  $("#log-panel").classList.remove("hidden");
  refreshLogs();
});
$("#btn-log-close").addEventListener("click", () => $("#log-panel").classList.add("hidden"));
$("#btn-log-refresh").addEventListener("click", refreshLogs);
$("#btn-log-clear").addEventListener("click", async () => {
  await fetch("/api/logs", { method: "DELETE" });
  refreshLogs();
});
async function refreshLogs() {
  try {
    const r = await apiGet("/api/logs");
    const body = $("#log-body");
    body.innerHTML = "";
    (r.logs || []).forEach(l => {
      const line = document.createElement("div");
      line.className = "log-line " + l.level;
      line.innerHTML = `<span class="ts">${escapeHtml(l.ts)}</span>[${l.level}] ${escapeHtml(l.message)}`;
      body.appendChild(line);
    });
  } catch (e) { $("#log-body").textContent = "加载失败: " + e.message; }
}

// ============================================================
//                          启动
// ============================================================
(async function boot() {
  try {
    const r = await apiGet("/api/students");
    STATE.students = r.items || [];
    const sid = localStorage.getItem("currentStudentId");
    if (sid) {
      const found = STATE.students.find(s => s.id === sid);
      if (found) {
        saveCurrentStudent(found);
        goto("home");
        return;
      } else {
        localStorage.removeItem("currentStudentId");
      }
    }
  } catch (e) {
    showToast("服务连接失败: " + e.message, 3000);
  }
  goto("login");
})();
