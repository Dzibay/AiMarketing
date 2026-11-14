// Скриншоты и аудит страниц кабинета на демо-стенде (headless Chrome через CDP, без зависимостей).
//
//   node scripts/demo/shots.mjs --out <папка> [--modes simple,expert] [--themes light,dark] [--widths 1440,375]
//                               [--pages home,ws-analytics] [--audit] [--stable]
//   node scripts/demo/shots.mjs --compare <папка-до> <папка-после>
//
// Вход — токен демо-пользователя из DEMO_DIR/token.txt, номера проектов — из DEMO_DIR/stand.json (их пишет seed.py).
// --audit   на каждой странице: горизонтальная прокрутка, элементы без подписи, контраст текста ниже WCAG AA,
//           элементы без видимого фокуса при обходе Tab. Итог — <out>/audit-<темы>-<режимы>.json и сводка в консоли.
// --stable  для сравнения «до/после»: часы страницы стоят, анимации и переходы выключены.
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const args = process.argv.slice(2)
const opt = (name, def) => {
  const i = args.indexOf(`--${name}`)
  return i >= 0 ? args[i + 1] : def
}
const flag = (name) => args.includes(`--${name}`)

if (flag('compare')) {
  const i = args.indexOf('--compare')
  compare(args[i + 1], args[i + 2])
  process.exit(0)
}

const DEMO_DIR = process.env.DEMO_DIR || (process.platform === 'win32' ? 'C:/Users/Public/tolk_demo' : '/tmp/tolk_demo')
const BASE = process.env.DEMO_URL || 'http://localhost:5173'
const CHROME =
  process.env.CHROME ||
  (process.platform === 'win32' ? 'C:/Program Files/Google/Chrome/Application/chrome.exe' : 'google-chrome')
const outDir = opt('out', join(DEMO_DIR, 'shots'))
const modes = opt('modes', 'simple,expert').split(',')
const themes = opt('themes', 'light,dark').split(',')
const widths = opt('widths', '1440,375').split(',').map(Number)
const only = opt('pages', '') ? opt('pages', '').split(',') : null
const audit = flag('audit')
const stable = flag('stable')
const WAIT = Number(process.env.WAIT || 1800)

const token = readFileSync(join(DEMO_DIR, 'token.txt'), 'utf8').trim()
const stand = JSON.parse(readFileSync(join(DEMO_DIR, 'stand.json'), 'utf8'))
const ws = stand.workspaces.flowers
const PAGES = [
  ['home', '/cabinet'],
  ['chat-new', '/cabinet/chat'],
  ['chat', `/cabinet/chat/${stand.chats.general}`],
  ['campaigns', '/cabinet/campaigns'],
  ['queries', '/cabinet/queries'],
  ['negative-lists', '/cabinet/negative-lists'],
  ['analytics', '/cabinet/analytics'],
  ['autopilot', '/cabinet/autopilot'],
  ['history', '/cabinet/history'],
  ['pending', '/cabinet/pending'],
  ['connections', '/cabinet/connections'],
  ['settings', '/cabinet/settings'],
  ['settings-notifications', '/cabinet/settings/notifications'],
  ['settings-interface', '/cabinet/settings/interface'],
  ['plan', '/cabinet/plan'],
  ['welcome', '/cabinet/welcome'],
  ['ws-summary', `/w/${ws}/summary`],
  ['ws-chat', `/w/${ws}/chat/${stand.chats.flowers}`],
  // Чат с этапом «Картинки»: галерея на согласование (на старых стендах этого чата нет).
  ...(stand.chats.images ? [['ws-chat-images', `/w/${ws}/chat/${stand.chats.images}`]] : []),
  ['ws-campaigns', `/w/${ws}/campaigns`],
  ['ws-campaign', `/w/${ws}/campaigns/${stand.campaign_row}`],
  ['ws-queries', `/w/${ws}/queries`],
  ['ws-analytics', `/w/${ws}/analytics`],
  ['ws-slices', `/w/${ws}/slices`],
  ['ws-ad-stats', `/w/${ws}/ad-stats`],
  ['ws-autopilot', `/w/${ws}/autopilot`],
  ['ws-experiments', `/w/${ws}/experiments`],
  ['ws-history', `/w/${ws}/history`],
  ['ws-settings', `/w/${ws}/settings`],
  ['promo-summary', `/w/${stand.workspaces.promo}/summary`],
  ['promo-analytics', `/w/${stand.workspaces.promo}/analytics`],
].filter(([name]) => !only || only.includes(name))

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const port = 9300 + Math.floor(Math.random() * 400)
// Свой профиль на каждый запуск: несколько прогонов (темы, режимы) идут параллельно.
const profile = mkdtempSync(join(tmpdir(), 'tolk-shots-'))
const chrome = spawn(
  CHROME,
  ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--hide-scrollbars', 'about:blank'],
  { stdio: 'ignore' },
)
const chromeExited = new Promise((r) => chrome.once('exit', r))
// Упали посреди прогона — всё равно закрываем Chrome и убираем профиль.
process.on('uncaughtException', async (err) => {
  console.error(err)
  await shutdown(1)
})
let targets = []
for (let i = 0; i < 60 && !targets.length; i++) {
  await sleep(250)
  try {
    targets = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).filter((t) => t.type === 'page')
  } catch {}
}
if (!targets.length) throw new Error('Chrome не запустился: ' + CHROME)
const socket = new WebSocket(targets[0].webSocketDebuggerUrl)
await new Promise((r) => socket.addEventListener('open', r))
let seq = 0
const waiting = new Map()
socket.addEventListener('message', (ev) => {
  const msg = JSON.parse(ev.data)
  if (msg.id && waiting.has(msg.id)) {
    waiting.get(msg.id)(msg)
    waiting.delete(msg.id)
  } else if (msg.method === 'Fetch.requestPaused') {
    pinMode(msg.params)
  }
})
const send = (method, params = {}) =>
  new Promise((r) => {
    const id = ++seq
    waiting.set(id, r)
    socket.send(JSON.stringify({ id, method, params }))
  })
