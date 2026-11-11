/* Вариант 3 «Пульт»: плотное рабочее место — всё на виду, ИИ в боковой панели и знает, что открыто на экране. */

const SECTIONS = [['home', 'home', 'Сводка'], ['chat', 'sparkles', 'ИИ-помощник'], ['camps', 'megaphone', 'Кампании'], ['queries', 'filter', 'Чистка запросов']]
const EXTRA = [['chart', 'Аналитика'], ['zap', 'Автопилот'], ['history', 'История']]
const SERVICE = [['plug', 'Подключения'], ['sliders', 'Настройки'], ['card', 'Тариф'], ['help', 'Справка']]
const CHAT_TASK = { c1: 't3', c2: 't4', c3: 't5' }
/* Расход, заявки и цена заявки по группам из предложения — для подробного вида. */
const GROUPS = [[4100, 8, 513], [3900, 7, 557], [2900, 7, 414]]
const SEV = { urgent: ['bad', 'Срочно'], today: ['warn', 'Сегодня'] }
const VERDICT = { no: ['bad', 'Мусор'], maybe: ['warn', 'Под вопросом'], yes: ['ok', 'Полезный'] }
const CFILTER = { all: ['Все', () => true], attn: ['Требуют внимания', (c) => c.health === 'bad' || c.health === 'warn'], off: ['Остановленные', (c) => !c.on] }
let cfilter = 'all'

const pct = (n) => `${n.toFixed(1).replace('.', ',')}%`
const words = (n) => plural(n, 'запрос', 'запроса', 'запросов')
const rejected = `<span class="st st--off">${ic('x', 13)}Отклонено</span>`
const shownCampaigns = () => visibleCampaigns().filter(CFILTER[cfilter][1])
const shownQueries = () => M.queries.filter((q) => S.qfilter === 'all' || q.v === S.qfilter)
const seg = () => `<div class="seg" role="group" aria-label="Вид"><button class="${pro() ? '' : 'active'}" data-act="mode:simple">Просто</button><button class="${pro() ? 'active' : ''}" data-act="mode:pro">Подробно</button></div>`

function rail() {
  const clients = [...new Set(M.projects.map((p) => p.client))]
  const counts = { chat: pendingCount(), queries: S.decided.minus ? 0 : minusCount() }
  const nv = (k, i, label) => `<button class="nv ${k && S.screen === k ? 'active' : ''}" data-act="go:${k || 'stub'}">${ic(i, 16)}<span class="t">${label}</span>${counts[k] ? `<i class="num">${counts[k]}</i>` : ''}</button>`
  return `<aside class="rail" data-scroll="rail">
    <button class="brand" data-act="go:home"><i>${ic('sparkles', 14)}</i><span class="t">AiMarketing</span></button>
    ${SECTIONS.map(([k, i, label]) => nv(k, i, label)).join('')}
    ${EXTRA.map(([i, label]) => nv('', i, label)).join('')}
    <div class="rail__projects">
      <h4>Проекты</h4>
      <button class="pj ${S.project ? '' : 'active'}" data-act="project:">${ic('folder', 14)}<span class="t">Все проекты</span><span class="num">${M.projects.length}</span></button>
      ${clients.map((c) => `<small class="client">${c || 'Без клиента'}</small>${M.projects.filter((p) => p.client === c).map((p) => `<button class="pj ${S.project === p.id ? 'active' : ''}" data-act="project:${p.id}" title="${STATUS[p.status]}: ${kpiText(p)}"><span class="dot dot--${p.status}"></span><span class="t">${p.name}</span><span class="num">${p.kpi === 'CPA' ? num(p.value) : p.value + '%'}</span></button>`).join('')}`).join('')}
    </div>
    <div class="rail__foot">
      <div class="limit" title="Сколько помощника осталось в этом месяце"><span>Лимит ИИ</span><b class="num">${M.plan.used}%</b></div>
      <div class="limit__bar"><i style="width:${M.plan.used}%"></i></div>
      <small>Тариф ${M.plan.name} до ${M.plan.until}</small>
      <div class="svc">${SERVICE.map(([i, label]) => `<button data-act="go:stub" title="${label}" aria-label="${label}">${ic(i, 16)}</button>`).join('')}</div>
    </div>
  </aside>`
}

