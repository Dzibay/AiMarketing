/* Общие данные и помощники для макетов интерфейса. Все цифры вымышленные. */

const M = {
  date: 'Вторник, 10 ноября',
  plan: { name: 'Про', until: '12 декабря', used: 62 },
  totals: { today: 15170, yesterday: 15370, week: 103120, weekDelta: 6, conv: 196, convDelta: 8, cpa: 526, cpaDelta: -2 },
  spendDays: [13900, 14200, 15100, 14800, 16300, 13200, 12900, 14100, 14600, 15800, 15200, 16100, 13700, 15370],
  convDays: [24, 26, 29, 27, 31, 22, 21, 26, 27, 30, 29, 33, 24, 27],
  cpaDays: [579, 546, 521, 548, 526, 600, 614, 542, 541, 527, 524, 488, 571, 569],

  projects: [
    { id: 'okna', name: 'okna-profi.ru', client: 'Окна Профи', status: 'ok', today: 4820, yesterday: 5100, week: 30670, conv: 77, kpi: 'CPA', value: 402, target: 450, balance: 9400, balanceDays: 2, pace: 96, camps: 4, spark: [4100, 4350, 4600, 4200, 4900, 3900, 4620] },
    { id: 'msk', name: 'msk.okna-profi.ru', client: 'Окна Профи', status: 'bad', today: 1950, yesterday: 2240, week: 13650, conv: 21, kpi: 'CPA', value: 650, target: 450, balance: 18200, balanceDays: 9, pace: 112, camps: 2, spark: [1700, 1850, 2100, 1900, 2200, 1960, 1940] },
    { id: 'dent', name: 'ulybka-dent.ru', client: 'Стоматология Улыбка', status: 'warn', today: 6300, yesterday: 6050, week: 44100, conv: 35, kpi: 'CPA', value: 1260, target: 1000, balance: 88000, balanceDays: 14, pace: 104, camps: 3, spark: [5900, 6100, 6400, 6200, 6800, 6300, 6400] },
    { id: 'kofe', name: 'shop-kofe.ru', client: null, status: 'ok', today: 2100, yesterday: 1980, week: 14700, conv: 63, kpi: 'ДРР', value: 14, target: 18, balance: 44000, balanceDays: 21, pace: 88, camps: 2, spark: [2200, 1900, 2000, 2150, 2300, 2050, 2100] },
  ],

  /* Дела: срочные и обычные находит код по цифрам, предложения ИИ появляются только после вопроса пользователя. */
  tasks: [
    { id: 't1', group: 'urgent', project: 'okna', title: 'Деньги в Директе закончатся через 2 дня', text: 'На балансе 9 400 ₽, в день уходит около 4 800 ₽. Реклама остановится в четверг.', primary: 'Пополнить в Директе', icon: 'wallet', external: true },
    { id: 't2', group: 'urgent', project: 'dent', title: 'Заявка стоит 1 260 ₽ при цели 1 000 ₽ — третий день', text: 'Дороже всего обходится кампания «Имплантация — Поиск».', primary: 'Разобрать с ИИ', secondary: 'Открыть кампании', icon: 'alert', ai: true },
    { id: 't3', group: 'ai', project: 'okna', title: 'Снизить ставки в 3 группах на 15%', text: 'Расход меньше на 1 900 ₽ в неделю, заявок столько же или на одну меньше.', primary: 'Выполнить', secondary: 'Посмотреть', icon: 'sparkles', diff: [['Балконы — холодное остекление', '48 ₽', '41 ₽'], ['Балконы — тёплое остекление', '62 ₽', '53 ₽'], ['Лоджии под ключ', '55 ₽', '47 ₽']] },
    { id: 't4', group: 'ai', project: 'msk', title: 'Исключить 14 площадок РСЯ без заявок', text: 'За месяц они потратили 2 760 ₽ и не дали ни одной заявки.', primary: 'Выполнить', secondary: 'Посмотреть', icon: 'sparkles' },
    { id: 't5', group: 'ai', project: 'kofe', title: 'Добавить 23 минус-фразы', text: 'Запросы про работу, б/у и чужие бренды: 3 240 ₽ за неделю.', primary: 'Выполнить', secondary: 'Посмотреть', icon: 'sparkles' },
    { id: 't6', group: 'today', project: 'msk', title: '«Окна ПВХ — РСЯ» потратила 3 400 ₽ за неделю без заявок', text: 'Кампания показывается на сайтах-партнёрах, кликов много, заявок нет.', primary: 'Остановить кампанию', secondary: 'Разобрать с ИИ', icon: 'pause' },
    { id: 't7', group: 'today', project: 'kofe', title: '2 объявления отклонены модерацией', text: 'Пока они не показываются. Причина: «лучший кофе» — превосходная степень без подтверждения.', primary: 'Исправить объявления', icon: 'x' },
    { id: 't8', group: 'today', project: null, title: '148 новых поисковых запросов, 23 похожи на мусор', text: 'На мусорные запросы за неделю ушло 3 240 ₽.', primary: 'Почистить запросы', icon: 'filter', go: 'queries' },
  ],
  groups: { urgent: 'Срочно', ai: 'Предложения ИИ ждут решения', today: 'Можно сегодня' },

  campaigns: [
    { id: 1, project: 'okna', name: 'Окна ПВХ — Поиск — Москва', type: 'Поиск', on: true, cost: 16400, clicks: 468, conv: 43, cpa: 381, health: 'ok', spark: [5, 7, 6, 8, 6, 5, 6] },
    { id: 2, project: 'okna', name: 'Остекление балконов — Поиск', type: 'Поиск', on: true, cost: 10900, clicks: 298, conv: 22, cpa: 495, health: 'warn', spark: [4, 3, 4, 3, 3, 2, 3] },
    { id: 3, project: 'okna', name: 'Ретаргетинг — калькулятор', type: 'РСЯ', on: true, cost: 2250, clicks: 146, conv: 7, cpa: 321, health: 'ok', spark: [1, 1, 0, 2, 1, 1, 1] },
    { id: 4, project: 'okna', name: 'Бренд — Окна Профи', type: 'Поиск', on: true, cost: 1120, clicks: 96, conv: 5, cpa: 224, health: 'ok', spark: [1, 0, 1, 1, 1, 0, 1] },
    { id: 5, project: 'msk', name: 'Окна ПВХ — РСЯ', type: 'РСЯ', on: true, cost: 3400, clicks: 912, conv: 0, cpa: null, health: 'bad', spark: [0, 0, 0, 0, 0, 0, 0] },
    { id: 6, project: 'msk', name: 'Окна ПВХ — Поиск — область', type: 'Поиск', on: true, cost: 10250, clicks: 301, conv: 21, cpa: 488, health: 'warn', spark: [3, 4, 2, 3, 4, 2, 3] },
    { id: 7, project: 'dent', name: 'Имплантация — Поиск', type: 'Поиск', on: true, cost: 27300, clicks: 322, conv: 17, cpa: 1606, health: 'bad', spark: [3, 2, 3, 2, 2, 3, 2] },
    { id: 8, project: 'dent', name: 'Лечение кариеса — Поиск', type: 'Поиск', on: true, cost: 11900, clicks: 280, conv: 14, cpa: 850, health: 'ok', spark: [2, 2, 1, 3, 2, 2, 2] },
    { id: 9, project: 'dent', name: 'Отбеливание — РСЯ', type: 'РСЯ', on: false, cost: 4900, clicks: 610, conv: 4, cpa: 1225, health: 'off', spark: [1, 1, 1, 1, 0, 0, 0] },
    { id: 10, project: 'kofe', name: 'Кофе в зёрнах — Товарная', type: 'Товары', on: true, cost: 9800, clicks: 540, conv: 44, drr: 12, health: 'ok', spark: [6, 5, 7, 6, 8, 6, 6] },
    { id: 11, project: 'kofe', name: 'Капсулы — Поиск', type: 'Поиск', on: true, cost: 4900, clicks: 233, conv: 19, drr: 19, health: 'warn', spark: [3, 2, 3, 3, 2, 3, 3] },
  ],

  /* Запросы: ИИ разметил их по кнопке «Разметить», причины — его пояснения. */
  reasons: {
    job: { label: 'Ищут работу', count: 5, cost: 640 },
    diy: { label: 'Хотят сделать сами', count: 8, cost: 1310 },
    brand: { label: 'Чужой бренд', count: 6, cost: 870 },
    free: { label: 'Б/у и бесплатно', count: 4, cost: 420 },
  },
  queries: [
    { id: 'q1', q: 'вакансии монтажник окон', v: 'no', r: 'job', clicks: 7, cost: 119, conv: 0 },
    { id: 'q2', q: 'окна профи отзывы сотрудников', v: 'no', r: 'job', clicks: 9, cost: 205, conv: 0 },
    { id: 'q3', q: 'работа замерщик окон москва', v: 'no', r: 'job', clicks: 5, cost: 88, conv: 0 },
    { id: 'q4', q: 'пластиковые окна своими руками', v: 'no', r: 'diy', clicks: 18, cost: 410, conv: 0 },
    { id: 'q5', q: 'как отрегулировать окно на зиму', v: 'no', r: 'diy', clicks: 21, cost: 372, conv: 0 },
    { id: 'q6', q: 'скачать чертёж окна dwg', v: 'no', r: 'diy', clicks: 6, cost: 96, conv: 0 },
    { id: 'q7', q: 'окна рехау официальный сайт', v: 'no', r: 'brand', clicks: 14, cost: 380, conv: 0 },
    { id: 'q8', q: 'века окна личный кабинет', v: 'no', r: 'brand', clicks: 8, cost: 124, conv: 0 },
    { id: 'q9', q: 'бу окна пвх авито', v: 'no', r: 'free', clicks: 11, cost: 160, conv: 0 },
    { id: 'q10', q: 'окна даром самовывоз', v: 'no', r: 'free', clicks: 4, cost: 46, conv: 0 },
    { id: 'q11', q: 'ремонт пластиковых окон', v: 'maybe', why: 'Ремонта нет в услугах, но одна заявка была', clicks: 22, cost: 530, conv: 1 },
    { id: 'q12', q: 'окна в рассрочку', v: 'maybe', why: 'На сайте не нашёл условий рассрочки', clicks: 12, cost: 300, conv: 1 },
    { id: 'q13', q: 'деревянные окна со стеклопакетом', v: 'maybe', why: 'Другой материал — продаёте ли вы такие?', clicks: 9, cost: 240, conv: 0 },
    { id: 'q14', q: 'москитная сетка на окно', v: 'maybe', why: 'Сопутствующий товар, дешёвые клики', clicks: 15, cost: 210, conv: 0 },
    { id: 'q15', q: 'окна пвх оптом', v: 'maybe', why: 'Опт: работаете ли вы с застройщиками?', clicks: 6, cost: 150, conv: 0 },
    { id: 'q16', q: 'остекление коттеджа', v: 'maybe', why: 'Крупный заказ, одна заявка', clicks: 7, cost: 330, conv: 1 },
    { id: 'q17', q: 'пластиковые окна с установкой', v: 'yes', clicks: 41, cost: 1480, conv: 5 },
    { id: 'q18', q: 'окна пвх цена москва', v: 'yes', clicks: 34, cost: 1240, conv: 4 },
    { id: 'q19', q: 'остекление балкона под ключ', v: 'yes', clicks: 27, cost: 990, conv: 3 },
  ],
  queryTotals: { all: 148, no: 23, noCost: 3240, maybe: 6, yes: 119 },

  chats: [
    { id: 'c1', title: 'Почему выросла цена заявки', project: 'okna', when: 'сегодня, 11:42', pending: 1 },
    { id: 'c2', title: 'Площадки РСЯ без заявок', project: 'msk', when: 'вчера', pending: 1 },
    { id: 'c3', title: 'Чистка запросов за неделю', project: 'kofe', when: 'вчера', pending: 1 },
    { id: 'c4', title: 'Новая кампания: входные двери', project: 'okna', when: '6 ноября' },
    { id: 'c5', title: 'Сравни проекты за октябрь', project: null, when: '2 ноября' },
  ],
  chat: {
    question: 'Почему выросла цена заявки на прошлой неделе?',
    lead: 'Заявка подорожала с 362 ₽ до 402 ₽ (+11%). Причин две:',
    points: [
      ['Ставки выросли, заявок не прибавилось.', 'В трёх группах кампании «Остекление балконов — Поиск» расход вырос на 2 900 ₽, а заявка добавилась одна.'],
      ['Выходные были дороже обычного.', 'Конкуренты подняли ставки, клик в субботу и воскресенье стоил на 18% больше.'],
    ],
    good: '«Окна ПВХ — Поиск — Москва» работает стабильно, её не трогаю.',
    steps: ['Статистика кампаний за 14 дней', 'История ставок в 12 группах', 'Цена клика по дням и часам'],
    cpa: [351, 358, 349, 366, 372, 360, 378, 384, 395, 410, 432, 418, 405, 402],
    days: ['27.10', '28.10', '29.10', '30.10', '31.10', '1.11', '2.11', '3.11', '4.11', '5.11', '6.11', '7.11', '8.11', '9.11'],
    target: 450,
  },
  starters: ['Разбери кампании', 'Почисти запросы', 'Собери новую кампанию'],
  models: [['Быстрая', 1], ['Стандартная', 2], ['Думающая', 3]],
}