const evaluate = async (expression) => {
  const res = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
  if (res.result?.exceptionDetails) throw new Error(res.result.exceptionDetails.exception?.description || expression)
  return res.result?.result?.value
}

await send('Page.enable')
// Режим кабинета хранится в аккаунте (user.ui.expert), а прогоны с разными режимами идут параллельно под одним
// демо-пользователем. Поэтому на сервер режим не пишем, а подставляем в ответ /api/web/me своему Chrome.
let shotMode = 'simple'
await send('Fetch.enable', { patterns: [{ urlPattern: '*/api/web/me', requestStage: 'Response' }] })
async function pinMode({ requestId, responseStatusCode, responseHeaders = [] }) {
  try {
    if (responseStatusCode !== 200) throw new Error('не профиль')
    const got = (await send('Fetch.getResponseBody', { requestId })).result
    const data = JSON.parse(got.base64Encoded ? Buffer.from(got.body, 'base64').toString('utf8') : got.body)
    data.user.ui = { ...data.user.ui, expert: shotMode === 'expert' }
    await send('Fetch.fulfillRequest', {
      requestId,
      responseCode: 200,
      // Тело стало другим: длину и сжатие Chrome посчитает сам.
      responseHeaders: responseHeaders.filter((h) => !/^content-(length|encoding)$/i.test(h.name)),
      body: Buffer.from(JSON.stringify(data)).toString('base64'),
    })
  } catch {
    await send('Fetch.continueRequest', { requestId })
  }
}
if (stable) {
  // Часы стоят: «20 минут назад» и «сегодня» одинаковы в прогонах «до» и «после».
  const now = Number(opt('now', Date.now()))
  await send('Page.addScriptToEvaluateOnNewDocument', {
    source: `(() => { const T = ${now}; const D = Date; class F extends D { constructor(...a) { super(...(a.length ? a : [T])) } static now() { return T } }
      globalThis.Date = F })()`,
  })
}
if (stable || audit) {
  // Без переходов снимки одинаковы, а стиль фокуса виден сразу после Tab, а не через 150 мс.
  await send('Page.addScriptToEvaluateOnNewDocument', {
    source: `addEventListener('DOMContentLoaded', () => { const s = document.createElement('style');
      s.textContent = 'html { scroll-behavior: auto !important } *, *::before, *::after { transition: none !important; animation: none !important; caret-color: transparent !important }';
      document.head.append(s) })`,
  })
}