function topbar() {
  const n = pendingCount()
  return `<header class="bar">
    <div class="crumbs"><span>${projectName(S.project)}</span>${ic('right', 13)}<b>${SCREEN_NAMES[S.screen] || 'Раздел'}</b></div>
    <button class="pill" title="Период">3–9 ноября${ic('down', 13)}</button>
    <button class="cmd">${ic('search', 14)}<span>Поиск и команды</span><kbd>Ctrl K</kbd></button>
    ${seg()}
    <button class="iconbtn" data-act="theme" aria-label="Сменить тему">${ic(S.theme === 'dark' ? 'sun' : 'moon', 16)}</button>
    ${S.screen === 'chat' ? '' : `<button class="aibtn ${S.dock ? 'on' : ''}" data-act="dock" title="Панель ИИ-помощника">${ic('sparkles', 15)}ИИ${n ? `<i class="num">${n}</i>` : ''}</button>`}
    <span class="avatar">А</span>
  </header>`
}

function composer(placeholder) {
  const m = M.models[S.model]
  return `<div class="composer"><textarea rows="2" placeholder="${placeholder}"></textarea>
    <div class="composer__row"><button class="model" data-act="model:next" title="Класс модели: чем больше точек, тем больше лимита тратит ответ">${m[0]}${dots(m[1])}</button><button class="send" data-act="go:chat" aria-label="Отправить">${ic('up', 16)}</button></div></div>`
}

/* ---------- панель ИИ ---------- */

function pending(t) {
  const st = S.decided[t.id]
  return `<article class="pend ${st ? 'is-closed' : ''}"><small>${project(t.project).name}</small><b>${t.title}</b>
    ${st ? '' : `<p>${t.text}</p>${t.diff ? `<table class="diff">${t.diff.map((d) => `<tr><td>${d[0]}</td><td class="r"><s>${d[1]}</s> → <b>${d[2]}</b></td></tr>`).join('')}</table>` : ''}`}
    <div class="pend__acts">${st
      ? `${st === 'done' ? badge('ok', 'Выполнено') : rejected}<button class="btn btn--ghost" data-act="undo:${t.id}">${ic('undo', 13)}Вернуть</button>`
      : `<button class="btn btn--pri" data-act="done:${t.id}">Выполнить</button><button class="btn" data-act="reject:${t.id}">Отклонить</button><button class="btn btn--ghost" data-act="go:chat">Открыть чат</button>`}</div></article>`
}

