// Telegram-бот расписания КФ МГУ: webhook + cron (раз в минуту). Cloudflare Workers + D1.
import ROOMS from "./rooms.json";
const TZ = 5 * 3600 * 1000; // Казахстан, UTC+5 без перехода на летнее время
const SUPPORT_URL = "https://t.me/aitufmstaff";
const supportKb = { inline_keyboard: [[{ text: "✉️ Написать в поддержку", url: SUPPORT_URL }]] };
const GREETINGS = [
  "Доброе утро! 🌿 Пусть неделя будет лёгкой, пары — интересными, а кофе — крепким. Удачной недели!",
  "С понедельником! ☀️ Желаю продуктивной недели, вовремя найденных кабинетов и хороших оценок. Удачи!",
  "Новая неделя — новые возможности! 🚀 Удачной учёбы, бодрого настроения и поменьше дедлайнов.",
];
// Карты кабинетов: PNG лежат в репозитории (maps/png), Telegram сам скачивает их по ссылке
const MAP_BASE = "https://raw.githubusercontent.com/milanastyopkina/msukzparser/main/maps/";
const MAP_LEAD = 20;                                            // за сколько минут до пары присылать карту
const roomNo = (l) => (!l.zid && l.mode !== "ДО" && /^(\d{3})(?!\d)/.exec(l.room || "")?.[1]) || null;
const mapUrl = (no) => (ROOMS[no] ? MAP_BASE + ROOMS[no].img.split("/").map(encodeURIComponent).join("/") : null);
const FEEDS = {
  ann: "📢 Объявления", news: "📰 Новости", conf: "🎓 Конференции и семинары",
  lect: "🎤 Гостевые лекции", olymp: "🏆 Олимпиады", school: "🏫 Школы юных МГУ",
};
const DEFAULT_FEEDS = ["ann"];   // что включается новому пользователю
const NEWS_MAX_AGE = 3 * 86400;  // старше 3 суток в рассылку не попадает
const nowSec = (env) => Math.floor((env.DEV === "1" && env.FAKE_NOW ? Date.parse(env.FAKE_NOW) : Date.now()) / 1000);
const DOW = ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"];

const nowLocal = (env) => new Date((env.DEV === "1" && env.FAKE_NOW ? Date.parse(env.FAKE_NOW) : Date.now()) + TZ);
const iso = (d) => d.toISOString().slice(0, 10);
const addDays = (d, n) => new Date(d.getTime() + n * 86400000);
const hhmm = (m) => String(Math.floor(m / 60)).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0");
const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const fmtDate = (s) => { const d = new Date(s + "T00:00:00Z"); return `${DOW[d.getUTCDay()]} ${s.slice(8)}.${s.slice(5, 7)}`; };

async function tg(env, method, body) {
  try {
    const r = await fetch(`${env.TG_API || "https://api.telegram.org"}/bot${env.BOT_TOKEN}/${method}`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    return await r.json().catch(() => ({ ok: false, error_code: r.status }));
  } catch { return { ok: false, error_code: 0 }; } // сеть
}
// Telegram режет сообщения длиннее 4096 символов: делим по пустым строкам (HTML-теги внутри абзаца не рвутся)
const LIMIT = 3900;
function parts(text) {
  if (text.length <= LIMIT) return [text];
  const out = []; let cur = "";
  for (const p of text.split("\n\n")) {
    const piece = p.length > LIMIT ? p.slice(0, LIMIT) : p;
    if (cur && cur.length + piece.length + 2 > LIMIT) { out.push(cur); cur = ""; }
    cur = cur ? cur + "\n\n" + piece : piece;
  }
  if (cur) out.push(cur);
  return out;
}
async function send(env, chat_id, text, extra = {}) {
  const ps = parts(text); let r;
  for (let i = 0; i < ps.length; i++)
    r = await tg(env, "sendMessage", { chat_id, text: ps[i], parse_mode: "HTML", disable_web_page_preview: true,
      ...(i === ps.length - 1 ? extra : {}) });
  return r;
}
async function sendPhoto(env, chat_id, photo, caption, extra = {}) {
  return tg(env, "sendPhoto", { chat_id, photo, caption, parse_mode: "HTML", ...extra });
}
// постоянное сравнение строк (секреты не угадываются по времени ответа)
function safeEq(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length < 16 || a.length !== b.length) return false;
  let x = 0; for (let i = 0; i < a.length; i++) x |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return x === 0;
}
// защита от флуда: не больше 20 действий в минуту с одного чата (в памяти изолята, best-effort)
const hits = new Map();
function flood(chat) {
  const now = Date.now(), w = 60000;
  const h = (hits.get(chat) || []).filter((t) => now - t < w);
  h.push(now); hits.set(chat, h);
  if (hits.size > 5000) for (const [k, v] of hits) if (now - v[v.length - 1] > w) hits.delete(k);
  return h.length > 20 ? (h.length === 21 ? "warn" : "drop") : "";
}
const okUrl = (u) => /^https:\/\/(www\.)?msu\.kz\//.test(u || "");

