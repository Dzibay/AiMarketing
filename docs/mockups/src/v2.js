/* Вариант 2 «Диалог»: в центре разговор с ИИ, данные открываются рядом с ним. */

const MORE = [['chart', 'Аналитика'], ['zap', 'Автопилот'], ['history', 'История'], ['plug', 'Подключения'], ['sliders', 'Настройки'], ['card', 'Тариф']]
const CHAT_TASK = { c1: 't3', c2: 't4', c3: 't5' }
/* Расход, заявки и цена заявки по группам из предложения — для подробного вида. */
const GROUPS = [[4100, 8, 513], [3900, 7, 557], [2900, 7, 414]]
const TONE = { urgent: ['bad', 'Срочно'], today: ['warn', 'Стоит посмотреть'] }
const HEALTH = { bad: 'Тратят впустую', warn: 'Чуть дороже цели', ok: 'Работают хорошо', off: 'Остановлены' }
const allMinus = () => Object.keys(M.reasons).every((k) => S.minus.has(k))
const rejected = `<span class="st st--off">${ic('x', 13)}Отклонено</span>`

function side() {
  const waiting = M.chats.filter((c) => CHAT_TASK[c.id] && !S.decided[CHAT_TASK[c.id]])
  const rest = M.chats.filter((c) => !waiting.includes(c))
  const nav = [['home', 'home', 'Главная'], ['camps', 'megaphone', 'Кампании'], ['queries', 'filter', 'Запросы']]
  const chatRow = (c, wait) => `<button class="srow ${S.screen === 'chat' && c.id === 'c1' ? 'active' : ''}" data-act="go:chat">${wait ? '<i class="pin"></i>' : ''}<span class="t">${c.title}</span></button>`
  return `<aside class="side" data-scroll="side">
    <button class="brand" data-act="go:home"><i>${ic('sparkles', 16)}</i>AiMarketing</button>
    <button class="newchat" data-act="go:home">${ic('plus', 17)}Новый чат</button>
    ${nav.map(([k, i, label]) => `<button class="srow ${S.screen === k ? 'active' : ''}" data-act="go:${k}">${ic(i, 17)}<span class="t">${label}</span>${k === 'queries' && !allMinus() ? `<span class="badge">${M.queryTotals.no}</span>` : ''}</button>`).join('')}
    <div class="pop"><button class="srow" data-act="menu:more">${ic('more', 17)}<span class="t">Ещё</span></button>
      ${S.menu === 'more' ? `<div class="menu">${MORE.map((m) => `<button data-act="go:stub">${ic(m[0], 16)}${m[1]}</button>`).join('')}</div>` : ''}</div>
    <h4>Проекты</h4>
    <button class="srow ${S.project ? '' : 'active'}" data-act="project:">${ic('folder', 16)}<span class="t">Все проекты</span></button>
    ${M.projects.map((p) => `<button class="srow ${S.project === p.id ? 'active' : ''}" data-act="project:${p.id}" title="${STATUS[p.status]}"><span class="dot dot--${p.status}"></span><span class="t">${p.name}</span></button>`).join('')}
    ${waiting.length ? `<h4>Ждут вашего решения · ${waiting.length}</h4>${waiting.map((c) => chatRow(c, true)).join('')}` : ''}
    <h4>Недавние чаты</h4>${rest.map((c) => chatRow(c)).join('')}
    <div class="side__foot" title="Сколько помощника осталось в этом месяце">
      <div class="limit"><span>Лимит ИИ</span><b class="num">${M.plan.used}%</b></div>
      <div class="limit__bar"><i style="width:${M.plan.used}%"></i></div>
      <small>Тариф ${M.plan.name} до ${M.plan.until}</small>
    </div>
  </aside>`
}

function mbar() {
  const p = S.project && project(S.project)
  const title = S.screen === 'chat'
    ? `<b>Почему выросла цена заявки</b><span class="chip"><span class="dot dot--ok"></span>okna-profi.ru</span>`
    : `<span class="chip">${p ? `<span class="dot dot--${p.status}"></span>` : ic('folder', 14)}${projectName(S.project)}</span>`
  return `<header class="mbar"><div class="mbar__title">${title}</div>
    <div class="seg" role="group" aria-label="Вид"><button class="${pro() ? '' : 'active'}" data-act="mode:simple">Просто</button><button class="${pro() ? 'active' : ''}" data-act="mode:pro">Подробно</button></div>
    ${S.screen === 'chat' ? `<button class="iconbtn ${S.panel ? 'on' : ''}" data-act="panel" title="Панель с данными рядом с чатом">${ic('panel')}</button>` : ''}
    <button class="iconbtn" data-act="theme" aria-label="Сменить тему">${ic(S.theme === 'dark' ? 'sun' : 'moon')}</button>
    <span class="avatar">А</span>
  </header>`
}