function dock() {
  const n = pendingCount()
  const sel = visibleCampaigns().filter((c) => S.sel.has(c.id))
  const ctx = S.screen === 'camps' && sel.length ? `выбрано кампаний: ${sel.length}` : S.screen === 'queries' ? 'запросы okna-profi.ru за 7 дней' : `${projectName(S.project)} · ${SCREEN_NAMES[S.screen] || 'раздел'}`
  const prompts = S.screen === 'camps'
    ? sel.length ? ['Почему у них дорогая заявка?', 'Что из этого отключить?', 'Сравни их между собой'] : ['Какие кампании тратят впустую?', 'Куда добавить бюджет?', 'Собери новую кампанию']
    : S.screen === 'queries' ? ['Разметь новые запросы', 'Почему «ремонт окон» под вопросом?', 'Собери минус-фразы по группам']
    : ['Что сегодня важнее всего?', 'Почему заявка у msk дороже цели?', 'Сравни проекты за неделю']
  const body = S.docktab === 'pending'
    ? `<div class="dock__body" data-scroll="dock">${M.tasks.filter((t) => t.group === 'ai').map(pending).join('')}
        <p class="hint">Ничего не изменится, пока вы не нажмёте «Выполнить». Вернуть как было можно в «Истории».</p></div>`
    : `<div class="dock__body" data-scroll="dock">
        <div class="ctx">${ic('panel', 14)}<span>Видит экран: <b>${ctx}</b></span></div>
        <small class="cap">Спросить про этот экран</small>
        ${prompts.map((s) => `<button class="ask" data-act="go:chat"><span>${s}</span>${ic('right', 14)}</button>`).join('')}
        <small class="cap">Последний чат</small>
        <button class="last" data-act="go:chat"><b>${M.chats[0].title}</b><span>${M.chat.lead} ставки выросли без новых заявок, а выходные были дороже обычного.</span><small>okna-profi.ru · ${M.chats[0].when}</small></button>
        <p class="hint">ИИ отвечает только на ваш вопрос. Сигналы на экранах считает сам сервис — лимит на них не тратится.</p>
      </div>${composer('Спросите про то, что на экране…')}`
  return `<aside class="dock">
    <header class="dock__head"><div class="tabs"><button class="${S.docktab === 'chat' ? 'active' : ''}" data-act="docktab:chat">Помощник</button><button class="${S.docktab === 'pending' ? 'active' : ''}" data-act="docktab:pending">Ждут решения${n ? `<i class="num">${n}</i>` : ''}</button></div>
      <button class="iconbtn" data-act="dock" aria-label="Скрыть панель">${ic('x', 16)}</button></header>
    ${body}
  </aside>`
}

/* ---------- Сводка ---------- */

function alertRow(t) {
  const st = S.decided[t.id]
  const p = t.project && project(t.project)
  const where = p ? p.name : 'Все проекты'
  if (st) return `<tr class="is-closed"><td>${st === 'later' ? badge('off', 'Скрыто') : badge('ok', 'Готово')}</td><td><s>${t.title}</s></td><td class="muted">${where}</td>
    <td class="acts"><button class="btn btn--ghost" data-act="undo:${t.id}">${ic('undo', 13)}Вернуть</button></td></tr>`
  const [tone, label] = SEV[t.group]
  const act = t.go ? `go:${t.go}` : t.ai ? 'go:chat' : `done:${t.id}`
  return `<tr><td>${badge(tone, label)}</td><td><b>${t.title}</b>${pro() ? '' : `<small>${t.text}</small>`}</td><td class="muted">${where}</td>
    <td class="acts"><button class="btn ${t.group === 'urgent' ? 'btn--pri' : ''}" data-act="${act}">${t.ai ? ic('sparkles', 13) : ''}${t.primary}${t.external ? ic('external', 13) : ''}</button>${t.ai ? '' : `<button class="btn btn--ghost btn--icon" data-act="go:chat" title="Спросить ИИ об этом" aria-label="Спросить ИИ об этом">${ic('sparkles', 14)}</button>`}<button class="btn btn--ghost btn--icon" data-act="later:${t.id}" title="Скрыть до завтра" aria-label="Скрыть до завтра">${ic('x', 14)}</button></td></tr>`
}