function roomOf(l) {
  const r = l.room || (l.mode === "ДО" ? "онлайн" : "—");
  return l.zid ? `${r}${l.zcode ? ` (Zoom ${l.zid}, код ${l.zcode})` : ` (Zoom ${l.zid})`}` : r;
}
function lessonLine(l, withDate) {
  const sub = l.sub ? ` [подгр. ${esc(l.sub)}]` : "";
  const kind = l.kind ? ` · ${esc(l.kind)}` : "";
  return `${hhmm(l.start_min)}–${hhmm(l.end_min)}  <b>${esc(l.subject)}</b>${kind}${sub}\n      🚪 ${esc(roomOf(l))}` +
    (l.teacher ? `  👤 ${esc(l.teacher)}` : "") + (withDate ? "" : "");
}
function dayFromRows(group, date, rows, title) {
  const head = `${title ?? "📅"} <b>${fmtDate(date)}</b> · ${esc(group)}`;
  if (!rows.length) return `${head}\nЗанятий нет.`;
  return `${head}\n\n` + rows.map((l) => lessonLine(l)).join("\n\n");
}
function rangeFromRows(group, rows, title) {
  if (!rows.length) return `${title} · ${esc(group)}\nЗанятий в этот период нет (или расписание ещё не выложено).`;
  const by = {};
  for (const l of rows) (by[l.date] ||= []).push(l);
  return `${title} · ${esc(group)}\n\n` + Object.keys(by).map((d) =>
    `<b>${fmtDate(d)}</b>\n` + by[d].map((l) => lessonLine(l)).join("\n")).join("\n\n");
}
async function dayText(env, group, date, title) {
  const { results } = await env.DB.prepare(
    "SELECT * FROM cur_lessons WHERE group_code=?1 AND date=?2 ORDER BY start_min, sub").bind(group, date).all();
  return dayFromRows(group, date, results, title);
}
async function rangeText(env, group, from, days, title) {
  const to = iso(addDays(new Date(from + "T00:00:00Z"), days - 1));
  const { results } = await env.DB.prepare(
    "SELECT * FROM cur_lessons WHERE group_code=?1 AND date BETWEEN ?2 AND ?3 ORDER BY date, start_min, sub").bind(group, from, to).all();
  return rangeFromRows(group, results, title);
}

// Парсер Moodle запускается один раз в день вечером (см. sync.yml, ~19:50–20:00 по Астане): по просьбе техотдела вуза
const UPDATE_HOUR = 20;
const MON_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const dayMonth = (s) => `${+s.slice(8)} ${MON_GEN[+s.slice(5, 7) - 1]}`;
async function freshness(env) {
  const r = await env.DB.prepare("SELECT v FROM meta WHERE k='checked'").first();
  if (!r?.v) return "";
  const c = new Date(Date.parse(r.v) + TZ), n = nowLocal(env);
  const nm = n.getUTCHours() * 60 + n.getUTCMinutes();
  // сегодняшнее обновление ещё впереди (или как раз идёт) -> «сегодня», иначе -> завтра
  const todayAhead = nm < UPDATE_HOUR * 60 || (iso(c) < iso(n) && nm < (UPDATE_HOUR + 1) * 60);
  const nextDay = todayAhead ? iso(n) : iso(addDays(n, 1));
  return `\n\n🕒 <i>Расписание актуально на ${UPDATE_HOUR}:00, ${dayMonth(iso(c))}. Следующее обновление: ${dayMonth(nextDay)} в ${UPDATE_HOUR}:00.</i>`;
}
async function reply(env, chat, text) { return send(env, chat, text + (await freshness(env))); }