function composer(big, placeholder) {
  const m = M.models[S.model]
  return `<div class="composer ${big ? 'composer--big' : ''}">
    <textarea rows="${big ? 2 : 1}" placeholder="${placeholder}"></textarea>
    <div class="composer__row">
      <span class="chip">${ic('folder', 14)}${S.screen === 'chat' ? 'okna-profi.ru' : projectName(S.project)}</span>
      <button class="chip chip--btn" data-act="model:next" title="Класс модели: чем больше точек, тем больше лимита тратит ответ">${m[0]}${dots(m[1])}</button>
      <button class="send" data-act="go:chat" aria-label="Отправить">${ic('up')}</button>
    </div>
  </div>`
}

const diffTable = (t) => `<table class="diff num">${t.diff.map((d) => `<tr><td>${d[0]}</td><td><s>${d[1]}</s> → <b>${d[2]}</b></td></tr>`).join('')}</table>`

/* ---------- Главная ---------- */

function signal(t) {
  const st = S.decided[t.id]
  const p = t.project && project(t.project)
  if (st) return `<article class="sig is-closed"><p>${ic('check', 16)}<s>${t.title}</s> · ${st === 'later' ? 'скрыто до завтра' : 'готово'}</p><button class="btn btn--ghost btn--sm" data-act="undo:${t.id}">${ic('undo', 14)}Вернуть</button></article>`
  const [tone, label] = TONE[t.group]
  const act = t.go ? `go:${t.go}` : t.ai ? 'go:chat' : `done:${t.id}`
  return `<article class="sig">
    <div class="sig__top">${badge(tone, label)}<span>${p ? p.name : 'Все проекты'}</span></div>
    <h3>${t.title}</h3>${pro() ? '' : `<p>${t.text}</p>`}
    <div class="sig__acts"><button class="btn ${t.ai ? 'btn--pri' : ''}" data-act="${act}">${t.ai ? ic('sparkles', 15) : ''}${t.primary}${t.external ? ic('external', 14) : ''}</button>
      ${t.ai || t.external ? '' : `<button class="btn btn--ghost" data-act="go:chat" title="Спросить ИИ об этом">${ic('sparkles', 15)}Спросить</button>`}
      <button class="btn btn--ghost btn--icon" data-act="later:${t.id}" title="Скрыть до завтра" aria-label="Скрыть до завтра">${ic('x', 15)}</button></div>
  </article>`
}

function prow(t) {
  const st = S.decided[t.id]
  const p = project(t.project)
  if (st) return `<div class="prow is-closed"><div><small>${p.name}</small><b><s>${t.title}</s></b></div>
    <div class="prow__acts">${st === 'done' ? badge('ok', 'Выполнено') : rejected}<button class="btn btn--ghost btn--sm" data-act="undo:${t.id}">${ic('undo', 14)}Вернуть</button></div></div>`
  return `<div class="prow"><div><small>${p.name}</small><b>${t.title}</b><p>${t.text}</p>${pro() && t.diff ? diffTable(t) : ''}</div>
    <div class="prow__acts"><button class="btn btn--pri" data-act="done:${t.id}">Выполнить</button><button class="btn" data-act="reject:${t.id}">Отклонить</button><button class="btn btn--ghost" data-act="go:chat">Открыть чат</button></div></div>`
}