const report = {}
for (const width of widths) {
  const height = width < 700 ? 812 : 900
  await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width < 700 })
  for (const theme of themes) {
    for (const mode of modes) {
      const dir = join(outDir, `${theme}-${mode}-${width}`)
      mkdirSync(dir, { recursive: true })
      shotMode = mode
      await send('Page.navigate', { url: BASE + '/login' })
      await waitLoaded()
      await evaluate(`localStorage.setItem('aimarketing-session', ${JSON.stringify(token)});
        localStorage.setItem('aimarketing-expert', '${mode === 'expert' ? 1 : 0}');
        localStorage.setItem('aimarketing-theme', '${theme}');
        localStorage.setItem('aimarketing-cookies', '1'); 1`)
      for (const [name, path] of PAGES) {
        await send('Page.navigate', { url: BASE + path })
        await waitLoaded()
        await sleep(WAIT)
        // Vite мог закэшировать пустой css, если файл читали посреди перезаписи: такие снимки бессмысленны.
        if (!(await evaluate(`!!getComputedStyle(document.documentElement).getPropertyValue('--ink').trim()`))) {
          console.error(`${name}: стили не загрузились (нет токена --ink) — прогон остановлен`)
          await shutdown(2)
        }
        const shot = await send('Page.captureScreenshot', { format: 'png' })
        writeFileSync(join(dir, `${name}.png`), Buffer.from(shot.result.data, 'base64'))
        let line = ''
        if (audit) {
          const res = await auditPage()
          // Пороги размеров из плана: на 1920 px страница не уже 1500 px, таблица кампаний от 1280 px — без
          // горизонтальной прокрутки. На других страницах прокрутка таблиц только записывается.
          res.wide = width === 1920 && res.dpage !== null && res.dpage < 1500 ? [`.dpage ${res.dpage}px < 1500`] : []
          res.campaignScroll = width >= 1280 && /(^|-)campaigns$/.test(name) ? res.tableScroll : []
          report[`${theme}-${mode}-${width}/${name}`] = res
          line = ` overflow ${res.overflow.length}, без подписи ${res.unlabeled.length}, контраст ${res.contrast.length}, фокус ${res.focus.length}`
          line += `, прокрутка ${res.nestedScroll.length}, системные ${res.native.length}, title ${res.titles.length}`
          line += `, курсор ${res.cursor.length}, лайм ${res.limeDisabled.length}`
          if (res.wide.length) line += `, ${res.wide[0]}`
          if (res.campaignScroll.length) line += `, таблица кампаний листается вбок`
        }
        console.log(`${theme}-${mode}-${width} ${name}${line}`)
      }
    }
  }
}
if (audit) {
  writeFileSync(join(outDir, `audit-${themes.join('+')}-${modes.join('+')}.json`), JSON.stringify(report, null, 1))
  summarize(report)
}
await shutdown(0)

// Сводка прогона: по каждой проверке — что нашлось и на скольких снимках (страница × ширина × тема × режим).
function summarize(rep) {
  const checks = {
    overflow: 'горизонтальная прокрутка страницы', unlabeled: 'без подписи', contrast: 'контраст ниже AA',
    focus: 'фокус не виден',
    nestedScroll: 'вложенная вертикальная прокрутка', native: 'системные поля', titles: 'атрибут title',
    cursor: 'кликабельное без руки', limeDisabled: 'лайм у неактивной кнопки', wide: 'узкая страница на 1920',
    campaignScroll: 'таблица кампаний листается вбок',
  }
  const all = Object.entries(rep)
  console.log(`\nИтог: ${all.length} снимков`)
  for (const [key, title] of Object.entries(checks)) {
    const found = new Map()
    for (const [shot, res] of all) {
      // Числа в пикселях у разных ширин разные — для группировки их убираем.
      for (const item of res[key] || []) {
        const text = typeof item === 'object' ? `${item.el} «${item.text}» ${item.color} ${item.ratio}` : String(item)
        const k = text.replace(/\s*[\d.]+(\/[\d.]+)?px/g, '').trim()
        found.set(k, [...(found.get(k) || []), shot])
      }
    }
    console.log(`${title}: ${found.size ? '' : 'нет'}`)
    for (const [item, shots] of [...found].sort((a, b) => b[1].length - a[1].length).slice(0, 12)) {
      const pages = [...new Set(shots.map((s) => s.split('/')[1]))]
      console.log(`  ${item} — ${shots.length} сн.: ${pages.slice(0, 6).join(', ')}${pages.length > 6 ? '…' : ''}`)
    }
  }
}