async function groupKeyboard(env, course) {
  const { results } = await env.DB.prepare("SELECT code, degree, course FROM groups ORDER BY degree DESC, code").all();
  if (!results.length) return null;
  const rows = []; let row = [];
  for (const g of results) {
    row.push({ text: g.code, callback_data: "g:" + g.code });
    if (row.length === 3) { rows.push(row); row = []; }
  }
  if (row.length) rows.push(row);
  return { inline_keyboard: rows };
}

async function subsKeyboard(env, chat) {
  const { results } = await env.DB.prepare("SELECT feed FROM subs WHERE chat_id=?1").bind(chat).all();
  const on = new Set(results.map((r) => r.feed));
  return { inline_keyboard: Object.entries(FEEDS).map(([k, name]) => [{ text: (on.has(k) ? "✅ " : "⬜ ") + name, callback_data: "s:" + k }]) };
}
const SUBS_TEXT = "Выберите, о чём присылать короткие уведомления (заголовок + ссылка на сайт msu.kz). Нажатие включает/выключает раздел:";

const NOTIFY_TEXT = "Настройки уведомлений. Нажатие включает или выключает пункт:";
async function notifyKeyboard(env, chat) {
  const u = await env.DB.prepare("SELECT notify, map_pre FROM users WHERE chat_id=?1").bind(chat).first();
  return { inline_keyboard: [
    [{ text: (u?.notify ? "🔔 Уведомления: вкл" : "🔕 Уведомления: выкл"), callback_data: "n:t" }],
    [{ text: (u?.map_pre ? "✅ " : "⬜ ") + `Карта кабинета за ${MAP_LEAD} мин до пары`, callback_data: "n:m" }] ] };
}

async function userGroup(env, chat) {
  const u = await env.DB.prepare("SELECT group_code, notify FROM users WHERE chat_id=?1").bind(chat).first();
  return u;
}

async function chooseGroup(env, chat) {
  const kb = await groupKeyboard(env, null);
  if (!kb) return send(env, chat, "Расписание ещё не загружено. Попробуйте позже.");
  return send(env, chat, "Выберите свою группу:", { reply_markup: kb });
}

const INFO_TEXT = `<b>Что умеет Мудлик</b>

Выберите группу через /start — и бот будет показывать расписание и напоминать о парах.

<b>Расписание</b>
/today — на сегодня
/tomorrow — на завтра
/week — на текущую неделю
/nextweek — на следующую неделю (доступно, как только оно появится на сайте)
/next — ближайшая пара
/group — сменить группу
/room 507 — карта кабинета (5–7 этажи)

<b>Уведомления</b> (включены по умолчанию)
• в воскресенье в 20:30 — расписание на всю следующую неделю с кабинетами
• за час до первой пары — расписание на день
• за 15 минут до каждой пары — какая пара и в каком кабинете
Настройки: /notify. Там же можно включить карту кабинета: за 20 минут до пары бот пришлёт карту этажа с отмеченным кабинетом.
Выключить и включить все уведомления: /notify off и /notify on

<b>Новости и объявления</b>
/news — выберите, какие разделы сайта msu.kz вам интересны: объявления, новости, конференции и семинары, гостевые лекции, олимпиады, школы юных МГУ. По умолчанию включены только «Объявления». Приходит короткий заголовок и ссылка на сайт.

<b>Актуальность</b>
Расписание обновляется раз в день, вечером в 20:00. Внизу каждого ответа указана дата последнего обновления и следующего. Если расписание изменилось в течение дня, напишите нам через /support, мы обновим вручную.

<b>Поддержка</b>
/support — написать нам, если нашли ошибку. Приложите фото или файл настоящего расписания, мы обновим его вручную.

Бот работает только в личных сообщениях.`;

// Разовая рассылка всем пользователям (по одному разу на id). Чтобы разослать новое — поменяйте id и текст.
// Идёт на остаток лимита (ночью — без звука), поэтому не мешает напоминаниям.
const UPDATE_TEXT = `🆕 <b>Первое обновление Мудлика</b>
Всем большое спасибо, что пользуетесь ботом и помогаете сделать его лучше! В этом обновлении добавлено:

🗺 <b>Карты кабинетов</b>
Команда /room 507 (или любой другой кабинет 5–7 этажа) присылает карту этажа с отмеченным кабинетом. Карты есть для блока B, 5–7 этажи.

🔔 <b>Карта перед парой</b>
В /notify можно включить «Карта кабинета за 20 минут до пары»: бот пришлёт фото с пометкой, где будет пара, и сразу время, кабинет и преподавателя. По умолчанию выключено.

🕗 <b>Расписание теперь обновляется раз в день, вечером в 20:00</b>
В конце каждого ответа указана дата последнего обновления и следующего. Новости с сайта проверяются в 8:00 и 20:00, расписание на следующую неделю приходит в воскресенье в 20:30.

⚠️ Если расписание в боте не совпадает с настоящим, напишите @aitufmstaff и пришлите фото или файл актуального расписания. Обновим вручную.

Все команды и их краткое описание: /info`;
const BROADCAST = { id: "upd1", text: UPDATE_TEXT };