function home() {
  const T = totals()
  const tasks = visibleTasks().filter((t) => t.group !== 'ai')
  const open = tasks.filter((t) => !S.decided[t.id]).length
  const projs = M.projects.filter((p) => !S.project || p.id === S.project)
  const n = pendingCount()
  const spend = S.project ? project(S.project).spark : M.spendDays.slice(7)
  const alerts = `<section class="panel"><header class="panel__head"><h2>Требует внимания <span class="num">${open}</span></h2><small>считает сервис по цифрам — лимит ИИ не тратится</small></header>
    <table class="tbl tbl--alerts">${tasks.map(alertRow).join('')}</table></section>`
  if (!pro()) return `<div class="page">
    <div class="figs">
      <div class="fig"><small>Потрачено сегодня</small><b class="num">${rub(T.today)}</b><span>вчера ${rub(T.yesterday)}</span></div>
      <div class="fig"><small>Заявок за 7 дней</small><b class="num">${T.conv}</b><span class="up">▲ на 8% больше</span></div>
      <div class="fig"><small>Одна заявка стоит</small><b class="num">${rub(T.cpa)}</b><span class="up">▼ на 2% дешевле</span></div>
      <button class="fig fig--link" data-act="docktab:pending"><small>Предложения ИИ</small><b class="num">${n}</b><span>${n ? 'ждут вашего решения' : 'все разобраны'}</span></button>
    </div>
    ${alerts}
    <section class="panel"><header class="panel__head"><h2>Проекты <span class="num">${projs.length}</span></h2></header>
      <div class="pgrid">${projs.map((p) => `<button class="pcard ${S.project === p.id ? 'active' : ''}" data-act="project:${S.project === p.id ? '' : p.id}">
        <div class="pcard__top"><b>${p.name}</b>${badge(p.status)}</div><p>${kpiVerdict(p)}</p>
        <div class="pcard__row"><span class="num">Сегодня ${rub(p.today)}</span>${spark(p.spark, 72, 22)}</div></button>`).join('')}</div></section>
  </div>`
  const tile = (label, value, sub, sp = '') => `<div class="kpi"><small>${label}</small><b class="num">${value}</b><div class="kpi__foot"><span>${sub}</span>${sp}</div></div>`
  return `<div class="page">
    <div class="kpis">
      ${tile('Расход сегодня', rub(T.today), `вчера ${rub(T.yesterday)}`)}
      ${tile('Расход · 7 дней', rub(T.week), '<i title="к прошлой неделе">▲ 6%</i>', spark(spend, 60, 22))}
      ${tile('Конверсии · 7 дней', T.conv, '<i class="up" title="к прошлой неделе">▲ 8%</i>', spark(M.convDays.slice(7), 60, 22))}
      ${tile('CPA · 7 дней', rub(T.cpa), '<i class="up" title="к прошлой неделе">▼ 2%</i>', spark(M.cpaDays.slice(7), 60, 22))}
      <button class="kpi kpi--link" data-act="docktab:pending"><small>Предложения ИИ</small><b class="num">${n}</b><div class="kpi__foot"><span>${n ? 'ждут решения' : 'все разобраны'}</span>${ic('right', 14)}</div></button>
    </div>
    ${alerts}
    <section class="panel"><header class="panel__head"><h2>Проекты <span class="num">${projs.length}</span></h2><small>нажмите на строку, чтобы оставить один проект</small></header>
      <div class="scroll-x"><table class="tbl">
        <tr><th>Проект</th><th>Состояние</th><th class="r">Сегодня</th><th class="r opt">Вчера</th><th class="r">7 дней</th><th class="r">Конв.</th><th class="r">KPI</th><th class="r opt">Цель</th><th class="r opt">Темп</th><th class="r">Баланс</th><th>Расход, 7 дн.</th></tr>
        ${projs.map((p) => `<tr class="is-link ${S.project === p.id ? 'is-sel' : ''}" data-act="project:${S.project === p.id ? '' : p.id}"><td><b>${p.name}</b><small>${p.client || 'Без клиента'} · ${p.camps} камп.</small></td><td>${badge(p.status)}</td>
          <td class="r">${rub(p.today)}</td><td class="r opt">${rub(p.yesterday)}</td><td class="r">${rub(p.week)}</td><td class="r">${p.conv}</td>
          <td class="r"><b>${p.kpi} ${p.kpi === 'CPA' ? rub(p.value) : p.value + '%'}</b></td><td class="r opt">${p.kpi === 'CPA' ? rub(p.target) : p.target + '%'}</td><td class="r opt">${p.pace}%</td>
          <td class="r">${rub(p.balance)}<small>${p.balanceDays <= 3 ? `<span class="st st--bad">${ic('alert', 12)}на ${p.balanceDays} дн.</span>` : `на ${p.balanceDays} дн.`}</small></td><td>${spark(p.spark, 60, 20)}</td></tr>`).join('')}
      </table></div></section>
  </div>`
}

