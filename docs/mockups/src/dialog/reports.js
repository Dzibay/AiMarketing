/* Отчёты и общие графики макета. Правила графиков: одна шкала на график, тонкие линии, подписи серым, подсказка при наведении. */

const mod = (a, n) => ((a % n) + n) % n
const sum = (a) => a.reduce((s, v) => s + v, 0)
const avg = (a) => sum(a) / (a.length || 1)
const tickText = (v) => (Number.isInteger(v) ? num(v) : String(v).replace('.', ','))
const pct = (v) => `${String(v).replace('.', ',')}%`

/** Ровные деления оси от нижней до верхней границы. */
function ticksFor(lo, hi, minStep = 0) {
  if (hi <= lo) hi = lo + 1
  const raw = (hi - lo) / 4
  const pow = 10 ** Math.floor(Math.log10(raw))
  const f = raw / pow
  const step = Math.max(minStep, (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * pow)
  const out = []
  for (let v = Math.floor(lo / step) * step; v < Math.ceil(hi / step) * step + step / 2; v += step) out.push(+v.toFixed(4))
  return out
}

/** Подписи дней под графиком: не чаще семи, отсчёт от последнего дня. */
function dayTicks(labels, x, y) {
  const step = Math.ceil(labels.length / 7)
  let out = ''
  for (let i = labels.length - 1; i >= 0; i -= step) out += `<text x="${x(i).toFixed(1)}" y="${y}" text-anchor="middle" class="ch-tick">${labels[i]}</text>`
  return out
}

/** Линейный график. Серия с prev — прошлый период, рисуется пунктиром. */
function lineChart({ series, labels, target, fmt = rub, w = 720, h = 200, label = '' }) {
  const n = labels.length
  const padL = 56
  const padR = 18
  const padT = 24
  const padB = 26
  const all = series.flatMap((s) => s.data).concat(target != null ? [target] : [])
  const T = ticksFor(Math.min(...all), Math.max(...all))
  const lo = T[0]
  const hi = T[T.length - 1]
  const x = (i) => padL + (i / Math.max(1, n - 1)) * (w - padL - padR)
  const y = (v) => padT + (1 - (v - lo) / (hi - lo)) * (h - padT - padB)
  const path = (a) => a.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ')
  const grid = T.map((v) => `<line x1="${padL}" x2="${w - padR}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" class="ch-grid"/><text x="${padL - 8}" y="${(y(v) + 4).toFixed(1)}" text-anchor="end" class="ch-tick">${tickText(v)}</text>`).join('')
  const main = series.find((s) => !s.prev) || series[0]
  const last = main.data[n - 1]
  const up = n < 2 || last >= main.data[n - 2]
  /* Подпись последней точки не садится на линию цели: уходит по другую сторону точки. */
  const side = (v) => y(last) + (v ? -10 : 18)
  const endY = target != null && Math.abs(side(up) - 4 - y(target)) < 12 ? side(!up) : side(up)
  const slot = (w - padL - padR) / Math.max(1, n - 1)
  const hits = labels.map((d, i) => `<g class="ch-hit"><rect x="${(x(i) - slot / 2).toFixed(1)}" y="0" width="${slot.toFixed(1)}" height="${h - padB}" fill="transparent"/>${series.map((s) => `<circle cx="${x(i).toFixed(1)}" cy="${y(s.data[i]).toFixed(1)}" r="4.5" class="ch-dot ${s.prev ? 'ch-dot--prev' : ''}"/>`).join('')}<title>${d}: ${series.map((s) => `${series.length > 1 ? `${s.name} — ` : ''}${fmt(s.data[i])}`).join('; ')}</title></g>`).join('')
  const legend = series.length > 1 ? `<div class="legend">${series.map((s) => `<span><i class="${s.prev ? 'prev' : ''}"></i>${s.name}</span>`).join('')}</div>` : ''
  return `${legend}<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="${attr(label)}">
    ${grid}
    ${target != null ? `<line x1="${padL}" x2="${w - padR}" y1="${y(target).toFixed(1)}" y2="${y(target).toFixed(1)}" class="ch-target"/><text x="${padL + 6}" y="${(y(target) - 6).toFixed(1)}" class="ch-label">цель ${fmt(target)}</text>` : ''}
    ${series.filter((s) => s.prev).map((s) => `<path d="${path(s.data)}" class="ch-line ch-line--prev"/>`).join('')}
    ${series.filter((s) => !s.prev).map((s) => `<path d="${path(s.data)}" class="ch-line"/>`).join('')}
    <circle cx="${x(n - 1).toFixed(1)}" cy="${y(last).toFixed(1)}" r="4" class="ch-end"/>
    <text x="${(x(n - 1) + 6).toFixed(1)}" y="${endY.toFixed(1)}" text-anchor="end" class="ch-value">${fmt(last)}</text>
    ${dayTicks(labels, x, h - 6)}${hits}
  </svg>`
}

/** Столбики по дням: от нуля, скруглён только верх, между столбиками зазор. */
function barChart({ data, labels, fmt = num, w = 520, h = 190, label = '', none = 'За эти дни ничего не было' }) {
  const n = data.length
  const padL = 48
  const padR = 8
  const padT = 12
  const padB = 26
  const base = h - padB
  const T = ticksFor(0, Math.max(...data, 1), data.every(Number.isInteger) ? 1 : 0)
  const hi = T[T.length - 1]
  const y = (v) => padT + (1 - v / hi) * (base - padT)
  const slot = (w - padL - padR) / n
  const bw = Math.min(28, slot - Math.max(2, slot * 0.2))
  const x = (i) => padL + i * slot + slot / 2
  const grid = T.map((v) => `<line x1="${padL}" x2="${w - padR}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" class="ch-grid"/><text x="${padL - 8}" y="${(y(v) + 4).toFixed(1)}" text-anchor="end" class="ch-tick">${tickText(v)}</text>`).join('')
  const marks = data.map((v, i) => {
    const l = x(i) - bw / 2
    const top = y(v)
    const r = Math.min(4, bw / 2, base - top)
    const d = v > 0 ? `<path d="M${l.toFixed(1)} ${base} V${(top + r).toFixed(1)} Q${l.toFixed(1)} ${top.toFixed(1)} ${(l + r).toFixed(1)} ${top.toFixed(1)} H${(l + bw - r).toFixed(1)} Q${(l + bw).toFixed(1)} ${top.toFixed(1)} ${(l + bw).toFixed(1)} ${(top + r).toFixed(1)} V${base} Z" class="ch-bar"/>` : ''
    return `<g class="ch-hit"><rect x="${(x(i) - slot / 2).toFixed(1)}" y="0" width="${slot.toFixed(1)}" height="${base}" fill="transparent"/>${d}<title>${labels[i]}: ${fmt(v)}</title></g>`
  }).join('')
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="${attr(label)}">
    ${grid}${marks}${Math.max(...data) > 0 ? '' : `<text x="${(padL + (w - padL - padR) / 2).toFixed(1)}" y="${((padT + base) / 2).toFixed(1)}" text-anchor="middle" class="ch-label">${none}</text>`}
    ${dayTicks(labels, x, h - 6)}
  </svg>`
}

/** Горизонтальные полосы «кто сколько потратил». */
function hbars(rows) {
  const max = Math.max(...rows.map((r) => r.value), 1)
  return `<div class="hbars">${rows.map((r) => `<div class="hbar"><div class="hbar__l">${r.label}</div><span class="hbar__bar" aria-hidden="true"><i style="width:${Math.max(1, (r.value / max) * 100).toFixed(1)}%"></i></span><b class="num">${r.text}</b><small class="num">${r.note || ''}</small></div>`).join('')}</div>`
}

/** Ряды по дням под выбранный проект: расход, конверсии, цена заявки или ДРР, подписи дат. back — на сколько дней раньше. */
function daily(n, back = 0) {
  const p = S.project && project(S.project)
  const out = { spend: [], conv: [], kpi: [], labels: [] }
  for (let i = 0; i < n; i++) {
    const k = back + n - 1 - i
    const j = mod(13 - k, 14)
    const fade = k > 13 ? 0.97 - 0.012 * (k % 4) : 1
    out.spend.push(Math.round((M.spendDays[j] * (p ? p.week / M.totals.week : 1) * fade) / 10) * 10)
    out.conv.push(Math.max(0, Math.round(M.convDays[j] * (p ? p.conv / M.totals.conv : 1) * fade)))
    const rel = M.cpaDays[j] / M.totals.cpa
    out.kpi.push(isDrr(p) ? Math.round(rel * p.value * 10) / 10 : Math.round(rel * (p ? p.value : M.totals.cpa)))
    const d = new Date(2026, 10, 9 - k)
    out.labels.push(`${d.getDate()}.${d.getMonth() + 1}`)
  }
  return out
}

const PERIODS = {
  7: { n: 7, k: 1, name: '7 дней', title: '3–9 ноября', prev: 'Прошлая неделя', word: 'неделей раньше', d: [6, 8, -2] },
  30: { n: 30, k: 4.3, name: '30 дней', title: '11 октября — 9 ноября', prev: 'Предыдущие 30 дней', word: 'месяцем раньше', d: [11, 14, -3] },
  m: { n: 9, k: 1.3, name: 'Этот месяц', title: '1–9 ноября', prev: 'Те же дни октября', word: 'в октябре', d: [4, 5, -1] },
}

const needProject = (what) => `<div class="card empty empty--sm">${ic('folder', 24)}<p>${what} считаются по одному сайту. Выберите, какой открыть.</p><div>${M.projects.map((p) => `<button class="btn btn--sm" data-act="project:${p.id}">${p.name}</button>`).join('')}</div></div>`
const dOf = (c, s) => mod(c.id * 7 + s * 5, 23) - 9 || 2

function reportTotals(P, D) {
  const p = S.project && project(S.project)
  const T = totals()
  const week = P.n === 7
  return {
    spend: week ? T.week : sum(D.spend),
    conv: week ? T.conv : sum(D.conv),
    kpi: p && week ? p.value : isDrr(p) ? Math.round(avg(D.kpi) * 10) / 10 : week ? T.cpa : Math.round(avg(D.kpi)),
  }
}

function reportsDyn(P, D, R) {
  const p = S.project && project(S.project)
  const drr = isDrr(p)
  const B = daily(P.n, P.n)
  const cmp = on('compare')
  const two = (a, b, name) => (cmp ? [{ name, data: a }, { name: P.prev, data: b, prev: true }] : [{ name, data: a }])
  const kpiName = drr ? 'ДРР' : 'CPA'
  const kfmt = drr ? pct : rub
  const camps = visibleCampaigns().filter((c) => !c.archived)
  return `<div class="kline card num">
      <div><small>Расход</small><b>${rub(R.spend)}</b><span>${delta(P.d[0])} к прошлому периоду</span></div>
      <div><small>Конверсии</small><b>${num(R.conv)}</b><span>${delta(P.d[1])} к прошлому периоду</span></div>
      <div><small>${kpiName}</small><b>${kfmt(R.kpi)}</b><span>${p ? `цель ${kfmt(p.target)}` : `${delta(P.d[2])} к прошлому периоду`}</span></div>
      <div><small>Клики</small><b>${num(sum(camps.map((c) => c.clicks)) * P.k)}</b><span>${delta(P.d[0] - 1)} к прошлому периоду</span></div>
    </div>
    <div class="fbar fbar--top">${chipFlag('compare', 'Сравнивать с прошлым периодом')}</div>
    <div class="duo">
      <div class="card pad"><h3 class="cap">Расход по дням, ₽</h3>${lineChart({ series: two(D.spend, B.spend, 'Этот период'), labels: D.labels, w: 520, h: 210, label: 'Расход по дням' })}</div>
      <div class="card pad"><h3 class="cap">${kpiName} по дням${drr ? ', %' : ', ₽'}</h3>${lineChart({ series: two(D.kpi, B.kpi, 'Этот период'), labels: D.labels, target: p ? p.target : null, fmt: kfmt, w: 520, h: 210, label: `${kpiName} по дням` })}</div>
    </div>
    <h2>Кампании<span>${camps.length}</span><small>изменение — к прошлому периоду</small></h2>
    <div class="card scroll-x"><table class="tbl num"><tr><th>Кампания</th><th class="r">Расход</th><th class="r">Изм.</th><th class="r">Клики</th><th class="r">Конверсии</th><th class="r">Изм.</th><th class="r">CPA / ДРР</th><th class="r">Изм.</th></tr>
      ${camps.map((c) => `<tr><td>${campName(c)}<small>${c.type}${S.project ? '' : ` · ${project(c.project).name}`}</small></td><td class="r">${rub(Math.round((c.cost * P.k) / 10) * 10)}</td><td class="r muted">${delta(dOf(c, 1))}</td>
        <td class="r">${num(c.clicks * P.k)}</td><td class="r">${Math.round(c.conv * P.k)}</td><td class="r muted">${c.conv ? delta(dOf(c, 2)) : '—'}</td><td class="r"><b>${campKpi(c)}</b></td><td class="r muted">${c.conv ? delta(dOf(c, 3)) : '—'}</td></tr>`).join('')}</table></div>
    ${S.project ? '' : `<h2>По кабинетам Директа<span>${M.cabinets.length}</span></h2>
      <div class="card scroll-x"><table class="tbl num"><tr><th>Кабинет</th><th class="r">Кампаний</th><th class="r">Баланс</th><th class="r">Расход</th><th class="r">Конверсии</th><th class="r">Цена конверсии</th></tr>
      ${M.cabinets.map((c) => `<tr><td><b>${c.login}</b></td><td class="r">${c.camps}</td><td class="r">${rub(c.balance)}</td><td class="r">${rub(Math.round((c.cost * P.k) / 10) * 10)}</td><td class="r">${Math.round(c.conv * P.k)}</td><td class="r">${rub(c.cost / c.conv)}</td></tr>`).join('')}</table></div>`}
    <div class="acts"><button class="btn" data-act="chat:${S.project ? 'c1' : 'c5'}">${ic('sparkles', 16)}Разобрать период с ИИ</button><button class="btn btn--ghost" data-act="toast:Таблица скопирована — её можно вставить в Excel или Google Таблицы">${ic('copy', 16)}Скопировать таблицу</button></div>`
}

function reportsPlacements() {
  if (!S.project) return needProject('Площадки')
  const picked = M.placements.filter((x, i) => on(`pl:${i}`) && !on(`plb:${i}`)).length
  return `<div class="card pad"><p><b>Площадки в сетях.</b> Сайты и приложения, где показывалась реклама за 30 дней. Те, что тратят деньги без заявок, можно исключить — показы на них прекратятся.</p></div>
    <div class="fbar fbar--top">${field('plthr', 'Расход без заявок от', '250', { unit: '₽', w: 70, cls: 'field--row' })}
      <button class="btn btn--sm" data-act="plmark">Отметить подозрительные</button>
      <button class="btn btn--sm ${picked ? 'btn--pri' : ''}" data-act="${picked ? 'plban' : 'toast:Сначала отметьте площадки галочкой'}">Исключить выбранные${picked ? ` · ${picked}` : ''}</button></div>
    <div class="card scroll-x"><table class="tbl num"><tr><th></th><th>Площадка</th><th class="r">Показы</th><th class="r">Клики</th><th class="r">Расход</th><th class="r">Заявки</th><th>Заметка сервиса</th><th></th></tr>
      ${M.placements.map((x, i) => {
        const banned = on(`plb:${i}`)
        return `<tr class="${on(`pl:${i}`) && !banned ? 'is-change' : ''}"><td>${banned ? '' : checkBtn(`flag:pl:${i}`, on(`pl:${i}`), `Выбрать ${x.n}`)}</td><td>${banned ? `<s>${x.n}</s>` : `<b>${x.n}</b>`}<small>${x.kind}</small></td>
          <td class="r">${num(x.shows)}</td><td class="r">${x.clicks}</td><td class="r">${rub(x.cost)}</td><td class="r">${x.conv || '—'}</td>
          <td>${banned ? '<span class="st st--off">' + ic('x', 13) + 'Исключена</span>' : x.hint ? badge('warn', x.hint) : '<span class="muted">—</span>'}</td>
          <td class="r">${banned ? `<button class="btn btn--ghost btn--sm" data-act="flag:plb:${i}">${ic('undo', 14)}Вернуть</button>` : ''}</td></tr>`
      }).join('')}</table></div>
    <h2>Корректировки ставок<small>по устройствам, полу и возрасту</small></h2>
    <div class="card scroll-x"><table class="tbl num"><tr><th>Сегмент</th><th class="r">Сейчас</th><th class="r">Совет сервиса</th><th class="r">Новое значение</th></tr>
      ${M.bidAdjust.map((b, i) => `<tr><td>${b[0]}</td><td class="r">${b[1]}</td><td class="r muted">${b[2]}</td><td class="r"><span class="inp inp--sm"><input data-k="adj${i}" value="${attr(val(`adj${i}`, b[2]))}" aria-label="Корректировка для сегмента «${b[0]}»"></span></td></tr>`).join('')}</table></div>
    <div class="acts"><button class="btn btn--pri" data-act="toast:Корректировки применены. Запись появилась в «Истории»">Применить корректировки</button><button class="btn btn--ghost" data-act="go:history">${ic('history', 16)}Журнал и откат</button></div>`
}

function reportsAds() {
  if (!S.project) return needProject('Объявления')
  const picked = M.adStats.filter((a, i) => on(`ad:${i}`) && !on(`ads:${i}`)).length
  return `<div class="card pad"><p><b>Слабые объявления.</b> Сервис сравнивает объявления внутри одной группы: если по объявлению кликают заметно реже соседей и заявок нет — оно помечается как слабое.</p></div>
    <div class="fbar fbar--top"><button class="btn btn--sm" data-act="admark">Отметить слабые</button>
      <button class="btn btn--sm" data-act="${picked ? 'adstop' : 'toast:Сначала отметьте объявления галочкой'}">${ic('pause', 14)}Остановить выбранные${picked ? ` · ${picked}` : ''}</button>
      <button class="btn btn--sm" data-act="toast:ИИ напишет новый вариант и покажет его до отправки — около 1% месячного лимита">${ic('sparkles', 14)}Новый вариант с ИИ</button>
      ${chipFlag('moder', 'Сразу на модерацию')}</div>
    <div class="card scroll-x"><table class="tbl num"><tr><th></th><th>Объявление</th><th class="r">CTR на поиске</th><th class="r">CTR в сетях</th><th class="r">Клики</th><th class="r">Заявки</th><th>Вывод</th></tr>
      ${M.adStats.map((a, i) => {
        const stopped = on(`ads:${i}`)
        return `<tr class="${on(`ad:${i}`) && !stopped ? 'is-change' : ''}"><td>${stopped ? '' : checkBtn(`flag:ad:${i}`, on(`ad:${i}`), 'Выбрать объявление')}</td><td>${stopped ? `<s>${a.t}</s>` : `<b>${a.t}</b>`}<small>${a.g}</small></td>
          <td class="r">${pct(a.s)}</td><td class="r">${pct(a.n)}</td><td class="r">${a.clicks}</td><td class="r">${a.conv || '—'}</td>
          <td>${stopped ? `<span class="st st--off">${ic('pause', 13)}Остановлено</span>` : a.weak ? badge('warn', 'Слабое') : badge('ok', 'Работает')}</td></tr>`
      }).join('')}</table></div>
    <p class="meta">В макете показаны объявления одной кампании. ${on('moder') ? 'Новые варианты сразу уйдут на модерацию.' : 'Новые варианты сохранятся черновиками — на модерацию отправляете вы.'}</p>`
}

function reports() {
  const p = S.project && project(S.project)
  const drr = isDrr(p)
  const per = val('rper', '7')
  const P = PERIODS[per]
  const D = daily(P.n)
  const R = reportTotals(P, D)
  const tab = sub('reports', 'dyn')
  const kfmt = drr ? pct : rub
  const camps = visibleCampaigns().filter((c) => !c.archived).sort((a, b) => b.cost - a.cost).slice(0, 6)
  const simple = `<div class="tiles">
      ${tile('Потрачено', rub(R.spend), `на ${Math.abs(P.d[0])}% ${P.d[0] > 0 ? 'больше' : 'меньше'}, чем ${P.word}`)}
      ${tile(drr ? 'Заказы' : 'Заявки', num(R.conv), `на ${Math.abs(P.d[1])}% ${P.d[1] > 0 ? 'больше' : 'меньше'}, чем ${P.word}`)}
      ${tile(drr ? 'Доля расходов на рекламу' : 'Цена заявки', kfmt(R.kpi), p ? `цель — до ${kfmt(p.target)}` : `на ${Math.abs(P.d[2])}% ${P.d[2] > 0 ? 'дороже' : 'дешевле'}, чем ${P.word}`)}
    </div>
    <div class="duo">
      <div class="card pad"><h3 class="cap">Расход по дням, ₽</h3>${barChart({ data: D.spend, labels: D.labels, fmt: rub, label: 'Расход по дням' })}</div>
      <div class="card pad"><h3 class="cap">${drr ? 'Доля расходов на рекламу по дням, %' : 'Цена заявки по дням, ₽'}</h3>${lineChart({ series: [{ name: drr ? 'ДРР' : 'Цена заявки', data: D.kpi }], labels: D.labels, target: p ? p.target : null, fmt: kfmt, w: 520, h: 190, label: drr ? 'Доля расходов на рекламу по дням' : 'Цена заявки по дням' })}</div>
    </div>
    <h2>На что ушли деньги<small>кампании по расходу</small></h2>
    <div class="card pad">${hbars(camps.map((c) => ({ label: campName(c), value: c.cost, text: rub(Math.round((c.cost * P.k) / 10) * 10), note: c.conv ? `${Math.round(c.conv * P.k)} ${convWord(Math.round(c.conv * P.k), project(c.project))} · ${campKpi(c)}` : 'заявок нет' })))}</div>
    <div class="acts"><button class="btn btn--pri" data-act="chat:${S.project ? 'c1' : 'c5'}">${ic('sparkles', 16)}Попросить ИИ разобрать период</button></div>
    ${expertHint('Сравнение с прошлым периодом, площадки, слабые объявления и разбивка по кабинетам')}`
  return `<div class="page" data-scroll="main"><div class="wide">
    <div class="head"><div><p class="eyebrow">${projectName(S.project)} · ${P.title}</p><h1>Отчёты</h1></div>
      <div class="head__acts">${segm('rper', Object.entries(PERIODS).map(([k, v]) => [k, v.name]), '7')}</div></div>
    ${pro() ? `${ptabs('reports', [['dyn', 'Динамика'], ['pl', 'Площадки'], ['ads', 'Объявления']], 'dyn')}${tab === 'pl' ? reportsPlacements() : tab === 'ads' ? reportsAds() : reportsDyn(P, D, R)}` : simple}
  </div></div>`
}

Object.assign(ACT, {
  plmark: () => {
    const thr = parseInt(String(val('plthr', '250')).replace(/\D/g, ''), 10) || 0
    M.placements.forEach((x, i) => (S.flags[`pl:${i}`] = !x.conv && x.cost >= thr && !on(`plb:${i}`)))
    const n = M.placements.filter((x, i) => on(`pl:${i}`)).length
    S.toast = n ? `Отмечено: ${n} ${plural(n, 'площадка', 'площадки', 'площадок')} без заявок с расходом от ${rub(thr)}` : 'Под это условие площадок нет'
  },
  plban: () => {
    let n = 0
    M.placements.forEach((x, i) => {
      if (!on(`pl:${i}`) || on(`plb:${i}`)) return
      S.flags[`plb:${i}`] = true
      S.flags[`pl:${i}`] = false
      n++
    })
    S.toast = `Исключено: ${n} ${plural(n, 'площадка', 'площадки', 'площадок')}. Вернуть можно в «Истории»`
  },
  admark: () => {
    M.adStats.forEach((a, i) => (S.flags[`ad:${i}`] = !!a.weak && !on(`ads:${i}`)))
    S.toast = 'Отмечены объявления, по которым кликают заметно реже соседей'
  },
  adstop: () => {
    let n = 0
    M.adStats.forEach((a, i) => {
      if (!on(`ad:${i}`) || on(`ads:${i}`)) return
      S.flags[`ads:${i}`] = true
      S.flags[`ad:${i}`] = false
      n++
    })
    S.toast = `Остановлено: ${n} ${plural(n, 'объявление', 'объявления', 'объявлений')}. Вернуть можно в «Истории»`
  },
})