function home() {
  const tasks = visibleTasks()
  const props = tasks.filter((t) => t.group === 'ai')
  const sigs = tasks.filter((t) => t.group !== 'ai')
  const projs = M.projects.filter((p) => !S.project || p.id === S.project)
  const T = totals()
  const open = (list) => list.filter((t) => !S.decided[t.id]).length
  return `<div class="page" data-scroll="main"><div class="col">
    <p class="eyebrow">${M.date}</p>
    <h1>Что сделать с рекламой?</h1>
    ${composer(true, 'Спросите или поручите: «почему подорожали заявки?», «почисти запросы»…')}
    <div class="starters">${M.starters.map((s) => `<button data-act="go:chat">${s}</button>`).join('')}</div>
    ${pro() ? `<div class="kline card num">
      <div><small>Сегодня</small><b>${rub(T.today)}</b><span>вчера ${rub(T.yesterday)}</span></div>
      <div><small>Расход за 7 дней</small><b>${rub(T.week)}</b><span>+6% к прошлой неделе</span>${spark(M.spendDays.slice(7), 48, 22)}</div>
      <div><small>Конверсии</small><b>${T.conv}</b><span>+8% к прошлой неделе</span>${spark(M.convDays.slice(7), 48, 22)}</div>
      <div><small>CPA</small><b>${rub(T.cpa)}</b><span>−2% к прошлой неделе</span>${spark(M.cpaDays.slice(7), 48, 22)}</div>
    </div>` : ''}
    ${props.length ? `<h2>Ждут вашего решения <span>${open(props)}</span></h2><div class="card">${props.map(prow).join('')}</div>` : ''}
    <h2>Сервис заметил <span>${open(sigs)}</span><small>по цифрам, без ИИ — лимит не тратится</small></h2>
    <div class="sigs">${sigs.map(signal).join('')}</div>
    <h2>Проекты <span>${projs.length}</span></h2>
    <div class="pstrip">${projs.map((p) => `<button class="pc card" data-act="project:${S.project === p.id ? '' : p.id}">
      <b>${p.name}</b>${badge(p.status)}
      <p>${pro() ? kpiText(p) : kpiVerdict(p)}</p>
      ${pro() ? `<div class="pc__row num"><span>Сегодня ${rub(p.today)}<br>7 дней ${rub(p.week)}</span>${spark(p.spark, 48, 22)}</div>` : ''}
    </button>`).join('')}</div>
  </div></div>`
}

/* ---------- Чат и панель с данными ---------- */

function decision() {
  const st = S.decided.t3
  if (!st) return `<button class="btn btn--pri" data-act="done:t3">${ic('check', 16)}Выполнить</button><button class="btn" data-act="reject:t3">Отклонить</button>`
  return `${st === 'done' ? badge('ok', 'Выполнено — ставки изменены в Директе') : rejected}<button class="btn btn--ghost btn--sm" data-act="undo:t3">${ic('undo', 14)}${st === 'done' ? 'Вернуть как было' : 'Вернуть'}</button>`
}

function canvas() {
  const t = M.tasks[2]
  const tabs = [['changes', 'Изменения'], ['chart', 'График'], ['camps', 'Кампании']]
  let body
  if (S.tab === 'chart') {
    body = `<p class="eyebrow">okna-profi.ru · 27 октября — 9 ноября</p><h3>Цена заявки по дням, ₽</h3>
      <figure class="fig">${cpaChart(560, 220)}</figure>
      <ul class="notes"><li><b>3–6 ноября.</b> После повышения ставок цена заявки выросла с 384 до 432 ₽.</li><li><b>7–9 ноября.</b> Немного снизилась, но остаётся выше, чем неделей раньше.</li><li><b>До цели 450 ₽</b> остаётся запас 48 ₽.</li></ul>`
  } else if (S.tab === 'camps') {
    body = `<p class="eyebrow">За 7 дней · цель — заявка до 450 ₽</p><h3>Кампании okna-profi.ru</h3>
      <table class="tbl num"><tr><th>Кампания</th><th class="r">Расход</th><th class="r">Заявки</th><th class="r">Цена заявки</th><th class="opt">По дням</th></tr>
      ${M.campaigns.filter((c) => c.project === 'okna').map((c) => `<tr class="${c.id === 2 ? 'is-change' : ''}"><td><b>${c.name}</b>${c.id === 2 ? '<small>в предложении</small>' : ''}</td><td class="r">${rub(c.cost)}</td><td class="r">${c.conv}</td><td class="r">${campKpi(c)}</td><td class="opt">${bars(c.spark)}</td></tr>`).join('')}</table>`
  } else {
    body = `<p class="eyebrow">Предложение ИИ · okna-profi.ru</p><h3>${t.title}</h3>
      <p class="muted">Кампания «Остекление балконов — Поиск»</p>
      <table class="tbl num"><tr><th>Группа объявлений</th>${pro() ? '<th class="r opt">Расход</th><th class="r opt">Заявки</th><th class="r">Цена заявки</th>' : ''}<th class="r">Ставка сейчас</th><th class="r">Станет</th></tr>
      ${t.diff.map((d, i) => `<tr><td>${d[0]}</td>${pro() ? `<td class="r opt">${rub(GROUPS[i][0])}</td><td class="r opt">${GROUPS[i][1]}</td><td class="r">${rub(GROUPS[i][2])}</td>` : ''}<td class="r"><s>${d[1]}</s></td><td class="r"><b class="hl">${d[2]}</b></td></tr>`).join('')}</table>
      <div class="effect num"><div><small>Расход в неделю</small><b>−1 900 ₽</b></div><div><small>Заявки</small><b>столько же или −1</b></div><div><small>Цена заявки</small><b>402 → около 375 ₽</b></div></div>
      ${pro() ? '<p class="muted">Почему эти группы: расход за неделю вырос на 2 900 ₽, а заявка прибавилась одна. Остальные 9 групп кампании не меняю.</p>' : ''}`
  }
  return `<aside class="canvas">
    <header class="canvas__head"><div class="tabs">${tabs.map(([k, label]) => `<button class="${S.tab === k ? 'active' : ''}" data-act="tab:${k}">${label}</button>`).join('')}</div>
      <button class="iconbtn" data-act="panel" aria-label="Закрыть панель">${ic('x')}</button></header>
    <div class="canvas__body" data-scroll="canvas">${body}</div>
    ${S.tab === 'changes' ? `<footer class="canvas__foot"><div>${decision()}</div><small>Ничего не изменится, пока вы не нажмёте «Выполнить». Вернуть как было можно в «Истории».</small></footer>` : ''}
  </aside>`
}

