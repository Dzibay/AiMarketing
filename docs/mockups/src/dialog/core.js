/* Полный макет варианта 2 «Диалог»: состояние, общие детали интерфейса и запуск. */

const S = {
  screen: 'home', expert: false, theme: 'light', themeSet: false, project: null,
  chat: 'c1', panel: true, tab: 'changes', stage: 2, stageDone: 2, camp: 2,
  menu: null, modal: null, toast: null, drawer: false, model: 1, step: 1, mapY: 0,
  decided: {}, sel: new Set(), minus: new Set(), verdicts: {}, marked: {},
  sub: {}, form: {}, log: [], gone: [], revert: null,
  flags: { digest: true, tg: true, 'rule:r1': true, 'rule:r2': true, 'rule:r3': true, compare: true, 'aud:1': true, 'aud:2': true },
}

const TITLES = {
  home: 'Главная', chat: 'Чат с ИИ', camps: 'Кампании', camp: 'Кампания', wizard: 'Новая кампания', queries: 'Запросы', reports: 'Отчёты',
  auto: 'Автопилот', history: 'История', project: 'Настройки проекта', connect: 'Подключения', settings: 'Настройки', plan: 'Тариф и лимит',
  map: 'Карта функций', start: 'Первый вход',
}
/* Разделы, которых нет в обычном режиме. */
const EXPERT_ONLY = ['auto', 'wizard']
const CHAT_TASK = { c1: 't3', c2: 't4', c3: 't5' }
const TASK_CHAT = { t2: 'c1', t3: 'c1', t4: 'c2', t5: 'c3', t6: 'c2' }

