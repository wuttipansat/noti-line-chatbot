// AI Planner calendar inside LINE (LIFF). Plain JavaScript, no build step.
// Open with ?demo to see it with sample data in any browser (no LINE needed).
"use strict";

const DAY_KEYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]; // Date.getDay() order
const DAY_NAMES = ["วันอาทิตย์", "วันจันทร์", "วันอังคาร", "วันพุธ", "วันพฤหัสบดี", "วันศุกร์", "วันเสาร์"];
const DAY_SHORT = ["อา", "จ", "อ", "พ", "พฤ", "ศ", "ส"];
const MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
  "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"];
const MONTHS_SHORT = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
  "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."];
const DEMO = new URLSearchParams(location.search).has("demo");
const UNDO_SECONDS = 5;

const state = {
  token: null,
  today: null, // "YYYY-MM-DD", the user's local date (from the server)
  year: 0,
  month: 0, // 0-11, the month on screen
  selected: null, // "YYYY-MM-DD"
  byDate: new Map(), // "YYYY-MM-DD" -> [task, ...]
  overdue: [], // open tasks due before today (shown on today's page)
};

const $ = (id) => document.getElementById(id);

// --- dates as "YYYY-MM-DD" strings (no time zones in the browser) ---
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const parse = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const gridStart = (y, m) => { const first = new Date(y, m, 1); return addDays(first, -first.getDay()); };

// --- talking to our server ---
async function api(path, options = {}) {
  if (DEMO) return demoApi(path, options);
  const response = await fetch(path, {
    ...options,
    headers: { Authorization: `Bearer ${state.token}`, ...(options.headers || {}) },
  });
  if (response.status === 401) throw new Error("expired");
  if (response.status === 422) throw new Error("invalid");
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.status === 204 ? null : response.json();
}

async function load() {
  const start = gridStart(state.year, state.month);
  const end = addDays(start, 42);
  $("grid").classList.add("loading");
  try {
    const data = await api(`/api/tasks?start=${iso(start)}&end=${iso(end)}`);
    state.today = data.today;
    state.overdue = data.overdue;
    state.byDate = new Map();
    for (const task of data.tasks) {
      const key = task.date || "none";
      if (!state.byDate.has(key)) state.byDate.set(key, []);
      state.byDate.get(key).push(task);
    }
    hideNotice();
  } catch (error) {
    showError(error);
  } finally {
    $("grid").classList.remove("loading");
    render();
  }
}

// --- drawing ---
function setDayColours(el, dateString) {
  const key = DAY_KEYS[parse(dateString).getDay()];
  el.style.setProperty("--day", `var(--${key})`);
  el.style.setProperty("--day-ink", `var(--${key}-ink)`);
  el.style.setProperty("--on-day", key === "mon" ? "#1f2329" : "#fff");
}

function isLate(task, dateString) {
  return !task.done && state.today && dateString < state.today;
}

function render() {
  $("month-title").textContent = `${MONTHS[state.month]} ${state.year + 543}`;
  if (state.today) setDayColours(document.documentElement, state.today);
  renderGrid();
  renderDay();
}

function renderGrid() {
  const grid = $("grid");
  grid.replaceChildren();
  const start = gridStart(state.year, state.month);
  for (let i = 0; i < 42; i++) {
    const date = addDays(start, i);
    const key = iso(date);
    const items = state.byDate.get(key) || [];
    const cell = document.createElement("button");
    cell.type = "button";
    cell.className = "cell";
    if (date.getMonth() !== state.month) cell.classList.add("other");
    if (key === state.today) cell.classList.add("today");
    if (key === state.selected) cell.classList.add("selected");
    cell.setAttribute("aria-label", `${date.getDate()} ${MONTHS[date.getMonth()]} ${items.length ? `${items.length} งาน` : "ไม่มีงาน"}`);

    const num = document.createElement("span");
    num.className = "num";
    num.textContent = date.getDate();
    cell.append(num);

    for (const task of items.slice(0, 2)) {
      const chip = document.createElement("span");
      chip.className = "chip";
      if (task.done) chip.classList.add("done");
      else if (isLate(task, key)) chip.classList.add("late");
      chip.textContent = task.title;
      cell.append(chip);
    }
    if (items.length > 2) {
      const more = document.createElement("span");
      more.className = "more";
      more.textContent = `อีก ${items.length - 2}`;
      cell.append(more);
    }
    cell.addEventListener("click", () => { state.selected = key; render(); });
    grid.append(cell);
  }
}