const STATUS = { ok: 'В цели', warn: 'Внимание', bad: 'Проблема', off: 'Остановлена' }

/* ---------- форматирование ---------- */

const num = (n) => String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ')
const rub = (n) => `${num(n)} ₽`
const delta = (d) => `${d > 0 ? '+' : '−'}${Math.abs(d)}%`
const project = (id) => M.projects.find((p) => p.id === id)
const projectName = (id) => (id ? project(id).name : 'Все проекты')
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')

/** «Заявка 402 ₽ при цели 450 ₽» или «ДРР 14% при цели 18%». */
function kpiText(p) {
  return p.kpi === 'CPA' ? `Заявка ${rub(p.value)} при цели ${rub(p.target)}` : `ДРР ${p.value}% при цели ${p.target}%`
}
/** Короткий вывод простыми словами. */
function kpiVerdict(p) {
  const d = Math.round((p.value / p.target - 1) * 100)
  if (d <= 0) return p.kpi === 'CPA' ? `Заявки на ${-d}% дешевле цели` : 'Расходы в пределах цели'
  return p.kpi === 'CPA' ? `Заявки на ${d}% дороже цели` : `Расходы на ${d}% выше цели`
}
/** Что с кампанией — одной фразой, без терминов. */
function campHealth(c) {
  const p = project(c.project)
  if (!c.on) return 'Остановлена 3 ноября'
  if (c.drr != null) return c.drr <= p.target ? `ДРР ${c.drr}% — в цели` : `ДРР ${c.drr}% — чуть выше цели ${p.target}%`
  if (!c.conv) return `${rub(c.cost)} за неделю, заявок нет`
  const d = Math.round((c.cpa / p.target - 1) * 100)
  if (d <= 0) return `Заявка ${rub(c.cpa)} — в цели`
  return d > 30 ? `Заявка ${rub(c.cpa)} — на ${d}% дороже цели` : `Заявка ${rub(c.cpa)} — чуть дороже цели ${rub(p.target)}`
}
const campKpi = (c) => (c.drr != null ? `${c.drr}%` : c.cpa ? rub(c.cpa) : '—')