async function handleMessage(env, msg) {
  const chat = msg.chat.id;
  const fl = flood(chat);
  if (fl === "drop") return;
  if (fl === "warn") return send(env, chat, "Слишком много запросов. Подождите минуту.");
  const text = (msg.text || "").trim();
  const cmd = text.split(/[\s@]/)[0].toLowerCase();
  if (cmd === "/info" || cmd === "/help") return send(env, chat, INFO_TEXT, { reply_markup: supportKb });
  if (cmd === "/support") return send(env, chat, "Возник вопрос или нашли ошибку в расписании? Напишите нам:", { reply_markup: supportKb });
  if (cmd === "/start" || cmd === "/group") return chooseGroup(env, chat);
  const u = await userGroup(env, chat);
  if (!u?.group_code) return chooseGroup(env, chat);
  const today = iso(nowLocal(env));
  const g = u.group_code;
  if (["/today", "/tomorrow", "/week", "/nextweek", "/next"].includes(cmd)) {
    const has = await env.DB.prepare("SELECT 1 FROM cur_lessons WHERE group_code=?1 LIMIT 1").bind(g).first();
    if (!has) return reply(env, chat, `Для группы <b>${esc(g)}</b> расписание пока не опубликовано на Moodle. Как только оно появится, оно будет здесь автоматически.`);
  }
  if (cmd === "/news") return send(env, chat, SUBS_TEXT, { reply_markup: await subsKeyboard(env, chat) });
  if (cmd === "/today") return reply(env, chat, await dayText(env, g, today, "📅 Сегодня"));
  if (cmd === "/tomorrow") return reply(env, chat, await dayText(env, g, iso(addDays(nowLocal(env), 1)), "📅 Завтра"));
  if (cmd === "/week") {
    const d = nowLocal(env); const mon = addDays(d, -((d.getUTCDay() + 6) % 7));
    return reply(env, chat, await rangeText(env, g, iso(mon), 6, "🗓 Эта неделя"));
  }
  if (cmd === "/nextweek") {
    const d = nowLocal(env); const mon = addDays(d, 7 - ((d.getUTCDay() + 6) % 7));
    return reply(env, chat, await rangeText(env, g, iso(mon), 6, "🗓 Следующая неделя"));
  }
  if (cmd === "/next") {
    const d = nowLocal(env); const m = d.getUTCHours() * 60 + d.getUTCMinutes();
    const l = await env.DB.prepare(
      "SELECT * FROM cur_lessons WHERE group_code=?1 AND (date>?2 OR (date=?2 AND end_min>?3)) ORDER BY date, start_min LIMIT 1")
      .bind(g, today, m).first();
    return reply(env, chat, l ? `⏭ Ближайшая пара — <b>${fmtDate(l.date)}</b>\n\n${lessonLine(l)}` : "Ближайших занятий в расписании нет.");
  }
  if (cmd === "/room") {
    const no = (text.split(/\s+/)[1] || "").replace(/\D/g, "");
    if (!no) return send(env, chat, "Напишите номер кабинета, например: /room 507");
    const r = ROOMS[no];
    if (!r) return send(env, chat, `Карты для кабинета ${esc(no)} пока нет. Карты есть для кабинетов 5–7 этажей.`);
    const cap = r.hl ? `🗺 Кабинет <b>${no}</b> · ${r.floor} этаж` : `🗺 Кабинет <b>${no}</b> · ${r.floor} этаж (общая схема этажа)`;
    const ph = await sendPhoto(env, chat, mapUrl(no), cap);
    return ph?.ok ? ph : send(env, chat, `Кабинет <b>${no}</b> — ${r.floor} этаж.`);
  }
  if (cmd === "/notify") {
    if (/\b(off|on)\b|выкл|вкл/i.test(text.slice(8))) {
      const on = /off|выкл|0/i.test(text.slice(8)) ? 0 : 1;
      await env.DB.prepare("UPDATE users SET notify=?2 WHERE chat_id=?1").bind(chat, on).run();
      return send(env, chat, on ? "🔔 Уведомления включены." : "🔕 Уведомления выключены. Вернуть: /notify on");
    }
    return send(env, chat, NOTIFY_TEXT, { reply_markup: await notifyKeyboard(env, chat) });
  }
  return send(env, chat,
    `Ваша группа: <b>${esc(g)}</b>\n\n/today — сегодня\n/tomorrow — завтра\n/week — неделя\n/nextweek — следующая неделя\n/next — ближайшая пара\n/group — сменить группу\n/news — подписка на новости и объявления\n/notify — настройки уведомлений\n/room 507 — карта кабинета\n/support — написать в поддержку`, { reply_markup: supportKb });
}