function renderDay() {
  const key = state.selected;
  const date = parse(key);
  const items = state.byDate.get(key) || [];
  setDayColours($("day"), key);
  $("day-num").textContent = date.getDate();
  $("day-name").textContent = DAY_NAMES[date.getDay()];
  const done = items.filter((t) => t.done).length;
  $("day-sub").textContent = `${MONTHS[date.getMonth()]} ${date.getFullYear() + 543}`;
  $("day-count").textContent = !items.length ? "" : done ? `เสร็จ ${done} จาก ${items.length} งาน` : `${items.length} งาน`;

  // Today's page also lists what is late, like the morning card in the chat.
  const late = key === state.today ? state.overdue : [];
  $("late-block").hidden = !late.length;
  $("late").replaceChildren(...late.map((t) => row(t, t.date, true)));

  const list = $("agenda");
  list.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = late.length ? "ไม่มีงานที่กำหนดไว้วันนี้" : "ไม่มีงานวันนี้ พิมพ์งานใหม่ในแชทได้เลย";
    list.append(empty);
    return;
  }
  for (const task of items) list.append(row(task, key));
}

function row(task, dateString, showDate = false) {
  const li = document.createElement("li");
  li.className = "row";
  if (task.done) li.classList.add("done");
  else if (isLate(task, dateString)) li.classList.add("late");
  if (task.important) li.classList.add("important");

  const when = document.createElement("span");
  when.className = "when";
  const d = parse(dateString);
  when.textContent = showDate ? `${d.getDate()} ${MONTHS_SHORT[d.getMonth()]}` : task.time || "ทั้งวัน";

  const what = document.createElement("button"); // tap the task to edit it
  what.type = "button";
  what.className = "what";
  what.addEventListener("click", () => openSheet(task));
  const title = document.createElement("span");
  title.className = "title";
  title.textContent = task.title;
  const meta = document.createElement("span");
  meta.className = "meta";
  meta.textContent = `#${task.number}${task.important ? "  สำคัญ" : ""}`;
  what.append(title, meta);

  const tick = document.createElement("button");
  tick.type = "button";
  tick.className = "tick";
  tick.setAttribute("aria-pressed", String(task.done));
  tick.setAttribute("aria-label", task.done ? `ยกเลิกเสร็จ ${task.title}` : `ทำเสร็จ ${task.title}`);
  tick.append(document.createElement("span"));
  tick.addEventListener("click", () => setDone(task, !task.done));

  li.append(when, what, tick);
  return li;
}

// --- ticking a task: change the screen first, then tell the server ---
let toastTimer = null;

// The same task can be on screen twice (its own day and "late"): change every copy.
function mark(number, done) {
  for (const list of [...state.byDate.values(), state.overdue]) {
    for (const t of list) if (t.number === number) t.done = done;
  }
}

async function setDone(task, done) {
  mark(task.number, done);
  render();
  try {
    await api(`/api/tasks/${task.number}/${done ? "done" : "undo"}`, { method: "POST" });
    if (done) showToast(`ทำเสร็จแล้ว #${task.number}`, () => setDone(task, false));
  } catch (error) {
    mark(task.number, !done); // put it back
    render();
    showError(error);
  }
}

function showToast(text, undo) {
  clearTimeout(toastTimer);
  $("toast-text").textContent = text;
  $("toast-undo").hidden = !undo;
  $("toast-undo").onclick = () => { hideToast(); undo(); };
  $("toast").hidden = false;
  toastTimer = setTimeout(hideToast, UNDO_SECONDS * 1000);
}

function hideToast() { $("toast").hidden = true; }

// --- messages ---
function showError(error) {
  if (error.message === "expired") {
    showNotice("หน้านี้เปิดค้างไว้นานจนการเข้าสู่ระบบหมดอายุ ปิดแล้วเปิดใหม่จากแชทได้เลย");
  } else {
    showNotice("โหลดงานไม่สำเร็จ ตรวจอินเทอร์เน็ตแล้วลองอีกครั้ง", "ลองอีกครั้ง", load);
  }
}