function chat() {
  const c = M.chat
  const t = M.tasks[2]
  return `<div class="chatwrap ${S.panel ? 'has-canvas' : ''}">
    <section class="thread">
      <div class="thread__scroll" data-scroll="main"><div class="thread__in">
        <div class="msg-user"><div>${c.question}</div></div>
        <div class="msg-ai">
          <p><b>${c.lead}</b></p>
          <ol>${c.points.map((p) => `<li><b>${p[0]}</b> ${p[1]}</li>`).join('')}</ol>
          ${S.panel
            ? `<div class="refs"><button data-act="tab:chart">${ic('chart', 15)}График цены заявки</button><button data-act="tab:camps">${ic('megaphone', 15)}Кампании проекта</button></div>`
            : `<figure class="fig card"><figcaption>Цена заявки по дням, ₽</figcaption>${cpaChart()}</figure>`}
          <p class="good">${ic('check', 16)}<span>${c.good}</span></p>
          <details class="steps" ${pro() ? 'open' : ''}><summary>Что я посмотрел · ${c.steps.length}</summary><ul>${c.steps.map((s) => `<li>${s}</li>`).join('')}</ul></details>
          <div class="ref card ${S.decided.t3 ? 'is-closed' : ''}">
            <div class="ref__head"><span class="tag">${ic('sparkles', 13)}Предложение</span><b>${t.title}</b></div>
            <p>${t.text}</p>
            ${S.panel ? '' : diffTable(t)}
            <div class="ref__acts">${decision()}<button class="btn btn--ghost" data-act="tab:changes">${S.panel ? `Что изменится${ic('right', 15)}` : `${ic('panel', 15)}Открыть рядом`}</button></div>
          </div>
        </div>
      </div></div>
      <div class="thread__foot">${composer(false, 'Уточните или попросите что-то ещё…')}</div>
    </section>
    ${S.panel ? canvas() : ''}
  </div>`
}

/* ---------- Кампании ---------- */