// Chrome закрываем штатно и ждём выхода: пока он (и его дочерние процессы) держит файлы профиля, папку не удалить.
async function shutdown(code) {
  try {
    await Promise.race([send('Browser.close'), sleep(2000)])
    socket.close()
  } catch {} // соединения с Chrome ещё нет
  await Promise.race([chromeExited, sleep(5000)])
  chrome.kill()
  try {
    rmSync(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 300 })
  } catch {}
  process.exit(code)
}

async function waitLoaded() {
  await sleep(300)
  for (let i = 0; i < 80; i++) {
    const ok = await evaluate('document.readyState === "complete" && !!document.querySelector("#app > *")').catch(() => false)
    if (ok) return
    await sleep(250)
  }
}

async function auditPage() {
  const res = await evaluate(`(${inspect.toString()})()`)
  // Видимый фокус: обходим страницу клавишей Tab и сравниваем стили элемента (и трёх предков: рамка поля
  // бывает на обёртке через :focus-within) до и после.
  await evaluate(`(${snapshotStyles.toString()})()`)
  const focus = []
  const seen = new Set()
  for (let i = 0; i < 45; i++) {
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 })
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 })
    const step = await evaluate(`(${checkFocus.toString()})()`)
    if (!step || seen.has(step.id)) break
    seen.add(step.id)
    if (!step.visible) focus.push(step.label)
  }
  return { ...res, focus }
}

// --- выполняется в странице ---

function snapshotStyles() {
  const keys = ['outline-style', 'outline-width', 'outline-color', 'box-shadow', 'border-color', 'background-color',
    'color', 'text-decoration-line']
  const style = (el) => keys.map((k) => getComputedStyle(el).getPropertyValue(k)).join('|')
  const chainOf = (el) => [el, el.parentElement, el.parentElement?.parentElement, el.parentElement?.parentElement?.parentElement].filter(Boolean)
  window.__auditStyle = style
  window.__auditChain = chainOf
  window.__auditBase = new Map()
  window.__auditIds = new Map()
  const focusable = 'a[href], button, input, select, textarea, summary, [tabindex]:not([tabindex="-1"]), [contenteditable="true"]'
  for (const el of document.querySelectorAll(focusable)) {
    for (const n of chainOf(el)) {
      if (n && !window.__auditBase.has(n)) window.__auditBase.set(n, style(n))
    }
  }
  document.activeElement?.blur?.()
  return 1
}

function checkFocus() {
  const el = document.activeElement
  if (!el || el === document.body || el === document.documentElement) return null
  if (!window.__auditIds.has(el)) window.__auditIds.set(el, window.__auditIds.size + 1)
  const chain = window.__auditChain(el)
  // Фокус за краем экрана (закрытая шторка меню, спрятанная панель) не виден, как бы ни менялся стиль.
  el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' }) // у html плавная прокрутка
  const r = el.getBoundingClientRect()
  const onScreen = r.right > 0 && r.left < innerWidth && r.bottom > 0 && r.top < innerHeight
  const visible = onScreen && (chain.some((n) => window.__auditBase.has(n) && window.__auditBase.get(n) !== window.__auditStyle(n))
    || !window.__auditBase.has(el))
  const text = (el.getAttribute('aria-label') || el.textContent || el.getAttribute('placeholder') || '').trim()
  const cls = typeof el.className === 'string' ? el.className.trim().split(/\s+/).slice(0, 2).join('.') : ''
  return { id: window.__auditIds.get(el), visible, label: `${el.tagName.toLowerCase()}${cls ? '.' + cls : ''} «${text.slice(0, 40)}»` }
}