function showNotice(text, actionLabel, action) {
  $("notice-text").textContent = text;
  const button = $("notice-action");
  button.hidden = !actionLabel;
  if (actionLabel) { button.textContent = actionLabel; button.onclick = action; }
  $("notice").hidden = false;
}

function hideNotice() { $("notice").hidden = true; }

// --- add / edit sheet ---
let editing = null; // the task being edited, or null when adding
let deleteTimer = null;

function openSheet(task) {
  editing = task;
  const sheet = $("sheet");
  setDayColours(sheet, task ? task.date : state.selected);
  $("sheet-title").textContent = task ? `แก้ไขงาน #${task.number}` : "เพิ่มงาน";
  $("f-title").value = task ? task.title : "";
  $("f-date").value = task ? task.date : state.selected;
  $("f-time").value = task?.time || "";
  $("f-important").checked = task ? task.important : false;
  $("delete").hidden = !task;
  resetDelete();
  $("form-error").hidden = true;
  sheet.showModal();
  if (!task) $("f-title").focus();
}

function closeSheet() { $("sheet").close(); }

function formError(text) {
  $("form-error").textContent = text;
  $("form-error").hidden = false;
}

async function save(event) {
  event.preventDefault();
  const body = {
    title: $("f-title").value.trim(),
    date: $("f-date").value,
    time: $("f-time").value || null,
    important: $("f-important").checked,
  };
  if (!body.title) return formError("ใส่ชื่องานก่อนบันทึก");
  if (!body.date) return formError("เลือกวันที่ก่อนบันทึก");

  $("save").disabled = true;
  try {
    const path = editing ? `/api/tasks/${editing.number}` : "/api/tasks";
    const saved = await api(path, {
      method: editing ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    closeSheet();
    showToast(editing ? `บันทึกงาน #${saved.number} แล้ว` : `เพิ่มงาน #${saved.number} แล้ว`);
    state.selected = saved.date;
    const d = parse(saved.date);
    state.year = d.getFullYear();
    state.month = d.getMonth();
    await load();
  } catch (error) {
    if (error.message === "invalid") formError("บันทึกไม่ได้ ตรวจชื่องาน วันที่ และเวลาอีกครั้ง");
    else if (error.message === "expired") { closeSheet(); showError(error); }
    else formError("บันทึกไม่สำเร็จ ตรวจอินเทอร์เน็ตแล้วลองอีกครั้ง");
  } finally {
    $("save").disabled = false;
  }
}

// Deleting needs two taps: the first turns the button into a confirmation.
function resetDelete() {
  clearTimeout(deleteTimer);
  $("delete").classList.remove("confirm");
  $("delete").textContent = "ลบงาน";
}

async function remove() {
  const button = $("delete");
  if (!button.classList.contains("confirm")) {
    button.classList.add("confirm");
    button.textContent = "กดอีกครั้งเพื่อลบ";
    deleteTimer = setTimeout(resetDelete, 4000);
    return;
  }
  try {
    await api(`/api/tasks/${editing.number}`, { method: "DELETE" });
    closeSheet();
    showToast(`ลบงาน #${editing.number} แล้ว`);
    await load();
  } catch (error) {
    resetDelete();
    formError("ลบไม่สำเร็จ ลองอีกครั้ง");
  }
}

// --- moving between months ---
function moveMonth(step) {
  const d = new Date(state.year, state.month + step, 1);
  state.year = d.getFullYear();
  state.month = d.getMonth();
  const sameDay = new Date(state.year, state.month, Math.min(parse(state.selected).getDate(), 28));
  state.selected = state.today && state.today.startsWith(iso(d).slice(0, 7)) ? state.today : iso(sameDay);
  load();
}

function goToday() {
  const today = parse(state.today || iso(new Date()));
  state.year = today.getFullYear();
  state.month = today.getMonth();
  state.selected = iso(today);
  load();
}

function setUp() {
  $("weekdays").replaceChildren(...DAY_SHORT.map((label, i) => {
    const div = document.createElement("div");
    div.textContent = label;
    div.style.setProperty("--c", `var(--${DAY_KEYS[i]})`);
    return div;
  }));
  $("prev").addEventListener("click", () => moveMonth(-1));
  $("next").addEventListener("click", () => moveMonth(1));
  $("go-today").addEventListener("click", goToday);
  $("add").addEventListener("click", () => openSheet(null));
  $("form").addEventListener("submit", save);
  $("cancel").addEventListener("click", closeSheet);
  $("delete").addEventListener("click", remove);

  // swipe left / right on the grid to change month
  let startX = null;
  $("grid").addEventListener("touchstart", (e) => { startX = e.touches[0].clientX; }, { passive: true });
  $("grid").addEventListener("touchend", (e) => {
    if (startX === null) return;
    const dx = e.changedTouches[0].clientX - startX;
    if (Math.abs(dx) > 60) moveMonth(dx < 0 ? 1 : -1);
    startX = null;
  });
}

// --- start ---
async function start() {
  setUp();
  try {
    if (!DEMO) {
      const { liffId } = await (await fetch("/api/config")).json();
      if (!liffId) throw new Error("not-configured");
      await liff.init({ liffId });
      if (!liff.isLoggedIn()) { liff.login(); return; }
      state.token = liff.getIDToken();
    }
  } catch (error) {
    showNotice(error.message === "not-configured"
      ? "เซิร์ฟเวอร์ยังไม่ได้ตั้งค่า LIFF_ID"
      : "เปิดหน้านี้จากแชท AI Planner ในแอป LINE");
    return;
  }
  state.today = DEMO ? DEMO_TODAY : iso(new Date());
  goToday();
}

// --- sample data for ?demo ---
const DEMO_TODAY = "2026-10-31";
const DEMO_TASKS = [
  [1, "ส่งใบเบิกค่าเดินทาง", "2026-10-29", null, false, false],
  [12, "ส่งแบบฟอร์มลา", "2026-10-05", null, false, true],
  [13, "ประชุมทีม", "2026-10-08", "10:00", true, true],
  [14, "อ่านเอกสารประกอบ", "2026-10-08", null, false, true],
  [15, "ตรวจรายงานไตรมาส", "2026-10-15", "14:00", false, true],
  [16, "นัดหมอฟัน", "2026-10-20", "16:30", false, true],
  [17, "ประชุมทีม", "2026-10-22", "10:00", false, true],
  [18, "จ่ายค่าไฟ", "2026-10-22", null, false, true],
  [19, "โทรหาแม่", "2026-10-22", "19:00", false, true],
  [20, "ซ้อมพรีเซนต์", "2026-10-27", null, false, true],
  [3, "ประชุมทีม", "2026-10-31", "10:00", true, false],
  [5, "ซื้อของเข้าบ้าน", "2026-10-31", "17:30", false, false],
  [4, "อ่าน paper บทที่ 2", "2026-10-31", null, false, false],
  [9, "เตรียมเอกสารประกอบการประชุมคณะกรรมการประจำไตรมาสและส่งให้ฝ่ายบัญชีตรวจทาน", "2026-10-31", null, false, false],
  [6, "โทรนัดช่างแอร์", "2026-11-02", "09:00", false, false],
  [7, "ส่งรายงานประจำเดือน", "2026-11-04", null, true, false],
  [8, "ไปรับพัสดุ", "2026-11-04", "17:00", false, false],
].map(([number, title, date, time, important, done]) => ({ number, title, date, time, important, done }));

async function demoApi(path, options = {}) {
  await new Promise((r) => setTimeout(r, 150));
  const method = options.method || "GET";
  const number = Number(path.split("/")[3]);
  if (method === "POST" && path === "/api/tasks") {
    const task = { number: Math.max(...DEMO_TASKS.map((t) => t.number)) + 1, done: false, ...JSON.parse(options.body) };
    DEMO_TASKS.push(task);
    return { ...task };
  }
  if (method === "PUT") {
    const task = DEMO_TASKS.find((t) => t.number === number);
    Object.assign(task, JSON.parse(options.body));
    return { ...task };
  }
  if (method === "DELETE") {
    DEMO_TASKS.splice(DEMO_TASKS.findIndex((t) => t.number === number), 1);
    return null;
  }
  if (path.startsWith("/api/tasks?")) {
    const q = new URLSearchParams(path.split("?")[1]);
    const tasks = DEMO_TASKS.filter((t) => t.date >= q.get("start") && t.date < q.get("end"));
    const overdue = DEMO_TASKS.filter((t) => !t.done && t.date < DEMO_TODAY);
    return { today: DEMO_TODAY, tasks: tasks.map((t) => ({ ...t })), overdue };
  }
  return {};
}

start();
