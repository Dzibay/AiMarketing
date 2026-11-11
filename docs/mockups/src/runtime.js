/* ---------- состояние и запуск макета ---------- */

const S = {
  variant: 'gallery', screen: 'home', project: null, mode: 'simple', theme: 'light', themeSet: false,
  decided: {}, sel: new Set(), minus: new Set(), verdicts: {},
  panel: true, tab: 'changes', dock: true, docktab: 'chat', menu: null, model: 1, qfilter: 'all', cursor: 0,
}
/** Варианты интерфейса: каждый файл vN.js возвращает { name, theme, render, actions?, keys?, after? }. */
const VARIANTS = {}
const SCREEN_NAMES = { home: 'Сводка', chat: 'ИИ-помощник', camps: 'Кампании', queries: 'Чистка запросов' }

const plural = (n, a, b, c) => (n % 10 === 1 && n % 100 !== 11 ? a : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? b : c)
const pro = () => S.mode === 'pro'
/** Кампании и дела под выбранный проект. */
const visibleCampaigns = () => M.campaigns.filter((c) => !S.project || c.project === S.project)
const visibleTasks = () => M.tasks.filter((t) => !S.project || t.project === S.project || !t.project)
const openTasks = () => visibleTasks().filter((t) => !S.decided[t.id])
const pendingCount = () => M.tasks.filter((t) => t.group === 'ai' && !S.decided[t.id]).length
/** Итоги под выбранный проект (без проекта — по всем). */
const totals = () => {
  const p = S.project && project(S.project)
  return p ? { ...M.totals, today: p.today, yesterday: p.yesterday, week: p.week, conv: p.conv, cpa: Math.round(p.week / p.conv) } : M.totals
}
/** Решение по запросу: выбор пользователя важнее разметки ИИ. */
const verdictOf = (q) => S.verdicts[q.id] || q.v
/** Сколько запросов уйдёт в минус-фразы с учётом решений пользователя. */
const minusCount = () =>
  M.queryTotals.no + M.queries.filter((q) => q.v !== 'no' && S.verdicts[q.id] === 'no').length - M.queries.filter((q) => q.v === 'no' && S.verdicts[q.id] === 'yes').length

/** Статус всегда с иконкой и словом, не только цветом. */
const ST_ICON = { ok: 'check', warn: 'alert', bad: 'alert', off: 'pause' }
const badge = (st, text) => `<span class="st st--${st}">${ic(ST_ICON[st], 13)}${text || STATUS[st]}</span>`

const frame = (v, attrs = '') => `<div class="v ${v}" data-theme="${S.theme}" data-mode="${S.mode}" data-screen="${S.screen}"${attrs}>${VARIANTS[v].render()}</div>`

/** Вариант, нарисованный с временным состоянием — для миниатюр на странице сравнения. */
function snapshot(v, over = {}) {
  const keep = { ...S }
  Object.assign(S, { menu: null, project: null, theme: VARIANTS[v].theme || 'light' }, over)
  const html = frame(v)
  Object.assign(S, keep)
  return html
}

function readHash() {
  const out = {}
  for (const p of location.hash.slice(1).split(',').filter(Boolean)) {
    if (VARIANTS[p]) out.variant = p
    else if (['simple', 'pro'].includes(p)) out.mode = p
    else if (['light', 'dark'].includes(p)) out.theme = p
    else if (M.projects.some((x) => x.id === p)) out.project = p
    else out.screen = p
  }
  return out
}