/* ---------- ИИ-помощник ---------- */

function decision() {
  const st = S.decided.t3
  if (!st) return `<button class="btn btn--pri" data-act="done:t3">${ic('check', 14)}Выполнить</button><button class="btn" data-act="reject:t3">Отклонить</button>`
  return `${st === 'done' ? badge('ok', 'Выполнено — ставки изменены в Директе') : rejected}<button class="btn btn--ghost" data-act="undo:t3">${ic('undo', 13)}${st === 'done' ? 'Вернуть как было' : 'Вернуть'}</button>`
}

function chat() {
  const c = M.chat
  const t = M.tasks[2]
  const row = (x) => {
    const wait = CHAT_TASK[x.id] && !S.decided[CHAT_TASK[x.id]]
    return `<button class="chatrow ${x.id === 'c1' ? 'active' : ''}"><b>${x.title}</b><small>${x.project ? project(x.project).name : 'Все проекты'} · ${x.when}</small>${wait ? `<span class="wait">${ic('sparkles', 12)}ждёт решения</span>` : ''}</button>`
  }
  return `<div class="chatgrid">
    <aside class="chatlist" data-scroll="chats">
      <button class="btn btn--block">${ic('plus', 14)}Новый чат</button>
      <label class="find">${ic('search', 14)}<input placeholder="Найти чат"></label>
      ${M.chats.map(row).join('')}
    </aside>
    <section class="thread">
      <header class="thread__head"><b>${M.chats[0].title}</b><span class="pill">okna-profi.ru</span><span class="muted">${M.chats[0].when}</span></header>
      <div class="thread__scroll" data-scroll="main"><div class="thread__in">
        <div class="q"><small>Вы</small><p>${c.question}</p></div>
        <div class="a"><small>${ic('sparkles', 13)}Помощник · ${M.models[1][0]}</small>
          <p><b>${c.lead}</b></p>
          <ol>${c.points.map((p) => `<li><b>${p[0]}</b> ${p[1]}</li>`).join('')}</ol>
          <figure class="chartbox"><figcaption>Цена заявки по дням, ₽ · okna-profi.ru</figcaption>${cpaChart(640, 190)}</figure>
          <p class="good">${ic('check', 14)}<span>${c.good}</span></p>
          <details class="steps" ${pro() ? 'open' : ''}><summary>Что я посмотрел · ${c.steps.length}</summary><ul>${c.steps.map((s) => `<li>${s}</li>`).join('')}</ul></details>
          <div class="prop ${S.decided.t3 ? 'is-closed' : ''}">
            <header><span class="tag">${ic('sparkles', 12)}Предложение</span><b>${t.title}</b><span class="muted">«Остекление балконов — Поиск»</span></header>
            <table class="tbl"><tr><th>Группа объявлений</th>${pro() ? '<th class="r">Расход</th><th class="r">Заявки</th><th class="r">CPA</th>' : ''}<th class="r">Ставка</th><th class="r">Станет</th></tr>
              ${t.diff.map((d, i) => `<tr><td>${d[0]}</td>${pro() ? `<td class="r">${rub(GROUPS[i][0])}</td><td class="r">${GROUPS[i][1]}</td><td class="r">${rub(GROUPS[i][2])}</td>` : ''}<td class="r"><s>${d[1]}</s></td><td class="r"><b class="hl">${d[2]}</b></td></tr>`).join('')}</table>
            <p class="effect">Расход <b class="num">−1 900 ₽ в неделю</b> · заявки <b>столько же или −1</b> · цена заявки <b class="num">402 → около 375 ₽</b></p>
            <footer>${decision()}<small>Ничего не изменится, пока вы не нажмёте «Выполнить».</small></footer>
          </div>
        </div>
      </div></div>
      <div class="thread__foot">${composer('Уточните или попросите что-то ещё…')}</div>
    </section>
  </div>`
}