/* ---------- графика ---------- */

/** Спарклайн: линия 2px с лёгкой заливкой, цвет берётся из currentColor. */
function spark(a, w = 96, h = 28) {
  const mx = Math.max(...a)
  const mn = Math.min(...a)
  const r = mx - mn || 1
  const pts = a.map((v, i) => [(i / (a.length - 1)) * w, h - 3 - ((v - mn) / r) * (h - 6)])
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ')
  const last = pts[pts.length - 1]
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true"><path d="${d} L${w} ${h} L0 ${h}Z" fill="currentColor" opacity=".1"/><path d="${d}" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/><circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="2.5" fill="currentColor"/></svg>`
}

/** Мини-столбики по дням (конверсии кампании). */
function bars(a, w = 56, h = 20) {
  const mx = Math.max(...a, 1)
  const bw = w / a.length
  return `<svg class="bars" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true">${a
    .map((v, i) => {
      const bh = Math.max(2, (v / mx) * h)
      return `<rect x="${(i * bw + 1).toFixed(1)}" y="${(h - bh).toFixed(1)}" width="${(bw - 2).toFixed(1)}" height="${bh.toFixed(1)}" rx="1.5" fill="currentColor" opacity="${v ? 0.85 : 0.25}"/>`
    })
    .join('')}</svg>`
}