async function handleCallback(env, cb) {
  if (!cb.message?.chat || cb.message.chat.type !== "private" || typeof cb.data !== "string" || cb.data.length > 64) return;
  const chat = cb.message.chat.id;
  if (cb.from?.id !== chat) return;
  if (flood(chat)) return tg(env, "answerCallbackQuery", { callback_query_id: cb.id, text: "Слишком часто, подождите минуту" });
  if (cb.data?.startsWith("s:")) {
    const feed = cb.data.slice(2);
    await tg(env, "answerCallbackQuery", { callback_query_id: cb.id });
    if (!FEEDS[feed]) return;
    const had = await env.DB.prepare("SELECT 1 FROM subs WHERE chat_id=?1 AND feed=?2").bind(chat, feed).first();
    if (had) await env.DB.prepare("DELETE FROM subs WHERE chat_id=?1 AND feed=?2").bind(chat, feed).run();
    else await env.DB.prepare("INSERT INTO subs(chat_id,feed,since) VALUES(?1,?2,?3)").bind(chat, feed, nowSec(env)).run();
    return tg(env, "editMessageReplyMarkup", { chat_id: chat, message_id: cb.message.message_id, reply_markup: await subsKeyboard(env, chat) });
  }
  if (cb.data === "n:t" || cb.data === "n:m") {
    await tg(env, "answerCallbackQuery", { callback_query_id: cb.id });
    const col = cb.data === "n:t" ? "notify" : "map_pre";
    await env.DB.prepare(`UPDATE users SET ${col} = 1 - COALESCE(${col},0) WHERE chat_id=?1`).bind(chat).run();
    return tg(env, "editMessageReplyMarkup", { chat_id: chat, message_id: cb.message.message_id, reply_markup: await notifyKeyboard(env, chat) });
  }
  if (cb.data?.startsWith("g:")) {
    const code = cb.data.slice(2);
    const ok = await env.DB.prepare("SELECT 1 FROM groups WHERE code=?1").bind(code).first();
    await tg(env, "answerCallbackQuery", { callback_query_id: cb.id });
    if (!ok) return send(env, chat, "Такой группы нет.");
    const existed = await env.DB.prepare("SELECT 1 FROM users WHERE chat_id=?1").bind(chat).first();
    await env.DB.prepare(
      "INSERT INTO users(chat_id, group_code, notify) VALUES(?1,?2,1) ON CONFLICT(chat_id) DO UPDATE SET group_code=?2").bind(chat, code).run();
    if (!existed) for (const f of DEFAULT_FEEDS) await env.DB.prepare("INSERT OR IGNORE INTO subs(chat_id,feed,since) VALUES(?1,?2,?3)").bind(chat, f, nowSec(env)).run();
    return send(env, chat, `✅ Группа <b>${esc(code)}</b> сохранена.\nУведомления включены: воскресенье — расписание недели, за час до первой пары, за 15 минут до каждой.\n\nНовости и объявления: /news\n/today /week /next /notify /support`, { reply_markup: supportKb });
  }
}

// ---------------------------------------------------------------- cron
// Лимит бесплатного плана — 50 подзапросов на запуск. Бюджет: ≤5 SELECT + 1 пакетная отметка + ≤40 отправок + 1 откат.
// Если получателей больше, чем помещается в минуту, «ранние» окна (за 16–22 мин до пары, за 61–75 до дня)
// разбирают очередь партиями; когда народу мало — ждём точного времени.
const MAX_SENDS = 38;
const pick = (cands, T, budget) => {
  const tt = (c) => c.T ?? T;
  const due = cands.filter((c) => c.lead <= tt(c)).sort((a, b) => a.lead - b.lead);
  let out = due.slice(0, budget);
  if (cands.length > budget) out = out.concat(cands.filter((c) => c.lead > tt(c)).sort((a, b) => a.lead - b.lead).slice(0, budget - out.length));
  return out;
};
const groupBy = (rows, f) => { const m = new Map(); for (const r of rows) { const k = f(r); (m.get(k) || m.set(k, []).get(k)).push(r); } return m; };