/* ---------- Кампании ---------- */

function camps() {
  const all = visibleCampaigns()
  const list = shownCampaigns()
  const sel = all.filter((c) => S.sel.has(c.id))
  const allSel = list.length > 0 && list.every((c) => S.sel.has(c.id))
  const check = (c) => `<button class="check ${S.sel.has(c.id) ? 'on' : ''}" data-act="sel:${c.id}" aria-label="Выбрать">${ic('check', 12)}</button>`
  const checkAll = `<button class="check ${allSel ? 'on' : ''}" data-act="selall" aria-label="Выбрать все">${ic('check', 12)}</button>`
  const sw = (c) => `<button class="switch ${c.on ? 'on' : ''}" role="switch" aria-checked="${c.on}" aria-label="${c.on ? 'Остановить' : 'Запустить'}" data-act="toggle:${c.id}"></button>`
  const sum = (k) => list.reduce((s, c) => s + c[k], 0)
  const where = (c) => `${c.type}${S.project ? '' : ' · ' + project(c.project).name}`
  const table = pro()
    ? `<table class="tbl">
        <tr><th class="w0">${checkAll}</th><th>Кампания</th><th>Вкл.</th><th>Состояние</th><th class="r">Расход</th><th class="r">Клики</th><th class="r opt">CPC</th><th class="r">Конв.</th><th class="r opt">CR</th><th class="r">CPA / ДРР</th><th class="opt">Конв., 7 дн.</th></tr>
        ${list.map((c) => `<tr class="${S.sel.has(c.id) ? 'is-sel' : ''}"><td class="w0">${check(c)}</td><td><b>${c.name}</b><small>${where(c)}</small></td><td>${sw(c)}</td><td>${badge(c.health)}</td>
          <td class="r">${rub(c.cost)}</td><td class="r">${num(c.clicks)}</td><td class="r opt">${rub(c.cost / c.clicks)}</td><td class="r">${c.conv}</td><td class="r opt">${pct((c.conv / c.clicks) * 100)}</td><td class="r"><b>${campKpi(c)}</b></td><td class="opt">${bars(c.spark)}</td></tr>`).join('')}
        ${list.length ? `<tr class="total"><td></td><td>Итого: ${list.length} ${plural(list.length, 'кампания', 'кампании', 'кампаний')}</td><td></td><td></td><td class="r">${rub(sum('cost'))}</td><td class="r">${num(sum('clicks'))}</td><td class="r opt">${rub(sum('cost') / sum('clicks'))}</td><td class="r">${sum('conv')}</td><td class="r opt">${pct((sum('conv') / sum('clicks')) * 100)}</td><td></td><td class="opt"></td></tr>` : ''}
      </table>`
    : `<table class="tbl tbl--roomy">
        <tr><th class="w0">${checkAll}</th><th>Кампания</th><th>Вкл.</th><th>Что с ней</th><th class="r">Расход</th><th class="r">Заявки</th></tr>
        ${list.map((c) => `<tr class="${S.sel.has(c.id) ? 'is-sel' : ''}"><td class="w0">${check(c)}</td><td><b>${c.name}</b><small>${where(c)}</small></td><td>${sw(c)}</td><td>${badge(c.health)}<small>${campHealth(c)}</small></td><td class="r">${rub(c.cost)}</td><td class="r">${c.conv}</td></tr>`).join('')}
      </table>`
  return `<div class="page">
    <div class="tools">
      <div class="ftabs">${Object.entries(CFILTER).map(([k, [label, fn]]) => `<button class="${cfilter === k ? 'active' : ''}" data-act="cfilter:${k}">${label}<i class="num">${all.filter(fn).length}</i></button>`).join('')}</div>
      <label class="find">${ic('search', 14)}<input placeholder="Найти кампанию"></label>
      <button class="btn btn--pri" data-act="go:chat">${ic('sparkles', 14)}Собрать новую с ИИ</button>
    </div>
    ${sel.length ? `<div class="bulk"><b>Выбрано: ${sel.length}</b><button class="btn" data-act="stopsel">${ic('pause', 13)}Остановить</button><button class="btn" data-act="docktab:chat">${ic('sparkles', 13)}Спросить ИИ про выбранные</button><button class="btn btn--ghost" data-act="selnone">Снять выбор</button></div>` : ''}
    <section class="panel"><div class="scroll-x">${list.length ? table : '<p class="empty">Таких кампаний нет.</p>'}</div></section>
  </div>`
}

