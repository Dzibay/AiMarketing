/* Главная: вопрос ИИ, то, что ждёт решения, сигналы сервиса и короткие итоги. */

const TONE = { urgent: ['bad', 'Срочно'], today: ['warn', 'Стоит посмотреть'] }
const STARTER_CHAT = ['c1', 'c3', 'c4']
const WHO = { ai: ['sparkles', 'ИИ'], user: ['user', 'Вы'], rule: ['zap', 'Правило'] }
const isDrr = (p) => !!p && p.kpi !== 'CPA'
const convWord = (n, p) => (isDrr(p) ? plural(n, 'заказ', 'заказа', 'заказов') : plural(n, 'заявка', 'заявки', 'заявок'))
const versus = (d, up, down) => `на ${Math.abs(d)}% ${d > 0 ? up : down}, чем неделей раньше`
const topCamps = (n) => visibleCampaigns().filter((c) => !c.archived).sort((a, b) => b.cost - a.cost).slice(0, n)
const askChat = (t) => `chat:${TASK_CHAT[t.id] || 'c1'}`
const campName = (c) => `<button class="cname" data-act="camp:${c.id}" title="Открыть кампанию"><b>${c.name}</b></button>`
const histLine = (h) => `<div class="hline">${ic(WHO[h.who][0], 15)}<span><b>${h.title}</b><small>${h.day}, ${h.time} · ${WHO[h.who][1]}</small></span></div>`

/** Сигнал сервиса: находит код по цифрам, ИИ не тратится. */
function signal(t) {
  const st = S.decided[t.id]
  const p = t.project && project(t.project)
  if (st) return `<article class="sig is-closed"><p>${ic('check', 16)}<span><s>${t.title}</s> · ${st === 'later' ? 'скрыто до завтра' : 'готово'}</span></p><button class="btn btn--ghost btn--sm" data-act="undo:${t.id}">${ic('undo', 14)}Вернуть</button></article>`
  const [tone, label] = TONE[t.group]
  const act = t.go ? `at:${t.project || ''}=${t.go}` : t.ai ? askChat(t) : t.external ? 'toast:В рабочем кабинете здесь откроется оплата в Директе' : `done:${t.id}`
  return `<article class="sig">
    <div class="sig__top">${badge(tone, label)}<span>${p ? p.name : 'Все проекты'}</span></div>
    <h3>${t.title}</h3>${pro() ? '' : `<p>${t.text}</p>`}
    <div class="sig__acts"><button class="btn ${t.ai ? 'btn--pri' : ''}" data-act="${act}">${t.ai ? ic('sparkles', 15) : ''}${t.primary}${t.external ? ic('external', 14) : ''}</button>
      ${t.ai || t.external ? '' : `<button class="btn btn--ghost" data-act="${askChat(t)}" title="Спросить ИИ об этом">${ic('sparkles', 15)}Спросить</button>`}
      <button class="btn btn--ghost btn--icon" data-act="later:${t.id}" title="Скрыть до завтра" aria-label="Скрыть до завтра">${ic('x', 15)}</button></div>
  </article>`
}

/** Предложение ИИ, которое ждёт решения. */
function prow(t) {
  const st = S.decided[t.id]
  const p = project(t.project)
  if (st) return `<div class="prow is-closed"><div><small>${p.name}</small><b><s>${t.title}</s></b></div>
    <div class="prow__acts">${st === 'done' ? badge('ok', 'Выполнено') : rejected}<button class="btn btn--ghost btn--sm" data-act="undo:${t.id}">${ic('undo', 14)}Вернуть</button></div></div>`
  return `<div class="prow"><div><small>${p.name}</small><b>${t.title}</b><p>${t.text}</p>${pro() && t.diff ? diffTable(t) : ''}</div>
    <div class="prow__acts"><button class="btn btn--pri" data-act="done:${t.id}">Выполнить</button><button class="btn" data-act="reject:${t.id}">Отклонить</button><button class="btn btn--ghost" data-act="${askChat(t)}">Открыть чат</button></div></div>`
}