async function tick(env) {
  const d = nowLocal(env), today = iso(d), m = d.getUTCHours() * 60 + d.getUTCMinutes();
  let budget = MAX_SENDS;
  const jobs = []; // {chat_id, key, text}

  // 1) за 15 минут до пары (рассылка допускается с 22-й минуты, если очередь большая)
  {
    const { results } = await env.DB.prepare(
      `SELECT u.chat_id, u.map_pre, l.* FROM users u JOIN cur_lessons l ON l.group_code=u.group_code
       WHERE u.notify=1 AND l.date=?1 AND l.start_min-?2 BETWEEN 5 AND CASE WHEN u.map_pre=1 THEN 27 ELSE 22 END
         AND NOT EXISTS (SELECT 1 FROM sent s WHERE s.chat_id=u.chat_id AND s.k='pre:'||l.date||':'||l.start_min)
       ORDER BY l.start_min, u.chat_id, l.sub`).bind(today, m).all();
    const cands = [...groupBy(results, (r) => r.chat_id + "|" + r.start_min).values()].map((rows) => {
      const withMap = rows[0].map_pre === 1;
      const nos = [...new Set(rows.map(roomNo).filter(Boolean))];
      const photo = withMap && nos.length === 1 && rows.every((r) => roomNo(r) === nos[0]) ? mapUrl(nos[0]) : null;
      const target = withMap ? MAP_LEAD : 15;
      const text = `⏰ Через ${rows[0].start_min - m} мин:\n\n` + rows.map((l) => lessonLine(l)).join("\n\n");
      return { chat_id: rows[0].chat_id, key: `pre:${today}:${rows[0].start_min}`, lead: rows[0].start_min - m - target,
        T: 0, text, photo: photo && text.length <= 1000 ? photo : null };
    });
    for (const c of pick(cands, 0, budget)) { jobs.push(c); budget--; }
  }

  // 2) за час до первой пары — расписание на сегодня
  if (budget > 0) {
    const { results: us } = await env.DB.prepare(
      `SELECT u.chat_id, u.group_code, x.f FROM users u
       JOIN (SELECT group_code, MIN(start_min) f FROM cur_lessons WHERE date=?1 GROUP BY group_code) x ON x.group_code=u.group_code
       WHERE u.notify=1 AND x.f-?2 BETWEEN 30 AND 75
         AND NOT EXISTS (SELECT 1 FROM sent s WHERE s.chat_id=u.chat_id AND s.k='day:'||?1)`).bind(today, m).all();
    const cands = us.map((u) => ({ ...u, key: "day:" + today, lead: u.f - m - 60 })); // lead<=0 <=> до первой пары ≤60 мин
    const chosen = pick(cands, 0, budget);
    if (chosen.length) {
      const gs = [...new Set(chosen.map((c) => c.group_code))];
      const { results } = await env.DB.prepare(
        `SELECT * FROM cur_lessons WHERE date=?1 AND group_code IN (${gs.map((_, i) => "?" + (i + 2)).join(",")}) ORDER BY start_min, sub`)
        .bind(today, ...gs).all();
      const byG = groupBy(results, (r) => r.group_code);
      for (const c of chosen) { jobs.push({ chat_id: c.chat_id, key: c.key, text: dayFromRows(c.group_code, today, byG.get(c.group_code) || [], "☀️ Сегодня") }); budget--; }
    }
  }

  // 2b) новости и объявления с msu.kz: заголовок + ссылка
  if (budget > 0) {
    const { results } = await env.DB.prepare(
      `SELECT DISTINCT s.chat_id, n.id, n.feed, n.title, n.url FROM subs s
         JOIN users u ON u.chat_id=s.chat_id AND u.notify=1
         JOIN news n ON instr(n.feeds, ','||s.feed||',')>0
       WHERE n.seen >= s.since AND n.seen >= ?1
         AND NOT EXISTS (SELECT 1 FROM sent x WHERE x.chat_id=s.chat_id AND x.k='n:'||n.id)
       ORDER BY n.id LIMIT ?2`).bind(nowSec(env) - NEWS_MAX_AGE, budget).all();
    for (const r of results) {
      if (!okUrl(r.url)) continue; // ссылки только на msu.kz
      jobs.push({ chat_id: r.chat_id, key: "n:" + r.id,
        text: `${FEEDS[r.feed] || "📰"}\n<b>${esc(r.title)}</b>\n<a href="${esc(r.url)}">Читать на сайте</a>` });
      budget--;
    }
  }

  // 3) воскресенье 20:30–21:30 — расписание следующей недели (после вечернего обновления в 20:00)
  if (budget > 0 && d.getUTCDay() === 0 && m >= 20 * 60 + 30 && m < 21 * 60 + 30) {
    const { results: us } = await env.DB.prepare(
      `SELECT u.chat_id, u.group_code FROM users u WHERE u.notify=1 AND u.group_code IS NOT NULL
         AND NOT EXISTS (SELECT 1 FROM sent s WHERE s.chat_id=u.chat_id AND s.k='sun:'||?1) LIMIT ?2`).bind(today, budget).all();
    if (us.length) {
      const mon = iso(addDays(d, 1)), sat = iso(addDays(d, 6));
      const gs = [...new Set(us.map((c) => c.group_code))];
      const { results } = await env.DB.prepare(
        `SELECT * FROM cur_lessons WHERE date BETWEEN ?1 AND ?2 AND group_code IN (${gs.map((_, i) => "?" + (i + 3)).join(",")}) ORDER BY date, start_min, sub`)
        .bind(mon, sat, ...gs).all();
      const byG = groupBy(results, (r) => r.group_code);
      for (const u of us) jobs.push({ chat_id: u.chat_id, key: "sun:" + today, text: rangeFromRows(u.group_code, byG.get(u.group_code) || [], "🗓 Расписание на неделю") });
    }
  }
  // 4) понедельник 08:00–08:30 — пожелание удачной недели
  if (budget > 0 && d.getUTCDay() === 1 && m >= 8 * 60 && m < 8 * 60 + 30) {
    const { results: us } = await env.DB.prepare(
      `SELECT u.chat_id FROM users u WHERE u.notify=1 AND u.group_code IS NOT NULL
         AND NOT EXISTS (SELECT 1 FROM sent s WHERE s.chat_id=u.chat_id AND s.k='mon:'||?1) LIMIT ?2`).bind(today, budget).all();
    const text = GREETINGS[Math.floor(Date.parse(today) / 604800000) % GREETINGS.length];
    for (const u of us) { jobs.push({ chat_id: u.chat_id, key: "mon:" + today, text }); budget--; }
  }
  // 5) разовая рассылка (ниже по приоритету, чем всё остальное)
  if (budget > 0 && BROADCAST) {   // ночью (22:00–08:00) уходит без звука
    const { results: us } = await env.DB.prepare(
      `SELECT u.chat_id FROM users u WHERE u.notify=1
         AND NOT EXISTS (SELECT 1 FROM sent s WHERE s.chat_id=u.chat_id AND s.k=?1) LIMIT ?2`).bind("bc:" + BROADCAST.id, Math.min(budget, 20)).all();
    for (const u of us) { jobs.push({ chat_id: u.chat_id, key: "bc:" + BROADCAST.id, text: BROADCAST.text, extra: (m < 8 * 60 || m >= 22 * 60) ? { disable_notification: true } : {} }); budget--; }
  }
  if (!jobs.length) return;
  // длинные сообщения (неделя) уходят несколькими частями: не выходим за лимит подзапросов
  let used = 0; const fit = [];
  for (const j of jobs) { const n = j.photo ? 2 : parts(j.text).length; if (used + n > MAX_SENDS) break; used += n; fit.push(j); }
  jobs.length = 0; jobs.push(...fit);

  // сначала отметка (один пакетный запрос), потом отправка: при сбое лучше пропуск, чем дубль
  await env.DB.batch(jobs.map((j) => env.DB.prepare("INSERT OR IGNORE INTO sent(chat_id,k) VALUES(?1,?2)").bind(j.chat_id, j.key)));
  const undo = [];
  for (let i = 0; i < jobs.length; i += 10) {           // по 10 параллельно, Telegram терпит ~30 сообщений/сек
    await Promise.all(jobs.slice(i, i + 10).map(async (j) => {
      let r;
      if (j.photo) {
        r = await sendPhoto(env, j.chat_id, j.photo, j.text);
        if (!r?.ok && r?.error_code === 400) r = await send(env, j.chat_id, j.text);   // картинка недоступна — шлём обычный текст
      } else r = await send(env, j.chat_id, j.text, j.extra || {});
      const c = r?.error_code ?? 0;
      if (r?.ok) return;
      if (c === 403) undo.push(env.DB.prepare("UPDATE users SET notify=0 WHERE chat_id=?1").bind(j.chat_id)); // бот заблокирован
      else if (c === 0 || c === 429 || c >= 500)                                                                  // временный сбой — повторим
        undo.push(env.DB.prepare("DELETE FROM sent WHERE chat_id=?1 AND k=?2").bind(j.chat_id, j.key));
      // 400 и прочие постоянные ошибки не повторяем, чтобы одно «битое» сообщение не съедало лимит каждую минуту
    }));
  }
  if (undo.length) await env.DB.batch(undo);
  if (m === 4 * 60) {
    await env.DB.prepare("DELETE FROM sent WHERE k NOT LIKE 'n:%' AND k NOT LIKE 'bc:%' AND k NOT LIKE '%'||?1||'%'").bind(today).run();
    await env.DB.prepare("DELETE FROM sent WHERE k LIKE 'n:%' AND CAST(substr(k,3) AS INTEGER) IN (SELECT id FROM news WHERE seen < ?1)").bind(nowSec(env) - 7 * 86400).run();
  }
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (url.pathname === "/setup") {
      if (!safeEq(url.searchParams.get("key") || "", env.SETUP_KEY)) return new Response("not found", { status: 404 });
      if (!/^[A-Za-z0-9_-]{16,256}$/.test(env.WEBHOOK_SECRET || ""))
        return new Response("WEBHOOK_SECRET: 16–256 символов, только A-Z a-z 0-9 _ -", { status: 500 });
      const a = await tg(env, "setWebhook", { url: `${url.origin}/webhook`, secret_token: env.WEBHOOK_SECRET,
        allowed_updates: ["message", "callback_query"], drop_pending_updates: true, max_connections: 20 });
      const b = await tg(env, "setMyCommands", { commands: [
        { command: "today", description: "Расписание на сегодня" }, { command: "tomorrow", description: "На завтра" },
        { command: "week", description: "На неделю" }, { command: "nextweek", description: "Следующая неделя" },
        { command: "next", description: "Ближайшая пара" }, { command: "group", description: "Сменить группу" },
        { command: "news", description: "Подписка на новости и объявления" }, { command: "notify", description: "Настройки уведомлений" }, { command: "room", description: "Карта кабинета: /room 507" }, { command: "info", description: "Что умеет бот" }, { command: "support", description: "Написать в поддержку" }] });
      return Response.json({ setWebhook: a, setMyCommands: b });
    }
    if (req.method === "POST" && url.pathname === "/webhook") {
      // Telegram присылает секрет в заголовке; без него — 404, тело даже не читаем
      if (!safeEq(req.headers.get("x-telegram-bot-api-secret-token") || "", env.WEBHOOK_SECRET)) return new Response("not found", { status: 404 });
      if (Number(req.headers.get("content-length") || 0) > 100000) return new Response("ok");
      try {
        const up = await req.json();
        const msg = up?.message;
        if (msg?.chat && msg.chat.type !== "private") {
          // бот работает только в личке: из групп и каналов выходим, чтобы его не использовали для спама
          if (msg.chat.type === "group" || msg.chat.type === "supergroup") await tg(env, "leaveChat", { chat_id: msg.chat.id });
        } else if (typeof msg?.text === "string" && msg.text.length <= 1000) await handleMessage(env, msg);
        else if (up?.callback_query) await handleCallback(env, up.callback_query);
      } catch (e) { console.log("update error", e?.message); } // всегда 200, иначе Telegram будет слать то же обновление снова
      return new Response("ok");
    }
    if (url.pathname === "/tick" && env.DEV === "1" && safeEq(url.searchParams.get("key") || "", env.SETUP_KEY)) { // только локальные тесты
      await tick({ ...env, FAKE_NOW: url.searchParams.get("now") || env.FAKE_NOW }); return new Response("ticked");
    }
    return new Response("not found", { status: 404 });
  },
  async scheduled(_e, env, ctx) { ctx.waitUntil(tick(env)); },
};