/* ---------- Чистка запросов ---------- */

function queries() {
  const t = M.queryTotals
  const n = minusCount()
  const pick = (q) => {
    const v = verdictOf(q)
    return `<div class="pick"><button class="no ${v === 'no' ? 'on' : ''}" data-act="verdict:${q.id}=no">В минус</button><button class="yes ${v === 'yes' ? 'on' : ''}" data-act="verdict:${q.id}=yes">Оставить</button></div>`
  }
  const apply = S.decided.minus
    ? `<div class="apply is-done">${badge('ok', `Добавлено в минус-фразы: ${n}`)}<span>Экономия около ${rub(t.noCost)} в неделю. Вернуть как было можно в «Истории».</span><button class="btn btn--ghost" data-act="undo:minus">${ic('undo', 13)}Вернуть</button></div>`
    : `<div class="apply"><span>ИИ разметил <b class="num">${t.all}</b> ${words(t.all)} за 7 дней. В минус-фразы уйдёт <b class="num">${n}</b> — это около <b class="num">${rub(t.noCost)}</b> в неделю.</span><button class="btn btn--pri" data-act="done:minus">Добавить ${n} ${plural(n, 'минус-фразу', 'минус-фразы', 'минус-фраз')}</button></div>`
  if (!pro()) {
    const line = (q) => `<div class="qline ${verdictOf(q) === 'yes' ? 'is-kept' : ''}"><span class="t">${q.q}</span><span class="num muted">${rub(q.cost)}</span><button class="btn btn--ghost" data-act="verdict:${q.id}=yes">${verdictOf(q) === 'yes' ? 'Вернуть в мусор' : 'Оставить'}</button></div>`
    return `<div class="page">${apply}
      <div class="qcols">
        <section class="panel"><header class="panel__head"><h2>${badge('bad', 'Мусор')}<span class="num">${t.no}</span></h2><small>уйдут в минус-фразы</small></header>
          ${Object.entries(M.reasons).map(([k, r]) => `<h3>${r.label}<span class="num">${r.count} · ${rub(r.cost)}</span></h3>${M.queries.filter((q) => q.r === k).map(line).join('')}`).join('')}
        </section>
        <div class="qstack">
          <section class="panel"><header class="panel__head"><h2>${badge('warn', 'Под вопросом')}<span class="num">${t.maybe}</span></h2><small>ИИ не уверен — решите сами</small></header>
            ${M.queries.filter((q) => q.v === 'maybe').map((q) => `<div class="qline qline--tall"><div class="t"><b>${q.q}</b><small>${q.why}</small></div>${pick(q)}</div>`).join('')}
          </section>
          <section class="panel"><header class="panel__head"><h2>${badge('ok', 'Полезные')}<span class="num">${t.yes}</span></h2><small>делать ничего не нужно</small></header>
            ${M.queries.filter((q) => q.v === 'yes').map((q) => `<div class="qline"><span class="t">${q.q}</span><span class="num muted">${q.conv} ${plural(q.conv, 'заявка', 'заявки', 'заявок')}</span></div>`).join('')}
            <button class="more">Показать все ${t.yes}${ic('down', 13)}</button>
          </section>
        </div>
      </div>
    </div>`
  }
  const list = shownQueries()
  const tabs = [['all', 'Все', t.all], ['no', 'Мусор', t.no], ['maybe', 'Под вопросом', t.maybe], ['yes', 'Полезные', t.yes]]
  const total = tabs.find((x) => x[0] === S.qfilter)[2]
  return `<div class="page">${apply}
    <div class="tools">
      <div class="ftabs">${tabs.map(([k, label, count]) => `<button class="${S.qfilter === k ? 'active' : ''}" data-act="qfilter:${k}">${label}<i class="num">${count}</i></button>`).join('')}</div>
      <div class="keys"><span><kbd>J</kbd><kbd>K</kbd>по строкам</span><span><kbd>X</kbd>в минус</span><span><kbd>Enter</kbd>оставить</span></div>
    </div>
    <section class="panel"><div class="scroll-x"><table class="tbl tbl--triage">
      <tr><th class="w0">№</th><th>Запрос</th><th>Вердикт ИИ</th><th class="r opt">Клики</th><th class="r">Расход</th><th class="r">Конв.</th><th class="r">Решение</th></tr>
      ${list.map((q, i) => `<tr class="${i === S.cursor ? 'cur' : ''} ${verdictOf(q) !== q.v ? 'is-changed' : ''}" data-act="cursor:${i}"><td class="w0 muted">${i + 1}</td><td><b>${q.q}</b><small>${q.r ? M.reasons[q.r].label : q.why || 'Есть заявки'}</small></td><td>${badge(...VERDICT[q.v])}</td>
        <td class="r opt">${q.clicks}</td><td class="r">${rub(q.cost)}</td><td class="r">${q.conv || '—'}</td><td class="r">${pick(q)}</td></tr>`).join('')}
    </table></div>
    <p class="empty">Показаны первые ${list.length} из ${total} — в макете остальные не загружены.</p></section>
  </div>`
}