/** Запускает макет: после каждого действия экран перерисовывается целиком, состояние хранится в адресе. */
function boot() {
  const app = document.getElementById('app')
  const bar = document.getElementById('bar')
  let fresh = true

  function load() {
    const h = readHash()
    Object.assign(S, { project: null }, h)
    if (h.theme) S.themeSet = true
    else if (!S.themeSet) S.theme = VARIANTS[S.variant].theme || 'light'
  }

  function paint() {
    const keep = {}
    if (!fresh) app.querySelectorAll('[data-scroll]').forEach((el) => (keep[el.dataset.scroll] = el.scrollTop))
    fresh = false
    const V = VARIANTS[S.variant]
    app.innerHTML = frame(S.variant, ' data-scroll="page"')
    app.querySelectorAll('[data-scroll]').forEach((el) => (el.scrollTop = keep[el.dataset.scroll] || 0))
    bar.innerHTML = `<strong>Макеты кабинета</strong><nav>${[['gallery', 'Сравнение'], ['v1', '1 · Сегодня'], ['v2', '2 · Диалог'], ['v3', '3 · Пульт']]
      .map(([k, label]) => `<button class="${S.variant === k ? 'active' : ''}" data-act="variant:${k}">${label}</button>`)
      .join('')}</nav><span>Данные вымышленные и одинаковые во всех вариантах · всё кликабельно</span>`
    document.title = `${V.name} — макеты AiMarketing`
    try {
      history.replaceState(null, '', `#${[S.variant, S.screen, S.mode, S.themeSet && S.theme, S.project].filter(Boolean).join(',')}`)
    } catch (e) {
      /* в предпросмотре без адреса состояние просто не запоминается */
    }
    if (V.after) V.after(app)
  }

  const ACT = {
    variant: (v) => {
      S.variant = v
      if (!S.themeSet) S.theme = VARIANTS[v].theme || 'light'
      if (!SCREEN_NAMES[S.screen]) S.screen = 'home'
      fresh = true
    },
    go: (v) => {
      S.screen = v
      S.sel.clear()
      fresh = true
    },
    mode: (v) => (S.mode = v),
    theme: () => {
      S.theme = S.theme === 'dark' ? 'light' : 'dark'
      S.themeSet = true
    },
    project: (v) => (S.project = v || null),
    menu: (v) => (S.menu = S.menu === v ? null : v),
    panel: () => (S.panel = !S.panel),
    tab: (v) => {
      S.tab = v
      S.panel = true
    },
    dock: () => (S.dock = !S.dock),
    docktab: (v) => {
      S.docktab = v
      S.dock = true
    },
    done: (v) => (S.decided[v] = 'done'),
    reject: (v) => (S.decided[v] = 'rejected'),
    later: (v) => (S.decided[v] = 'later'),
    undo: (v) => delete S.decided[v],
    sel: (v) => (S.sel.has(+v) ? S.sel.delete(+v) : S.sel.add(+v)),
    selnone: () => S.sel.clear(),
    minus: (v) => (S.minus.has(v) ? S.minus.delete(v) : S.minus.add(v)),
    verdict: (v) => {
      const [id, val] = v.split('=')
      S.verdicts[id] = S.verdicts[id] === val ? null : val
    },
    model: (v) => (S.model = v === 'next' ? (S.model + 1) % 3 : +v),
    qfilter: (v) => {
      S.qfilter = v
      S.cursor = 0
    },
    /* Включить или остановить кампанию: остановленная запоминает, какой была. */
    toggle: (v) => {
      const c = M.campaigns.find((x) => x.id === +v)
      c.on = !c.on
      if (c.on) c.health = c.was || 'warn'
      else {
        c.was = c.health === 'off' ? 'warn' : c.health
        c.health = 'off'
      }
    },
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
    if (name !== 'menu') S.menu = null
    const fn = (VARIANTS[S.variant].actions || {})[name] || ACT[name]
    if (fn) fn(arg, el)
    if (el.dataset.then) ACT.go(el.dataset.then)
    paint()
  })
  document.addEventListener('keydown', (e) => {
    const V = VARIANTS[S.variant]
    const t = e.target
    if (!V.keys || e.ctrlKey || e.metaKey || e.altKey || (t && t.closest && t.closest('input, textarea'))) return
    if (V.keys(e)) {
      e.preventDefault()
      paint()
    }
  })
  window.addEventListener('hashchange', () => {
    load()
    fresh = true
    paint()
  })
  window.addEventListener('resize', () => {
    const V = VARIANTS[S.variant]
    if (V.after) V.after(app)
  })

  load()
  paint()
}