/** Итоги недели простыми словами. */
function weekTiles() {
  const T = totals()
  const p = S.project && project(S.project)
  return `<div class="tiles">
    ${tile('Потрачено', rub(T.week), versus(T.weekDelta, 'больше', 'меньше'))}
    ${tile(isDrr(p) ? 'Заказы' : 'Заявки', T.conv, versus(T.convDelta, 'больше', 'меньше'))}
    ${p ? tile(isDrr(p) ? 'Доля расходов на рекламу' : 'Цена заявки', isDrr(p) ? `${p.value}%` : rub(p.value), kpiVerdict(p)) : tile('Цена заявки', rub(T.cpa), versus(T.cpaDelta, 'дороже', 'дешевле'))}
    ${p ? tile('Деньги в Директе', rub(p.balance), `хватит примерно на ${p.balanceDays} ${plural(p.balanceDays, 'день', 'дня', 'дней')}`) : ''}
  </div>`
}

/** Четыре показателя со спарклайнами — экспертный вид. */
function kline() {
  const T = totals()
  const p = S.project && project(S.project)
  return `<div class="kline card num">
    <div><small>Сегодня</small><b>${rub(T.today)}</b><span>вчера ${rub(T.yesterday)}</span></div>
    <div><small>Расход за 7 дней</small><b>${rub(T.week)}</b><span>${delta(T.weekDelta)} к прошлой неделе</span>${spark(M.spendDays.slice(7), 48, 22)}</div>
    <div><small>Конверсии</small><b>${T.conv}</b><span>${delta(T.convDelta)} к прошлой неделе</span>${spark(M.convDays.slice(7), 48, 22)}</div>
    <div><small>${isDrr(p) ? 'ДРР' : 'CPA'}</small><b>${p ? (isDrr(p) ? `${p.value}%` : rub(p.value)) : rub(T.cpa)}</b><span>${p ? `цель ${isDrr(p) ? `${p.target}%` : rub(p.target)}` : `${delta(T.cpaDelta)} к прошлой неделе`}</span>${spark(M.cpaDays.slice(7), 48, 22)}</div>
  </div>`
}

/** Таблица проектов: темп бюджета, баланс и число сигналов. */
function projectsTable() {
  const list = M.projects.filter((p) => !on('onlybad') || p.status !== 'ok')
  const issues = (p) => M.tasks.filter((t) => t.project === p.id && !S.decided[t.id]).length
  return `<h2>Проекты <span>${list.length}</span><small><button class="chip chip--btn ${on('onlybad') ? 'is-on' : ''}" data-act="flag:onlybad">${on('onlybad') ? ic('check', 13) : ''}Только с проблемами</button></small></h2>
    <div class="card scroll-x"><table class="tbl num"><tr><th>Сайт</th><th>KPI за 7 дней</th><th class="r">Расход</th><th>Темп бюджета</th><th class="r">Баланс</th><th class="r">Сигналы</th></tr>
    ${list.map((p) => `<tr><td><button class="cname" data-act="project:${p.id}"><b>${p.name}</b></button><small>${p.client || 'Без клиента'}</small></td>
      <td>${kpiText(p)}<small>${badge(p.status)}</small></td><td class="r">${rub(p.week)}</td>
      <td><span class="meter" title="Прогноз расхода к концу месяца относительно бюджета"><i style="width:${Math.min(100, p.pace / 1.3).toFixed(0)}%"></i></span><small>${p.pace}% от плана</small></td>
      <td class="r">${rub(p.balance)}<small>на ${p.balanceDays} дн.</small></td><td class="r">${issues(p) || '—'}</td></tr>`).join('')}
    </table></div>`
}