const stub = () => `<div class="page"><section class="panel"><p class="empty">Этот раздел в макете не нарисован: Аналитика, Автопилот, История и служебные страницы будут оформлены так же — плотные таблицы и панель ИИ справа.</p></section></div>`
const SCREENS = { home, chat, camps, queries }

return {
  name: 'Пульт',
  theme: 'dark',
  render: () => {
    const isChat = S.screen === 'chat'
    return `<div class="shell">${rail()}<div class="work">${topbar()}<div class="body">
      <main class="main" ${isChat ? '' : 'data-scroll="main"'}>${(SCREENS[S.screen] || stub)()}</main>${S.dock && !isChat ? dock() : ''}
    </div></div></div>`
  },
  actions: {
    cfilter: (v) => {
      cfilter = v
    },
    cursor: (v) => {
      S.cursor = +v
    },
    selall: () => {
      const list = shownCampaigns()
      const all = list.every((c) => S.sel.has(c.id))
      list.forEach((c) => (all ? S.sel.delete(c.id) : S.sel.add(c.id)))
    },
    stopsel: () => {
      M.campaigns.filter((c) => S.sel.has(c.id) && c.on).forEach((c) => {
        c.was = c.health
        c.health = 'off'
        c.on = false
      })
      S.sel.clear()
    },
  },
  /* Разбор запросов с клавиатуры: J/K или стрелки — строка, X — в минус, Enter — оставить. */
  keys: (e) => {
    if (S.screen !== 'queries' || !pro()) return false
    const list = shownQueries()
    const q = list[S.cursor]
    const next = () => (S.cursor = Math.min(list.length - 1, S.cursor + 1))
    if (e.code === 'KeyJ' || e.code === 'ArrowDown') next()
    else if (e.code === 'KeyK' || e.code === 'ArrowUp') S.cursor = Math.max(0, S.cursor - 1)
    else if (e.code === 'KeyX' && q) {
      S.verdicts[q.id] = 'no'
      next()
    } else if (e.code === 'Enter' && q) {
      S.verdicts[q.id] = 'yes'
      next()
    } else return false
    return true
  },
  after: (app) => {
    const cur = app.querySelector('tr.cur')
    if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: 'nearest' })
  },
}