const plural = (n, a, b, c) => (n % 10 === 1 && n % 100 !== 11 ? a : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? b : c)
const pro = () => S.expert
const on = (k) => !!S.flags[k]
const sub = (k, def) => S.sub[k] || def
const val = (k, def) => (S.form[k] != null ? S.form[k] : def)
const attr = (s) => esc(s).replace(/"/g, '&quot;')
const visibleCampaigns = () => M.campaigns.filter((c) => !c.deleted && (!S.project || c.project === S.project))
const visibleTasks = () => M.tasks.filter((t) => !S.project || t.project === S.project || !t.project)
const pendingCount = () => M.tasks.filter((t) => t.group === 'ai' && !S.decided[t.id]).length
const chatNow = () => M.chats.find((c) => c.id === S.chat) || M.chats[0]
const allMinus = () => Object.keys(M.reasons).every((k) => S.minus.has(k))
/** Итоги под выбранный проект (без проекта — по всем). */
const totals = () => {
  const p = S.project && project(S.project)
  return p ? { ...M.totals, today: p.today, yesterday: p.yesterday, week: p.week, conv: p.conv, cpa: Math.round(p.week / p.conv) } : M.totals
}
/** Решение по запросу: выбор пользователя важнее разметки ИИ. */
const verdictOf = (q) => S.verdicts[q.id] || q.v

/* ---------- общие детали ---------- */

/** Статус всегда с иконкой и словом, не только цветом. */
const ST_ICON = { ok: 'check', warn: 'alert', bad: 'alert', off: 'pause' }
const badge = (st, text) => `<span class="st st--${st}">${ic(ST_ICON[st], 13)}${text || STATUS[st]}</span>`
const rejected = `<span class="st st--off">${ic('x', 13)}Отклонено</span>`
const swBtn = (act, isOn, label) => `<button class="switch ${isOn ? 'on' : ''}" role="switch" aria-checked="${!!isOn}" aria-label="${attr(label)}" data-act="${act}"></button>`
const checkBtn = (act, isOn, label) => `<button class="check ${isOn ? 'on' : ''}" role="checkbox" aria-checked="${!!isOn}" aria-label="${attr(label)}" data-act="${act}">${ic('check', 13)}</button>`
/** Строка «название и пояснение — выключатель». */
const toggleRow = (flag, title, text) => `<div class="trow"><div><b>${title}</b>${text ? `<p>${text}</p>` : ''}</div>${swBtn(`flag:${flag}`, on(flag), title)}</div>`
/** Вкладки внутри раздела. */
const ptabs = (k, list, def) => `<div class="tabs ptabs">${list.map(([id, label]) => `<button class="${sub(k, def) === id ? 'active' : ''}" data-act="sub:${k}=${id}">${label}</button>`).join('')}</div>`
/** Переключатель из двух-четырёх значений. */
const segm = (k, list, def) => `<div class="seg">${list.map(([id, label]) => `<button class="${val(k, def) === id ? 'active' : ''}" data-act="set:${k}=${id}">${label}</button>`).join('')}</div>`
/** Выпадающий список. */
function dd(k, list, def, pre = '') {
  const cur = val(k, def)
  const menu = S.menu === `dd-${k}` ? `<div class="menu">${list.map((o) => `<button data-act="set:${k}=${attr(o)}">${o}${o === cur ? ic('check', 15) : ''}</button>`).join('')}</div>` : ''
  return `<div class="pop"><button class="chip chip--btn" data-act="menu:dd-${k}">${pre}${cur}${ic('down', 14)}</button>${menu}</div>`
}
/** Поле формы: значение переживает перерисовку экрана. */
function field(k, label, def, o = {}) {
  const input = o.area
    ? `<textarea data-k="${k}" rows="${o.rows || 3}" placeholder="${attr(o.ph || '')}">${esc(val(k, def))}</textarea>`
    : `<span class="inp"><input data-k="${k}" value="${attr(val(k, def))}" placeholder="${attr(o.ph || '')}"${o.w ? ` style="width:${o.w}px"` : ''}>${o.unit ? `<i>${o.unit}</i>` : ''}</span>`
  return `<label class="field ${o.cls || ''}"><span>${label}</span>${input}${o.hint ? `<small>${o.hint}</small>` : ''}</label>`
}
/** Подсказка обычного режима: где лежит то, чего здесь не видно. */
const expertHint = (text) => (pro() ? '' : `<p class="xhint">${ic('sliders', 15)}<span>${text} — в <button data-act="to:settings=ui">экспертном режиме</button>.</span></p>`)
/** Функция, которой нет в текущем тарифе. */
const lockChip = (plan) => `<button class="chip chip--btn chip--lock" data-act="modal:upgrade">${ic('lock', 13)}В тарифе «${plan}»</button>`
const tile = (label, value, note, cls = '') => `<div class="tile"><small>${label}</small><b class="num">${value}</b>${note ? `<span class="${cls}">${note}</span>` : ''}</div>`
const dl = (rows) => `<dl class="dl">${rows.map(([a, b]) => `<div><dt>${a}</dt><dd>${b}</dd></div>`).join('')}</dl>`
const diffTable = (t) => `<table class="diff num">${t.diff.map((d) => `<tr><td>${d[0]}</td><td><s>${d[1]}</s> → <b>${d[2]}</b></td></tr>`).join('')}</table>`

/* ---------- боковая панель и верхняя строка ---------- */

function side() {
  const waiting = M.chats.filter((c) => CHAT_TASK[c.id] && !S.decided[CHAT_TASK[c.id]])
  const rest = M.chats.filter((c) => !waiting.includes(c))
  const nav = [['home', 'home', 'Главная'], ['camps', 'megaphone', 'Кампании'], ['queries', 'filter', 'Запросы'], ['reports', 'chart', 'Отчёты']]
  if (pro()) nav.push(['auto', 'zap', 'Автопилот'])
  nav.push(['history', 'history', 'История'])
  const cur = { camp: 'camps', wizard: 'camps' }[S.screen] || S.screen
  const chatRow = (c, wait) => `<button class="srow ${S.screen === 'chat' && S.chat === c.id ? 'active' : ''}" data-act="chat:${c.id}">${wait ? '<i class="pin"></i>' : ''}<span class="t">${c.title}</span></button>`
  const prj = (p) => `<div class="srow-wrap"><button class="srow ${S.project === p.id ? 'active' : ''}" data-act="project:${p.id}" title="${STATUS[p.status]}"><span class="dot dot--${p.status}"></span><span class="t">${p.name}</span></button>
    ${S.project === p.id ? `<button class="srow__gear" data-act="go:project" title="Настройки проекта" aria-label="Настройки проекта">${ic('gear', 15)}</button>` : ''}</div>`
  /* В экспертном режиме проекты разложены по папкам клиентов. */
  const clients = [...new Set(M.projects.map((p) => p.client))]
  const projects = pro() ? clients.map((c) => `<h5>${c || 'Без клиента'}</h5>${M.projects.filter((p) => p.client === c).map(prj).join('')}`).join('') : M.projects.map(prj).join('')
  return `<aside class="side"><div class="side__scroll" data-scroll="side">
    <button class="brand" data-act="go:home"><i>${ic('sparkles', 16)}</i>AiMarketing${pro() ? '<em>эксперт</em>' : ''}</button>
    <button class="newchat" data-act="go:home">${ic('plus', 17)}Новый чат</button>
    ${nav.map(([k, i, label]) => `<button class="srow ${cur === k ? 'active' : ''}" data-act="go:${k}">${ic(i, 17)}<span class="t">${label}</span>${k === 'queries' && !allMinus() ? `<span class="badge">${M.queryTotals.no}</span>` : ''}</button>`).join('')}
    <h4>Проекты</h4>
    <button class="srow ${S.project ? '' : 'active'}" data-act="project:">${ic('folder', 16)}<span class="t">Все проекты</span></button>
    ${projects}
    <button class="srow srow--quiet" data-act="go:connect">${ic('plus', 16)}<span class="t">Добавить сайт</span></button>
    ${waiting.length ? `<h4>Ждут вашего решения · ${waiting.length}</h4>${waiting.map((c) => chatRow(c, true)).join('')}` : ''}
    <h4>Недавние чаты</h4>${rest.map((c) => chatRow(c)).join('')}
  </div>
  <button class="side__foot" data-act="go:plan" title="Сколько помощника осталось в этом месяце. Нажмите, чтобы открыть тариф">
    <span class="limit"><span>Лимит ИИ</span><b class="num">${M.plan.used}%</b></span>
    <span class="limit__bar"><i style="width:${M.plan.used}%"></i></span>
    <small>Тариф ${M.plan.name} до ${M.plan.until}</small>
  </button></aside>`
}

function mbar() {
  const p = S.project && project(S.project)
  const c = chatNow()
  const n = pendingCount()
  const cp = c.project && project(c.project)
  const title = S.screen === 'chat'
    ? `<b>${c.title}</b><span class="chip">${cp ? `<span class="dot dot--${cp.status}"></span>${cp.name}` : `${ic('folder', 14)}Все проекты`}</span>`
    : p ? `<button class="chip chip--btn" data-act="go:project" title="Открыть настройки проекта"><span class="dot dot--${p.status}"></span>${p.name}${ic('gear', 14)}</button>`
      : `<span class="chip">${ic('folder', 14)}Все проекты</span>`
  const chatMenu = `<div class="pop"><button class="iconbtn" data-act="menu:chat" aria-label="Действия с чатом" title="Действия с чатом">${ic('more')}</button>
    ${S.menu === 'chat' ? `<div class="menu menu--right"><button data-act="toast:Название чата можно изменить прямо в списке слева">${ic('edit', 16)}Переименовать</button><button data-act="modal:delchat">${ic('trash', 16)}Удалить чат</button></div>` : ''}</div>`
  const me = `<div class="menu menu--right"><div class="menu__me"><b>${M.user.name}</b><small>${M.user.email}</small></div>
    <button data-act="go:settings">${ic('user', 16)}Настройки</button>
    <button data-act="go:connect">${ic('plug', 16)}Подключения</button>
    <button data-act="go:plan">${ic('card', 16)}Тариф и лимит</button>
    <button data-act="modal:help">${ic('help', 16)}Помощь и поддержка</button>
    <hr><button data-act="toast:В макете выйти нельзя">${ic('logout', 16)}Выйти</button></div>`
  return `<header class="mbar"><button class="iconbtn burger" data-act="drawer" aria-label="Меню">${ic('menu')}</button>
    <div class="mbar__title">${title}</div>
    ${pro() ? `<button class="sync" data-act="go:connect" title="Когда обновлялись данные Директа и Метрики">${ic('refresh', 14)}<span>Данные: 10 минут назад</span></button>` : ''}
    ${n && S.screen !== 'home' ? `<button class="chip chip--btn chip--lime" data-act="go:home" title="Предложения ИИ, которые ждут вашего решения">Ждёт решения: ${n}</button>` : ''}
    ${S.screen === 'chat' ? `${chatMenu}${hasCanvas() ? `<button class="iconbtn ${S.panel ? 'on' : ''}" data-act="panel" title="Панель с данными рядом с чатом" aria-label="Панель с данными">${ic('panel')}</button>` : ''}` : ''}
    <button class="iconbtn" data-act="theme" aria-label="Сменить тему" title="Светлая или тёмная тема">${ic(S.theme === 'dark' ? 'sun' : 'moon')}</button>
    <div class="pop"><button class="avatar" data-act="menu:me" aria-label="Профиль и настройки" title="Профиль и настройки">${M.user.name[0]}</button>${S.menu === 'me' ? me : ''}</div>
  </header>`
}

/** Поле вопроса: проект, класс модели, в экспертном режиме — права ИИ. */
function composer(big, placeholder) {
  const m = M.models[S.model]
  return `<div class="composer ${big ? 'composer--big' : ''}">
    <textarea rows="${big ? 2 : 1}" placeholder="${placeholder}${pro() ? ' @ — сослаться на другой проект' : ''}"></textarea>
    <div class="composer__row">
      ${big ? `<span class="chip">${ic('folder', 14)}${projectName(S.project)}</span>` : ''}
      <button class="chip chip--btn" data-act="model:next" title="Класс модели: чем больше точек, тем больше лимита тратит ответ">${m[0]}${dots(m[1])}</button>
      ${pro() ? `<button class="chip chip--btn" data-act="flag:full" title="Права ИИ в этом чате. С согласованием — каждое изменение ждёт вашего «Выполнить»">${ic(on('full') ? 'zap' : 'check', 14)}${on('full') ? 'Полный доступ' : 'С согласованием'}</button>` : ''}
      <button class="send" data-act="chat:c1" aria-label="Отправить">${ic('up')}</button>
    </div>
  </div>`
}

/* ---------- окна поверх экрана ---------- */

const MODALS = {
  help: () => ({
    title: 'Помощь и поддержка',
    body: `<p class="muted">Опишите, что не получается. Ответим в этом окне и продублируем на почту ${M.user.email}.</p>${field('help', 'Ваш вопрос', '', { area: true, rows: 4, ph: 'Например: не вижу кампанию в списке' })}`,
    foot: `<button class="btn btn--pri" data-act="toast:Сообщение отправлено. Обычно отвечаем в течение часа" data-close>Отправить</button><button class="btn" data-act="close">Отмена</button>`,
  }),
  upgrade: () => ({
    title: 'Эта функция есть в тарифе «Бизнес»',
    body: `<p>Сейчас у вас тариф «${M.plan.name}». В «Бизнесе» добавятся генерация картинок, до 20 сайтов и лимит ИИ в три раза больше.</p><p class="muted">Доплата считается за оставшиеся дни. Сменить тариф обратно можно в любой момент.</p>`,
    foot: `<button class="btn btn--pri" data-act="go:plan" data-close>Сравнить тарифы</button><button class="btn" data-act="close">Не сейчас</button>`,
  }),
  delchat: () => ({
    title: 'Удалить чат?',
    body: `<p>Чат «${chatNow().title}» исчезнет из списка. Изменения, которые вы уже выполнили, останутся в силе и в «Истории».</p>`,
    foot: `<button class="btn btn--danger" data-act="toast:Чат удалён" data-then="home" data-close>Удалить</button><button class="btn" data-act="close">Отмена</button>`,
  }),
}

function modal() {
  if (!S.modal) return ''
  const i = S.modal.indexOf(':')
  const name = i < 0 ? S.modal : S.modal.slice(0, i)
  const m = MODALS[name](i < 0 ? '' : S.modal.slice(i + 1))
  return `<div class="scrim" data-act="close"><div class="modal" role="dialog" aria-modal="true" aria-label="${attr(m.title)}" data-act="noop">
    <header><h3>${m.title}</h3><button class="iconbtn" data-act="close" aria-label="Закрыть">${ic('x')}</button></header>
    <div class="modal__body">${m.body}</div><footer>${m.foot}</footer></div></div>`
}

const toast = () => (S.toast ? `<div class="toast" role="status">${ic('check', 16)}<span>${S.toast}</span>${S.toast.includes('Истори') && S.screen !== 'history' ? `<button data-act="go:history">Открыть историю</button>` : ''}</div>` : '')

/** Предупреждение о лимите: появляется само на 80% и 100%. */
const banner = () => (on('limit80') ? `<div class="banner">${ic('alert', 16)}<span><b>Использовано 80% лимита ИИ.</b> До обновления 12 ноября хватит примерно на 40 ответов стандартной модели. Сигналы и отчёты работают без лимита.</span><button class="btn btn--sm" data-act="go:plan">Тарифы</button><button class="iconbtn" data-act="flag:limit80" aria-label="Скрыть">${ic('x', 16)}</button></div>` : '')

const lockedScreen = () => `<div class="page" data-scroll="main"><div class="col empty">${ic('sliders', 30)}<h2>«${TITLES[S.screen]}» — раздел экспертного режима</h2>
  <p>В обычном режиме его нет: сервис сам следит за цифрами и показывает сигналы на главной.</p>
  <div><button class="btn btn--pri" data-act="expert:on">Включить экспертный режим</button><button class="btn" data-act="go:home">На главную</button></div></div></div>`

function render() {
  if (S.screen === 'map') return mapPage()
  if (S.screen === 'start') return startPage()
  const SCREENS = { home, chat, camps, camp, wizard, queries, reports, auto, history: historyPage, project: projectSettings, connect, settings, plan }
  const body = EXPERT_ONLY.includes(S.screen) && !pro() ? lockedScreen() : (SCREENS[S.screen] || home)()
  return `<div class="shell ${S.drawer ? 'is-drawer' : ''}">${side()}<button class="drawer-scrim" data-act="drawer" aria-label="Закрыть меню"></button>
    <div class="main">${mbar()}${banner()}${body}</div>${modal()}${toast()}</div>`
}

/* ---------- действия ---------- */

const ACT = {
  go: (v) => {
    S.screen = v
    S.sel.clear()
    S.drawer = false
  },
  /* Раздел и вкладка в нём: «to:settings=ui». */
  to: (v) => {
    const [scr, tab] = v.split('=')
    ACT.go(scr)
    S.sub[scr] = tab
  },
  /* Проект и раздел: «at:okna=queries». */
  at: (v) => {
    const [p, scr] = v.split('=')
    S.project = p || null
    ACT.go(scr)
  },
  sub: (v) => {
    const [k, tab] = v.split('=')
    S.sub[k] = tab
  },
  set: (v) => {
    const i = v.indexOf('=')
    S.form[v.slice(0, i)] = v.slice(i + 1)
  },
  flag: (v) => (S.flags[v] = !S.flags[v]),
  expert: (v) => {
    S.expert = v === 'on' ? true : v === 'off' ? false : !S.expert
    S.toast = S.expert ? 'Экспертный режим включён: появились фильтры, массовые действия и «Автопилот»' : 'Экспертный режим выключен: остался только простой вид'
  },
  theme: (v) => {
    S.theme = v || (S.theme === 'dark' ? 'light' : 'dark')
    S.themeSet = true
  },
  project: (v) => {
    S.project = v || null
    S.drawer = false
    S.sel.clear()
    if (S.screen === 'camp' || S.screen === 'wizard') S.screen = 'camps'
  },
  /* Переход из карты функций: «jump:reports,okna,tab-pl,expert». */
  jump: (v) => {
    S.sel.clear()
    /* Ссылка без вкладки открывает первую вкладку раздела, а не ту, что была открыта раньше. */
    delete S.sub[parseState(v).screen]
    applyState(v)
  },
  menu: (v) => (S.menu = S.menu === v ? null : v),
  drawer: () => (S.drawer = !S.drawer),
  modal: (v) => (S.modal = v),
  close: () => (S.modal = null),
  toast: (v) => (S.toast = v),
  chat: (v) => {
    Object.assign(S, { chat: v, screen: 'chat', panel: true, tab: 'changes', drawer: false })
  },
  panel: () => (S.panel = !S.panel),
  tab: (v) => {
    S.tab = v
    S.panel = true
  },
  done: (v) => {
    S.decided[v] = 'done'
    S.toast = M.tasks.find((t) => t.id === v).group === 'ai' ? 'Выполнено. Вернуть как было можно в «Истории»' : 'Готово. Запись появилась в «Истории»'
  },
  reject: (v) => (S.decided[v] = 'rejected'),
  later: (v) => (S.decided[v] = 'later'),
  undo: (v) => delete S.decided[v],
  sel: (v) => (S.sel.has(+v) ? S.sel.delete(+v) : S.sel.add(+v)),
  minus: (v) => (S.minus.has(v) ? S.minus.delete(v) : S.minus.add(v)),
  verdict: (v) => {
    const [id, x] = v.split('=')
    S.verdicts[id] = S.verdicts[id] === x ? null : x
  },
  model: () => (S.model = (S.model + 1) % 3),
  /* Включить или остановить кампанию: остановленная запоминает, какой была. */
  toggle: (v) => {
    const c = M.campaigns.find((x) => x.id === +v)
    c.on = !c.on
    if (c.on) c.health = c.was || 'warn'
    else {
      c.was = c.health === 'off' ? 'warn' : c.health
      c.health = 'off'
    }
    S.toast = c.on ? `«${c.name}» запущена` : `«${c.name}» остановлена. Вернуть можно в «Истории»`
  },
}

/* ---------- запуск ---------- */

function parseState(str) {
  const out = {}
  for (const p of str.split(',').filter(Boolean)) {
    if (p === 'expert' || p === 'simple') out.expert = p === 'expert'
    else if (p === 'light' || p === 'dark') out.theme = p
    else if (M.projects.some((x) => x.id === p)) out.project = p
    else if (p.startsWith('tab-')) out.tabOf = p.slice(4)
    else if (p.startsWith('chat-')) out.chat = p.slice(5)
    else if (p.startsWith('camp-')) out.camp = +p.slice(5)
    else if (p.startsWith('stage-')) out.stage = +p.slice(6)
    else if (p.startsWith('modal-')) out.modal = p.slice(6)
    else if (TITLES[p]) out.screen = p
  }
  return out
}

/** Состояние из адреса или из ссылки «Показать». */
function applyState(str) {
  const { tabOf, ...h } = parseState(str)
  Object.assign(S, { project: null }, h)
  if (tabOf) S.sub[S.screen] = tabOf
  if (h.stage != null) S.stageDone = Math.max(S.stageDone, h.stage)
  if (h.theme) S.themeSet = true
}

/** Сумма не рвётся на переносе строки: пробелы внутри числа и перед знаком рубля становятся неразрывными. Только в тексте — атрибуты и пути графиков не трогаем. */
const glue = (html) => html.replace(/>[^<]+</g, (t) => t.replace(/(\d) (?=\d{3}(?!\d)|₽)/g, '$1\u00a0'))

function boot() {
  const app = document.getElementById('app')
  const bar = document.getElementById('bar')
  let lastKey = ''

  const load = () => applyState(decodeURIComponent(location.hash.slice(1)))

  function paint() {
    const key = [S.screen, S.sub[S.screen], S.chat, S.camp, S.project].join('/')
    const keep = {}
    app.querySelectorAll('[data-scroll]').forEach((el) => (keep[el.dataset.scroll] = el.scrollTop))
    if (key !== lastKey) delete keep.main
    lastKey = key
    app.innerHTML = `<div class="v v2" data-theme="${S.theme}" data-mode="${S.expert ? 'expert' : 'simple'}" data-screen="${S.screen}">${glue(render())}</div>`
    /* Карта функций помнит, где её оставили, когда ушли смотреть раздел. */
    if (keep.map != null) S.mapY = keep.map
    else keep.map = S.mapY
    app.querySelectorAll('[data-scroll]').forEach((el) => (el.scrollTop = keep[el.dataset.scroll] || 0))
    const inApp = S.screen !== 'map' && S.screen !== 'start'
    bar.innerHTML = `<strong>Макет «Диалог»</strong>
      <nav><button class="${inApp ? 'active' : ''}" data-act="go:${inApp ? S.screen : 'home'}">Кабинет</button><button class="${S.screen === 'map' ? 'active' : ''}" data-act="go:map">Карта функций</button><button class="${S.screen === 'start' ? 'active' : ''}" data-act="go:start">Первый вход</button></nav>
      <nav><em>Режим:</em><button class="${S.expert ? '' : 'active'}" data-act="expert:off">Обычный</button><button class="${S.expert ? 'active' : ''}" data-act="expert:on">Экспертный</button></nav>
      <span>Данные вымышленные · всё кликабельно · <a href="index.html">три первых варианта</a></span>`
    document.title = `${TITLES[S.screen] || 'Главная'} — макет AiMarketing «Диалог»`
    const tab = S.sub[S.screen]
    try {
      history.replaceState(null, '', `#${[S.screen, tab && `tab-${tab}`, S.screen === 'chat' && `chat-${S.chat}`, S.screen === 'camp' && `camp-${S.camp}`, S.expert && 'expert', S.themeSet && S.theme, S.project].filter(Boolean).join(',')}`)
    } catch (e) {
      /* в предпросмотре без адреса состояние просто не запоминается */
    }
  }

  document.addEventListener('click', (e) => {
    const el = e.target.closest('[data-act]')
    if (!el) {
      if (S.menu) {
        S.menu = null
        paint()
      }
      return
    }
    const i = el.dataset.act.indexOf(':')
    const name = i < 0 ? el.dataset.act : el.dataset.act.slice(0, i)
    const arg = i < 0 ? '' : el.dataset.act.slice(i + 1)
    /* Клик внутри окна мимо кнопок ничего не делает; клик по затемнению закрывает окно. */
    if (name === 'noop' || (el.classList.contains('scrim') && e.target !== el)) return
    app.querySelectorAll('[data-k]').forEach((f) => (S.form[f.dataset.k] = f.value))
    if (name !== 'menu') S.menu = null
    S.toast = null
    if ('close' in el.dataset) S.modal = null
    const flagsBefore = { ...S.flags }
    S.revert = null
    if (ACT[name]) ACT[name](arg, el)
    noteHistory(name, arg, flagsBefore)
    if (el.dataset.then) ACT.go(el.dataset.then)
    paint()
  })
  /* Поле поиска фильтрует список сразу: перерисовываем и возвращаем курсор в поле. */
  document.addEventListener('input', (e) => {
    const f = e.target
    if (!f.dataset || !('live' in f.dataset)) return
    S.form[f.dataset.k] = f.value
    paint()
    const again = app.querySelector(`[data-k="${f.dataset.k}"]`)
    if (again) {
      again.focus()
      again.setSelectionRange(again.value.length, again.value.length)
    }
  })
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && (S.modal || S.menu || S.drawer)) Object.assign(S, { modal: null, menu: null, drawer: false })
    else if (!onKey(e)) return
    paint()
  })
  window.addEventListener('hashchange', () => {
    load()
    paint()
  })

  load()
  paint()
}