/** Подробности проекта: неделя к неделе, бюджет месяца, баланс, кампании и действия. */
function projectBlocks(p) {
  const D = daily(14)
  const hist = M.history.filter((h) => h.project === p.id).slice(0, 3)
  return `<h2>Неделя к неделе<small>расход по дням, ₽</small></h2>
    <div class="card pad">${lineChart({ series: [{ name: 'Эта неделя', data: D.spend.slice(7) }, { name: 'Прошлая неделя', data: D.spend.slice(0, 7), prev: true }], labels: D.labels.slice(7), h: 190, label: 'Расход по дням: эта неделя и прошлая' })}</div>
    <div class="duo">
      <div class="card pad"><small class="cap">Бюджет месяца</small><b class="big num">${rub(p.month)} <span>из ${rub(p.budget)}</span></b>
        <span class="meter meter--wide"><i style="width:${Math.round((p.month / p.budget) * 100)}%"></i></span>
        <p class="muted">Темп ${p.pace}% от плана — к концу месяца выйдет около ${rub((p.budget * p.pace) / 100)}.</p></div>
      <div class="card pad"><small class="cap">Баланс кабинета Директа</small><b class="big num">${rub(p.balance)}</b>
        <p class="muted">Хватит примерно на ${p.balanceDays} ${plural(p.balanceDays, 'день', 'дня', 'дней')} при расходе около ${rub(p.week / 7)} в день.</p>
        <button class="btn btn--sm" data-act="toast:В рабочем кабинете здесь откроется оплата в Директе">Пополнить в Директе${ic('external', 14)}</button></div>
    </div>
    <h2>Топ кампаний<small><button class="lnk" data-act="go:camps">Все кампании</button></small></h2>
    <div class="card scroll-x"><table class="tbl num"><tr><th>Кампания</th><th>Состояние</th><th class="r">Расход</th><th class="r">Конверсии</th><th class="r">${isDrr(p) ? 'ДРР' : 'CPA'}</th></tr>
      ${topCamps(3).map((c) => `<tr><td>${campName(c)}<small>${c.type}</small></td><td>${badge(c.health)}</td><td class="r">${rub(c.cost)}</td><td class="r">${c.conv}</td><td class="r"><b>${campKpi(c)}</b></td></tr>`).join('')}</table></div>
    <h2>Последние действия<small><button class="lnk" data-act="go:history">Вся история</button></small></h2>
    <div class="card">${hist.length ? hist.map(histLine).join('') : '<p class="pad muted">За последние дни в проекте ничего не меняли.</p>'}</div>`
}

function home() {
  const tasks = visibleTasks()
  const props = tasks.filter((t) => t.group === 'ai')
  const allSigs = tasks.filter((t) => t.group !== 'ai')
  const sigs = pro() && val('sigf', 'all') === 'urgent' ? allSigs.filter((t) => t.group === 'urgent') : allSigs
  const p = S.project && project(S.project)
  const open = (list) => list.filter((t) => !S.decided[t.id]).length
  const simpleBottom = p
    ? `<h2>Кампании проекта<small><button class="lnk" data-act="go:camps">Все кампании</button></small></h2>
      <div class="card">${topCamps(3).map((c) => `<div class="crow crow--lite"><div>${campName(c)}<small>${c.type}</small></div><div class="crow__health">${campHealth(c)}</div><div class="crow__num num"><b>${rub(c.cost)}</b><small>${c.conv} ${convWord(c.conv, p)}</small></div>${badge(c.health)}</div>`).join('')}</div>`
    : `<h2>Проекты <span>${M.projects.length}</span></h2>
      <div class="pstrip">${M.projects.map((x) => `<button class="pc card" data-act="project:${x.id}"><b>${x.name}</b>${badge(x.status)}<p>${kpiVerdict(x)}</p></button>`).join('')}</div>`
  return `<div class="page" data-scroll="main"><div class="col">
    <p class="eyebrow">${M.date}</p>
    <h1>Что сделать с рекламой?</h1>
    ${composer(true, 'Спросите или поручите: «почему подорожали заявки?», «почисти запросы»…')}
    <div class="starters">${M.starters.map((s, i) => `<button data-act="chat:${STARTER_CHAT[i]}">${s}</button>`).join('')}</div>
    ${pro() ? kline() : ''}
    ${props.length ? `<h2>Ждут вашего решения <span>${open(props)}</span></h2><div class="card">${props.map(prow).join('')}</div>` : ''}
    <h2>Сервис заметил <span>${open(allSigs)}</span><small>по цифрам, без ИИ — лимит не тратится</small>${pro() ? segm('sigf', [['all', 'Все'], ['urgent', 'Срочные']], 'all') : ''}</h2>
    ${sigs.length ? `<div class="sigs">${sigs.map(signal).join('')}</div>` : `<div class="card pad muted">${allSigs.length ? 'Срочных сигналов нет.' : 'Сегодня всё спокойно: сервис не нашёл ничего, что требует внимания.'}</div>`}
    ${pro() ? '' : `<h2>За неделю<small><button class="lnk" data-act="go:reports">Все отчёты</button></small></h2>${weekTiles()}`}
    ${pro() ? (p ? projectBlocks(p) : projectsTable()) : simpleBottom}
  </div></div>`
}