/** График «цена заявки по дням» из ответа ИИ: одна серия, линия цели, подсказка при наведении. */
function cpaChart(w = 560, h = 170) {
  const { cpa, days, target } = M.chat
  const padL = 40
  const padB = 22
  const padT = 14
  const lo = 320
  const hi = 470
  const x = (i) => padL + (i / (cpa.length - 1)) * (w - padL - 12)
  const y = (v) => padT + (1 - (v - lo) / (hi - lo)) * (h - padT - padB)
  const d = cpa.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ')
  const grid = [350, 400, 450]
    .map((v) => `<line x1="${padL}" x2="${w - 12}" y1="${y(v)}" y2="${y(v)}" class="ch-grid"/><text x="${padL - 8}" y="${y(v) + 4}" text-anchor="end" class="ch-tick">${v}</text>`)
    .join('')
  const ticks = [0, 3, 7, 10, 13].map((i) => `<text x="${x(i)}" y="${h - 4}" text-anchor="middle" class="ch-tick">${days[i]}</text>`).join('')
  const hits = cpa
    .map((v, i) => `<g class="ch-hit"><rect x="${x(i) - 18}" y="0" width="36" height="${h - padB}" fill="transparent"/><circle cx="${x(i)}" cy="${y(v)}" r="4.5" class="ch-dot"/><title>${days[i]}: ${v} ₽</title></g>`)
    .join('')
  const wk = x(6.5)
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="Цена заявки по дням, рубли">
    ${grid}
    <rect x="${wk}" y="${padT}" width="${w - 12 - wk}" height="${h - padT - padB}" class="ch-week"/>
    <line x1="${padL}" x2="${w - 12}" y1="${y(target)}" y2="${y(target)}" class="ch-target"/>
    <text x="${w - 14}" y="${y(target) - 6}" text-anchor="end" class="ch-label">цель ${target} ₽</text>
    <text x="${wk + 8}" y="${padT + 12}" class="ch-tick">прошлая неделя</text>
    <path d="${d}" class="ch-line"/>
    <circle cx="${x(13)}" cy="${y(cpa[13])}" r="4" class="ch-end"/>
    <text x="${x(13) - 8}" y="${y(cpa[13]) + 18}" text-anchor="end" class="ch-value">${cpa[13]} ₽</text>
    <text x="${x(0) + 6}" y="${y(cpa[0]) + 18}" class="ch-value">${cpa[0]} ₽</text>
    ${ticks}${hits}
  </svg>`
}

/** Точки расхода лимита у класса модели: ●●○. */
const dots = (n) => `<span class="dots" aria-label="расход лимита ${n} из 3">${[1, 2, 3].map((i) => `<i class="${i <= n ? 'on' : ''}"></i>`).join('')}</span>`

/* ---------- иконки (24×24, обводка) ---------- */

const ICONS = {
  home: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  sparkles: '<path d="M11 3l1.9 5.1L18 10l-5.1 1.9L11 17l-1.9-5.1L4 10l5.1-1.9z"/><path d="M18.5 15l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/>',
  megaphone: '<path d="M3 10v4a1 1 0 0 0 1 1h2l6 4V5L6 9H4a1 1 0 0 0-1 1z"/><path d="M16 9a4 4 0 0 1 0 6"/><path d="M19 6a8 8 0 0 1 0 12"/>',
  filter: '<path d="M3 6h18M6.5 12h11M10 18h4"/>',
  chart: '<path d="M3 3v18h18"/><path d="M7 15l4-5 3 3 5-7"/>',
  zap: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
  history: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l3 2"/>',
  sliders: '<path d="M4 6h8M16 6h4M4 12h2M10 12h10M4 18h10M18 18h2"/><circle cx="14" cy="6" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="16" cy="18" r="2"/>',
  plug: '<path d="M9 2v6M15 2v6M6 8h12v3a6 6 0 0 1-12 0zM12 17v5"/>',
  card: '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20"/>',
  inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5h13L22 12v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  down: '<path d="M6 9l6 6 6-6"/>',
  right: '<path d="M9 6l6 6-6 6"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
  up: '<path d="M12 19V5M5 12l7-7 7 7"/>',
  pause: '<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>',
  play: '<path d="M7 4l13 8-13 8z"/>',
  alert: '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.01"/>',
  wallet: '<path d="M3 7a2 2 0 0 1 2-2h13v4"/><path d="M3 7v10a2 2 0 0 0 2 2h15V9H5a2 2 0 0 1-2-2z"/><path d="M16 14h1"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  panel: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
  more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
  chat: '<path d="M21 12a8 8 0 0 1-11.8 7L4 20l1.2-4.6A8 8 0 1 1 21 12z"/>',
  folder: '<path d="M3 6a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 1-1 1.7M12 17v.01"/>',
}
const ic = (name, size = 18) =>
  `<svg class="ic" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ''}</svg>`