function camps() {
  const list = visibleCampaigns()
  const sel = list.filter((c) => S.sel.has(c.id))
  const check = (c) => `<button class="check ${S.sel.has(c.id) ? 'on' : ''}" data-act="sel:${c.id}" aria-label="Выбрать для вопроса">${ic('check', 13)}</button>`
  const sw = (c) => `<button class="switch ${c.on ? 'on' : ''}" role="switch" aria-checked="${c.on}" aria-label="${c.on ? 'Остановить' : 'Запустить'}" data-act="toggle:${c.id}"></button>`
  const where = (c) => `${c.type}${S.project ? '' : ' · ' + project(c.project).name}`
  const body = pro()
    ? `<div class="card scroll-x"><table class="tbl num">
        <tr><th></th><th>Кампания</th><th>Состояние</th><th class="r">Расход</th><th class="r">Клики</th><th class="r">Конверсии</th><th class="r">CPA / ДРР</th><th>По дням</th><th></th></tr>
        ${list.map((c) => `<tr class="${S.sel.has(c.id) ? 'is-change' : ''}"><td>${check(c)}</td><td><b>${c.name}</b><small>${where(c)}</small></td><td>${badge(c.health)}</td>
          <td class="r">${rub(c.cost)}</td><td class="r">${num(c.clicks)}</td><td class="r">${c.conv}</td><td class="r"><b>${campKpi(c)}</b></td><td>${bars(c.spark)}</td><td>${sw(c)}</td></tr>`).join('')}
      </table></div>`
    : Object.entries(HEALTH).map(([h, label]) => {
        const rows = list.filter((c) => c.health === h)
        if (!rows.length) return ''
        return `<h2>${badge(h, label)}<span>${rows.length}</span></h2><div class="card">${rows.map((c) => `<div class="crow ${S.sel.has(c.id) ? 'is-sel' : ''}">${check(c)}
          <div><b>${c.name}</b><small>${where(c)}</small></div>
          <div class="crow__health">${campHealth(c)}</div>
          <div class="crow__num num"><b>${rub(c.cost)}</b><small>${c.conv} ${c.drr != null ? plural(c.conv, 'заказ', 'заказа', 'заказов') : plural(c.conv, 'заявка', 'заявки', 'заявок')}</small></div>
          ${sw(c)}</div>`).join('')}</div>`
      }).join('')
  const prompts = sel.length ? ['Почему у них дорогая заявка?', 'Что из этого отключить?', 'Сравни их между собой'] : ['Какие тратят впустую?', 'Что отключить?', 'Куда добавить бюджет?']
  return `<div class="page" data-scroll="main"><div class="wide">
      <div class="head"><div><p class="eyebrow">${projectName(S.project)} · за 7 дней</p><h1>Кампании</h1></div><button class="btn btn--pri" data-act="go:chat">${ic('sparkles', 16)}Собрать новую с ИИ</button></div>
      ${body}
    </div></div>
    <div class="askbar"><div class="askbar__in">
      <div class="askbar__ctx">${ic('sparkles', 15)}${sel.length
        ? `<b>Выбрано ${sel.length}:</b>${sel.map((c) => `<span class="chip">${c.name}<button data-act="sel:${c.id}" aria-label="Убрать">${ic('x', 12)}</button></span>`).join('')}`
        : `<span>Вопрос про <b>все ${list.length} ${plural(list.length, 'кампанию', 'кампании', 'кампаний')}</b> — или отметьте нужные галочкой</span>`}</div>
      <div class="askbar__box"><input placeholder="Спросите про эти кампании…"><button class="send" data-act="go:chat" aria-label="Спросить">${ic('up')}</button></div>
      <div class="starters">${prompts.map((s) => `<button data-act="go:chat">${s}</button>`).join('')}</div>
    </div></div>`
}

/* ---------- Запросы ---------- */