function inspect() {
  const W = innerWidth
  const cache = new Map()
  const cs = (el) => {
    let s = cache.get(el)
    if (!s) cache.set(el, (s = getComputedStyle(el)))
    return s
  }
  const label = (el) => {
    const cls = typeof el.className === 'string' ? el.className.trim().split(/\s+/).slice(0, 2).join('.') : ''
    return `${el.tagName.toLowerCase()}${cls ? '.' + cls : ''}`
  }
  const shown = (el) => {
    const r = el.getBoundingClientRect()
    if (r.width < 2 || r.height < 2) return false
    const s = cs(el)
    return s.visibility !== 'hidden' && s.display !== 'none' && !el.closest('[hidden], [inert]')
  }
  // 1. Горизонтальная прокрутка страницы: кто вылез за правый край (кроме содержимого прокручиваемых блоков).
  const overflow = []
  if (document.documentElement.scrollWidth > W + 1) {
    for (const el of document.querySelectorAll('body *')) {
      const r = el.getBoundingClientRect()
      if (r.right <= W + 1 || !shown(el)) continue
      let p = el.parentElement
      let clipped = false
      while (p && p !== document.body) {
        const ox = cs(p).overflowX
        if (ox !== 'visible') { clipped = true; break }
        p = p.parentElement
      }
      if (!clipped && !overflow.some((o) => el.parentElement && o.el.contains(el))) overflow.push({ el, right: Math.round(r.right) })
    }
  }
  // 2. Кнопки, ссылки и поля без доступного имени.
  const nameOf = (el) => {
    const by = el.getAttribute('aria-labelledby')
    if (by) return by.split(/\s+/).map((id) => document.getElementById(id)?.textContent || '').join(' ').trim()
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label').trim()
    if (el.labels?.length) return [...el.labels].map((l) => l.textContent).join(' ').trim()
    const alt = [...el.querySelectorAll('img[alt]')].map((i) => i.alt).join(' ')
    return ((['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName) ? '' : el.textContent) + alt).trim() || el.title?.trim() || ''
  }
  const unlabeled = []
  for (const el of document.querySelectorAll('button, a[href], [role="button"], [role="switch"], [role="tab"], input:not([type="hidden"]), select, textarea')) {
    if (!shown(el) || el.closest('[aria-hidden="true"]')) continue
    if (!nameOf(el)) unlabeled.push(`${label(el)}${el.getAttribute('placeholder') ? ' (только placeholder)' : ''}`)
  }
  // 3. Контраст текста: цвет против итогового фона (с прозрачностью), WCAG AA 4.5 / 3 для крупного.
  const parse = (c) => {
    const m = c.match(/rgba?\(([^)]+)\)/)
    if (!m) return null
    const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number)
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }
  }
  const over = (top, bottom) => ({
    r: top.r * top.a + bottom.r * (1 - top.a), g: top.g * top.a + bottom.g * (1 - top.a),
    b: top.b * top.a + bottom.b * (1 - top.a), a: 1,
  })
  const lum = ({ r, g, b }) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
  }
  const backgroundOf = (el) => {
    const layers = []
    for (let n = el; n; n = n.parentElement) {
      const s = cs(n)
      if (s.backgroundImage && s.backgroundImage !== 'none') return null
      const c = parse(s.backgroundColor)
      if (c && c.a > 0) { layers.push(c); if (c.a >= 1) break }
    }
    let bg = { r: 255, g: 255, b: 255, a: 1 }
    for (let i = layers.length - 1; i >= 0; i--) bg = over(layers[i], bg)
    return bg
  }
  const opacityOf = (el) => { let o = 1; for (let n = el; n; n = n.parentElement) o *= Number(cs(n).opacity); return o }
  const contrast = new Map()
  for (const el of document.querySelectorAll('body *')) {
    if (!shown(el) || el.closest('[aria-hidden="true"], :disabled, [aria-disabled="true"], svg')) continue
    const text = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join('').trim()
    if (!text) continue
    const s = cs(el)
    if (s.clip === 'rect(0px, 0px, 0px, 0px)' || s.clipPath === 'inset(50%)') continue
    const bg = backgroundOf(el)
    const fg0 = parse(s.color)
    if (!bg || !fg0) continue
    const fg = over({ ...fg0, a: fg0.a * opacityOf(el) }, bg)
    const [l1, l2] = [lum(fg), lum(bg)].sort((a, b) => b - a)
    const ratio = (l1 + 0.05) / (l2 + 0.05)
    const size = parseFloat(s.fontSize)
    const large = size >= 24 || (size >= 18.66 && Number(s.fontWeight) >= 700)
    if (ratio >= (large ? 3 : 4.5)) continue
    const key = `${label(el)} ${s.color} on ${bg.r | 0},${bg.g | 0},${bg.b | 0}`
    const item = contrast.get(key) || { el: label(el), ratio: Math.round(ratio * 100) / 100, size, color: s.color, text: text.slice(0, 40), n: 0 }
    item.n++
    contrast.set(key, item)
  }
  const page = document.querySelector('.dpage')
  const inPage = page ? [...page.querySelectorAll('*')].filter(shown) : []
  // 4. Вложенная вертикальная прокрутка: страница листается целиком. Исключения — чат (лента и доска рядом),
  //    попапы и окна, поля ввода.
  const OWN_SCROLL = '.chat, .ui-pop, .modal, [role="dialog"], [role="listbox"], [role="menu"], textarea'
  const nestedScroll = inPage
    .filter((el) => !el.closest(OWN_SCROLL) && /auto|scroll/.test(cs(el).overflowY) && el.scrollHeight > el.clientHeight + 1)
    .map((el) => `${label(el)} ${el.clientHeight}/${el.scrollHeight}px`)
  // 5. Системные поля вместо своих: выпадающий список, дата, флажок (флажок — только внутри UiCheckbox / UiSwitch).
  const native = [...document.querySelectorAll('select, input[type="date"], input[type="datetime-local"], input[type="checkbox"]')]
    .filter((el) => !el.closest('.ui-check, .ui-switch'))
    .map((el) => `${label(el)}[${el.type}]${shown(el) ? '' : ' (скрыт)'}`)
  // 6. Атрибут title — системная подсказка вместо v-tip. <title> внутри SVG не атрибут, его не считаем.
  const titles = [...document.querySelectorAll('body [title]')]
    .filter((el) => !el.closest('svg'))
    .map((el) => `${label(el)} «${el.getAttribute('title').slice(0, 30)}»`)
  // 7. Кликабельное без курсора-руки.
  const cursor = [...document.querySelectorAll('a[href], button:not(:disabled), .is-link')]
    .filter((el) => shown(el) && !el.closest('[inert]') && cs(el).cursor !== 'pointer')
    .map((el) => `${label(el)} (${cs(el).cursor})`)
  // 8. Лайм в фоне неактивной кнопки: оттенок 55–110°, насыщенный, не серый (#c6f04a — 75°, --lime-soft тоже).
  const isLime = ({ r, g, b }) => {
    const max = Math.max(r, g, b)
    const min = Math.min(r, g, b)
    if (max - min < 20 || max !== g) return false
    const l = (max + min) / 510
    const s = (max - min) / 255 / (1 - Math.abs(2 * l - 1))
    const hue = 60 * ((b - r) / (max - min)) + 120
    return s > 0.4 && hue >= 55 && hue <= 110
  }
  const limeDisabled = [...document.querySelectorAll('button:disabled, .btn[aria-disabled="true"]')]
    .filter((el) => {
      if (!shown(el)) return false
      const s = cs(el)
      const colors = [s.backgroundColor, ...(s.backgroundImage.match(/rgba?\([^)]+\)/g) || [])]
      return colors.map(parse).some((c) => c && c.a > 0.2 && isLime(c))
    })
    .map((el) => `${label(el)} «${el.textContent.trim().slice(0, 30)}»`)
  // 9. Размеры: ширина страницы и таблицы, которые листаются вбок (пороги — в основном цикле по ширине окна).
  const tableScroll = inPage
    .filter((el) => el.querySelector('table') && /auto|scroll/.test(cs(el).overflowX) && el.scrollWidth > el.clientWidth + 1)
    .map((el) => `${label(el)} ${el.clientWidth}/${el.scrollWidth}px`)
  return {
    overflow: overflow.slice(0, 8).map((o) => `${label(o.el)} → ${o.right}px`),
    unlabeled: [...new Set(unlabeled)],
    contrast: [...contrast.values()],
    nestedScroll,
    native,
    titles: [...new Set(titles)],
    cursor: [...new Set(cursor)],
    limeDisabled,
    dpage: page ? Math.round(page.getBoundingClientRect().width) : null,
    tableScroll,
  }
}

// --- сравнение «до/после» ---

function compare(a, b) {
  const files = (dir) => readdirSync(dir).flatMap((d) => {
    const p = join(dir, d)
    return statSync(p).isDirectory() ? readdirSync(p).filter((f) => f.endsWith('.png')).map((f) => `${d}/${f}`) : []
  })
  const before = new Set(files(a))
  let same = 0
  const diff = []
  for (const f of files(b)) {
    if (!before.has(f)) { diff.push(`+ ${f}`); continue }
    if (readFileSync(join(a, f)).equals(readFileSync(join(b, f)))) same++
    else diff.push(`≠ ${f}`)
  }
  for (const f of before) if (!existsSync(join(b, f))) diff.push(`- ${f}`)
  console.log(`одинаковых ${same}, различий ${diff.length}`)
  for (const line of diff) console.log(line)
}