function queries() {
  const t = M.queryTotals
  const keys = Object.keys(M.reasons)
  const left = keys.filter((k) => !S.minus.has(k))
  const leftCount = left.reduce((s, k) => s + M.reasons[k].count, 0)
  const leftCost = left.reduce((s, k) => s + M.reasons[k].cost, 0)
  const words = (n) => plural(n, 'запрос', 'запроса', 'запросов')
  const pick = (q) => `<div class="pick"><button class="yes ${verdictOf(q) === 'yes' ? 'on' : ''}" data-act="verdict:${q.id}=yes">Наш</button><button class="no ${verdictOf(q) === 'no' ? 'on' : ''}" data-act="verdict:${q.id}=no">Не наш</button></div>`
  const stats = (q) => `${q.clicks} ${plural(q.clicks, 'клик', 'клика', 'кликов')} · ${rub(q.cost)}${q.conv ? ` · ${q.conv} ${plural(q.conv, 'заявка', 'заявки', 'заявок')}` : ''}`
  const groupAct = (k) => (S.minus.has(k)
    ? `${badge('ok', 'В минус-фразах')}<button class="btn btn--ghost btn--sm" data-act="minus:${k}">${ic('undo', 14)}Вернуть</button>`
    : `<button class="btn btn--sm" data-act="minus:${k}">Минусовать группу</button>`)
  const row = (q) => `<tr><td>${q.q}${q.why ? `<small>${q.why}</small>` : ''}</td><td class="r">${q.clicks}</td><td class="r">${rub(q.cost)}</td><td class="r">${q.conv || '—'}</td><td class="r">${pick(q)}</td></tr>`
  const body = pro()
    ? `<div class="card scroll-x"><table class="tbl num"><tr><th>Запрос</th><th class="r">Клики</th><th class="r">Расход</th><th class="r">Заявки</th><th class="r">Решение</th></tr>
        ${keys.map((k) => `<tr class="grp"><td colspan="4"><b>${M.reasons[k].label}</b> · ${M.reasons[k].count} ${words(M.reasons[k].count)} · ${rub(M.reasons[k].cost)}</td><td class="r"><div class="grp__act">${groupAct(k)}</div></td></tr>${M.queries.filter((q) => q.r === k).map(row).join('')}`).join('')}
        <tr class="grp"><td colspan="5"><b>ИИ не уверен</b> · ${t.maybe} ${words(t.maybe)}</td></tr>${M.queries.filter((q) => q.v === 'maybe').map(row).join('')}
        <tr class="grp"><td colspan="5"><b>Полезные</b> · ${t.yes} ${words(t.yes)}</td></tr>${M.queries.filter((q) => q.v === 'yes').map(row).join('')}
      </table></div>`
    : `<div class="rgrid">${keys.map((k) => {
        const r = M.reasons[k]
        const qs = M.queries.filter((q) => q.r === k)
        return `<article class="rcard card ${S.minus.has(k) ? 'is-done' : ''}"><header><h3>${r.label}</h3><span class="num">${r.count} ${words(r.count)} · ${rub(r.cost)}</span></header>
          <ul>${qs.map((q) => `<li>${q.q}<span class="num">${rub(q.cost)}</span></li>`).join('')}${r.count > qs.length ? `<li class="muted">и ещё ${r.count - qs.length}</li>` : ''}</ul>
          <footer>${groupAct(k)}${S.minus.has(k) ? '' : `<button class="btn btn--ghost btn--sm" data-act="go:chat">Почему это мусор?</button>`}</footer></article>`
      }).join('')}</div>
      <h2>ИИ не уверен — решите сами <span>${t.maybe}</span></h2>
      <div class="card">${M.queries.filter((q) => q.v === 'maybe').map((q) => `<div class="qrow"><div><b>${q.q}</b><small>${q.why}</small></div><span class="num muted">${stats(q)}</span>${pick(q)}</div>`).join('')}</div>
      <div class="okrow card">${badge('ok', `${t.yes} полезных ${words(t.yes)}`)}<span>С ними всё в порядке, делать ничего не нужно.</span><button class="btn btn--ghost btn--sm">Показать${ic('down', 15)}</button></div>`
  return `<div class="page" data-scroll="main"><div class="wide">
    <div class="head"><div><p class="eyebrow">okna-profi.ru · новые запросы за 7 дней</p><h1>Чистка запросов</h1></div></div>
    <div class="summary card">${ic('sparkles', 20)}<p>${left.length
      ? `ИИ просмотрел <b>${t.all} ${words(t.all)}</b> и разложил мусорные по причинам. Осталось разобрать <b>${leftCount} ${words(leftCount)} на ${rub(leftCost)}</b> за неделю.`
      : `Готово: ${t.no} мусорных ${words(t.no)} добавлены в минус-фразы. Экономия — около <b>${rub(t.noCost)} в неделю</b>.`}</p>
      ${left.length ? `<button class="btn btn--pri" data-act="minusall">Минусовать ${left.length === keys.length ? 'все ' : ''}${left.length} ${plural(left.length, 'группу', 'группы', 'групп')}</button>` : `<button class="btn" data-act="minusnone">${ic('undo', 15)}Вернуть</button>`}</div>
    ${body}
  </div></div>`
}

const stub = () => `<div class="page"><div class="col"><h1>Этот раздел в макете не нарисован</h1><p class="eyebrow" style="margin-top:10px">Аналитика, Автопилот, История и служебные разделы лежат в «Ещё» и будут оформлены так же.</p></div></div>`
const SCREENS = { home, chat, camps, queries }

return {
  name: 'Диалог',
  theme: 'light',
  render: () => `<div class="shell">${side()}<div class="main">${mbar()}${(SCREENS[S.screen] || stub)()}</div></div>`,
  actions: {
    minusall: () => Object.keys(M.reasons).forEach((k) => S.minus.add(k)),
    minusnone: () => S.minus.clear(),
  },
}
