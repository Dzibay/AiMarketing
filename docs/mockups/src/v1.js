const NAV = [['home', 'Сегодня'], ['chat', 'Помощник'], ['camps', 'Кампании'], ['queries', 'Запросы']]
const MORE = [['chart', 'Аналитика'], ['zap', 'Автопилот'], ['history', 'История'], null, ['plug', 'Подключения'], ['sliders', 'Настройки'], ['card', 'Тариф']]

function topbar() {
  const p = S.project && project(S.project)
  const todo = openTasks().length
  return `<header class="top">
    <button class="brand" data-act="go:home"><i>${ic('sparkles', 17)}</i><span>AiMarketing</span></button>
    <div class="pop">
      <button class="picker" data-act="menu:project"><span class="dot dot--${p ? p.status : 'ok'}"></span>${projectName(S.project)}${ic('down', 16)}</button>
      ${S.menu === 'project' ? `<div class="menu">
        <button class="${S.project ? '' : 'active'}" data-act="project:">${ic('folder', 16)}Все проекты</button>
        ${['Окна Профи', 'Стоматология Улыбка', null].map((c) => `<small>${c || 'Без клиента'}</small>${M.projects.filter((x) => x.client === c).map((x) => `<button class="${S.project === x.id ? 'active' : ''}" data-act="project:${x.id}"><span class="dot dot--${x.status}"></span>${x.name}</button>`).join('')}`).join('')}
      </div>` : ''}
    </div>
    <nav class="nav">
      ${NAV.map(([k, label]) => `<button class="${S.screen === k ? 'active' : ''}" data-act="go:${k}">${label}${k === 'home' && todo ? `<span class="count">${todo}</span>` : ''}</button>`).join('')}
      <div class="pop"><button data-act="menu:more">Ещё${ic('down', 16)}</button>
        ${S.menu === 'more' ? `<div class="menu">${MORE.map((m) => (m ? `<button data-act="go:stub">${ic(m[0], 16)}${m[1]}</button>` : '<hr>')).join('')}</div>` : ''}
      </div>
    </nav>
    <div class="tools">
      <div class="limit" title="Использовано ${M.plan.used}% лимита · тариф ${M.plan.name} до ${M.plan.until}"><div class="limit__bar"><i style="width:${M.plan.used}%"></i></div><span>Лимит ${M.plan.used}%</span></div>
      <div class="seg" role="group" aria-label="Вид"><button class="${pro() ? '' : 'active'}" data-act="mode:simple">Просто</button><button class="${pro() ? 'active' : ''}" data-act="mode:pro">Подробно</button></div>
      <button class="iconbtn" data-act="theme" aria-label="Сменить тему">${ic(S.theme === 'dark' ? 'sun' : 'moon')}</button>
      <span class="avatar">А</span>
    </div>
  </header>`
}

/* ---------- Сегодня ---------- */

function task(t) {
  const st = S.decided[t.id]
  const p = t.project && project(t.project)
  const meta = `<div class="task__meta">${p ? `<span class="dot dot--${p.status}"></span>${p.name}` : 'Все проекты'}</div>`
  if (st) {
    const text = st === 'rejected' ? 'Отклонено' : t.group === 'ai' ? 'Выполнено — изменения применены в Директе' : 'Готово'
    return `<article class="task card is-closed"><span class="task__ic">${ic(st === 'rejected' ? 'x' : 'check')}</span><div>${meta}<p><s>${t.title}</s> · ${text}</p></div><button class="btn btn--quiet btn--sm" data-act="undo:${t.id}">${ic('undo', 15)}Вернуть</button></article>`
  }
  const ai = t.group === 'ai'
  const second = t.secondary ? `<button class="btn btn--quiet" data-act="go:${t.secondary === 'Открыть кампании' ? 'camps' : 'chat'}">${t.secondary}</button>` : ''
  const primary = t.go ? `data-act="go:${t.go}"` : t.ai ? 'data-act="go:chat"' : `data-act="done:${t.id}"`
  return `<article class="task task--${t.group} card">
    <span class="task__ic">${ic(t.icon, 20)}</span>
    <div>${meta}<h3>${ai && !pro() ? 'ИИ предлагает: ' + t.title[0].toLowerCase() + t.title.slice(1) : t.title}</h3><p>${t.text}</p>
      ${pro() && t.diff ? `<table class="diff num">${t.diff.map((d) => `<tr><td>${d[0]}</td><td><s>${d[1]}</s> → <b>${d[2]}</b></td></tr>`).join('')}</table>` : ''}</div>
    <div class="task__acts">${second}${ai ? `<button class="btn btn--quiet" data-act="reject:${t.id}">Отклонить</button>` : `<button class="btn btn--quiet" data-act="later:${t.id}">Позже</button>`}
      <button class="btn btn--primary" ${primary}>${t.ai ? ic('sparkles', 16) : ''}${t.primary}${t.external ? ic('external', 15) : ''}</button></div>
  </article>`
}

function home() {
  const one = S.project && project(S.project)
  const tasks = visibleTasks().filter((t) => S.decided[t.id] !== 'later')
  const open = openTasks()
  const urgent = open.filter((t) => t.group === 'urgent').length
  const projs = M.projects.filter((p) => !S.project || p.id === S.project)
  const okCount = M.projects.filter((p) => p.status === 'ok').length
  const T = one ? { today: one.today, yesterday: one.yesterday, week: one.week, conv: one.conv, cpa: one.kpi === 'CPA' ? one.value : Math.round(one.week / one.conv) } : M.totals
  const title = open.length ? `${open.length} ${plural(open.length, 'дело', 'дела', 'дел')} на сегодня${urgent ? `, <em>${urgent} ${plural(urgent, 'срочное', 'срочных', 'срочных')}</em>` : ''}` : 'На сегодня всё сделано'
  const figs = pro()
    ? `<div class="tiles">
        <div class="tile card"><small>Сегодня</small><b class="num">${rub(T.today)}</b><div class="tile__foot">расход на 14:20</div></div>
        <div class="tile card"><small>Вчера</small><b class="num">${rub(T.yesterday)}</b><div class="tile__foot">за весь день</div></div>
        <div class="tile card"><small>Расход за 7 дней</small><b class="num">${rub(T.week)}</b><div class="tile__foot"><span>+6% к прошлой</span>${spark(M.spendDays.slice(7), 72, 26)}</div></div>
        <div class="tile card"><small>Конверсии за 7 дней</small><b class="num">${T.conv}</b><div class="tile__foot"><span class="tone-ok">▲ 8%</span>${spark(M.convDays.slice(7), 72, 26)}</div></div>
        <div class="tile card"><small>CPA за 7 дней</small><b class="num">${rub(T.cpa)}</b><div class="tile__foot"><span class="tone-ok">▼ 2%</span>${spark(M.cpaDays.slice(7), 72, 26)}</div></div>
      </div>`
    : ''
  const side = pro()
    ? `<h2>Проекты <span>${projs.length}</span></h2><div class="card scroll-x"><table class="tbl num">
        <tr><th>Проект</th><th class="r">Сегодня</th><th class="r">Вчера</th><th class="r">7 дней</th><th class="r">Конверсии</th><th>KPI за 7 дней</th><th class="r">Темп бюджета</th><th class="r">Баланс</th><th></th></tr>
        ${projs.map((p) => `<tr class="is-link" data-act="project:${p.id}"><td><b><span class="dot dot--${p.status}"></span> ${p.name}</b><small>${p.client || 'Без клиента'} · ${p.camps} ${plural(p.camps, 'кампания', 'кампании', 'кампаний')}</small></td>
          <td class="r">${rub(p.today)}</td><td class="r">${rub(p.yesterday)}</td><td class="r">${rub(p.week)}</td><td class="r">${p.conv}</td>
          <td><b>${p.kpi} ${p.kpi === 'CPA' ? rub(p.value) : p.value + '%'}</b> ${badge(p.status)}<small>цель ${p.kpi === 'CPA' ? rub(p.target) : p.target + '%'}</small></td>
          <td class="r">${p.pace}%<small>${p.pace > 105 ? 'перерасход' : p.pace < 92 ? 'недорасход' : 'по плану'}</small></td>
          <td class="r">${rub(p.balance)}<small class="${p.balanceDays <= 3 ? 'tone-bad' : ''}">≈ на ${p.balanceDays} дн.</small></td><td class="muted">${spark(p.spark, 72, 24)}</td></tr>`).join('')}
      </table></div>`
    : `<h2>Проекты <span>${projs.length}</span></h2>${projs.map((p) => `<button class="pcard card" data-act="project:${S.project === p.id ? '' : p.id}">
        <div class="pcard__top"><span class="dot dot--${p.status}"></span><strong>${p.name}</strong><small>${p.client || ''}</small></div>
        <div class="pcard__verdict tone-${p.status}">${kpiVerdict(p)}</div>
        <div class="pcard__row"><span>Сегодня <b class="num">${rub(p.today)}</b><br>${kpiText(p)}</span>${spark(p.spark, 84, 28)}</div>
      </button>`).join('')}`
  return `<div class="hero">
      <div><p class="eyebrow">${M.date} · ${projectName(S.project)}</p><h1>${title}</h1>
        <p class="lead">${one ? kpiVerdict(one) + '. ' + kpiText(one) + '.' : `В остальном порядок: ${okCount} проекта из ${M.projects.length} укладываются в цель.`}</p></div>
      ${pro() ? '' : `<div class="figs card num">
        <div class="fig"><small>Потрачено сегодня</small><b>${rub(T.today)}</b><span>вчера ${rub(T.yesterday)}</span></div>
        <div class="fig"><small>Заявок за 7 дней</small><b>${T.conv}</b><span class="tone-ok">на 8% больше</span></div>
        <div class="fig"><small>Одна заявка стоит</small><b>${rub(T.cpa)}</b><span class="tone-ok">на 2% дешевле</span></div>
      </div>`}
    </div>
    ${figs}
    <div class="cols">
      <section>${Object.entries(M.groups).map(([g, label]) => {
        const list = tasks.filter((t) => t.group === g)
        return list.length ? `<h2>${g === 'urgent' ? '<span class="dot dot--bad"></span>' : ''}${label} <span>${list.filter((t) => !S.decided[t.id]).length}</span></h2>${list.map(task).join('')}` : ''
      }).join('')}</section>
      <aside>${side}</aside>
    </div>`
}

/* ---------- Помощник ---------- */

function proposal(t) {
  const st = S.decided[t.id]
  if (st) return `<div class="prop card is-closed"><div class="prop__head"><span class="tag">${ic(st === 'done' ? 'check' : 'x', 14)}${st === 'done' ? 'Выполнено' : 'Отклонено'}</span><strong>${t.title}</strong></div>
    <div class="prop__acts" style="margin-top:12px"><button class="btn btn--sm" data-act="undo:${t.id}">${ic('undo', 15)}${st === 'done' ? 'Вернуть как было' : 'Вернуть предложение'}</button></div></div>`
  return `<div class="prop card">
    <div class="prop__head"><span class="tag">${ic('sparkles', 14)}Предложение</span><strong>${t.title}</strong></div>
    <table class="diff num">${t.diff.map((d) => `<tr><td>${d[0]}</td><td><s>${d[1]}</s> → <b>${d[2]}</b></td></tr>`).join('')}</table>
    <p class="prop__effect">Что изменится: ${t.text[0].toLowerCase() + t.text.slice(1)}</p>
    <div class="prop__acts"><button class="btn btn--primary" data-act="done:${t.id}">${ic('check', 16)}Выполнить</button><button class="btn" data-act="reject:${t.id}">Отклонить</button>${pro() ? '<button class="btn btn--quiet">Изменить ставки вручную</button>' : ''}
      <small>Ничего не изменится, пока вы не нажмёте «Выполнить». Вернуть как было можно в «Истории».</small></div>
  </div>`
}

function chat() {
  const c = M.chat
  return `<div class="chatpage">
    <aside class="chats"><button class="btn btn--primary">${ic('plus', 16)}Новый разговор</button>
      ${M.chats.map((x, i) => `<button class="chatlink ${i ? '' : 'active'}"><strong>${x.title}${x.pending && !(i === 0 && S.decided.t3) ? '<span class="count">1</span>' : ''}</strong><small>${x.project ? project(x.project).name : 'Все проекты'} · ${x.when}</small></button>`).join('')}
    </aside>
    <section class="talk">
      <div class="talk__head"><div><p class="eyebrow">okna-profi.ru · ИИ видит кампании, запросы и статистику проекта</p><h1>Почему выросла цена заявки</h1></div><button class="iconbtn" title="Переименовать, права ИИ, удалить">${ic('more')}</button></div>
      <div class="msg--user"><div>${c.question}</div></div>
      <div class="msg--ai"><span class="msg__av">${ic('sparkles')}</span><div class="msg__body">
        <p><b>${c.lead}</b></p>
        <ol>${c.points.map((p) => `<li><b>${p[0]}</b> <span>${p[1]}</span></li>`).join('')}</ol>
        <figure class="chartcard card"><figcaption>Цена заявки по дням, ₽</figcaption>${cpaChart()}</figure>
        <p class="good">${ic('check', 16)}<span>${c.good}</span></p>
        <details class="steps" ${pro() ? 'open' : ''}><summary>Что я посмотрел · ${c.steps.length}</summary><ul>${c.steps.map((s) => `<li>${s}</li>`).join('')}</ul></details>
        ${proposal(M.tasks[2])}
      </div></div>
      <div class="composer">
        <div class="starters">${M.starters.map((s) => `<button>${s}</button>`).join('')}</div>
        <div class="composer__box card"><textarea rows="1" placeholder="Спросите про рекламу или попросите что-то сделать…"></textarea>
          <div class="composer__row"><div class="models" title="Точки — сколько лимита тратит ответ">${M.models.map((m, i) => `<button class="${(S.model ?? 1) === i ? 'active' : ''}" data-act="model:${i}">${m[0]}${dots(m[1])}</button>`).join('')}</div><button class="send" aria-label="Отправить">${ic('up')}</button></div>
        </div>
      </div>
    </section>
  </div>`
}

/* ---------- Кампании ---------- */

const HEALTH = { bad: 'Тратят впустую', warn: 'Чуть дороже цели', ok: 'Работают хорошо', off: 'Остановлены' }

function camps() {
  const list = visibleCampaigns()
  const head = `<div class="head"><div><p class="eyebrow">${projectName(S.project)} · за 7 дней</p><h1>Кампании</h1></div>
    <div class="head__tools"><label class="search">${ic('search', 16)}<input placeholder="Найти кампанию"></label><button class="btn btn--primary">${ic('plus', 16)}Новая кампания</button></div></div>`
  if (!pro()) {
    return head + Object.entries(HEALTH).map(([h, label]) => {
      const rows = list.filter((c) => c.health === h)
      if (!rows.length) return ''
      return `<h2><span class="dot dot--${h}"></span>${label} <span>${rows.length}</span></h2><div class="card">${rows.map((c) => `<div class="camp">
        <button class="switch ${c.on ? 'on' : ''}" role="switch" aria-checked="${c.on}" aria-label="${c.on ? 'Остановить' : 'Запустить'}" data-act="toggle:${c.id}"></button>
        <div><strong>${c.name}</strong><small>${c.type}${S.project ? '' : ' · ' + project(c.project).name}</small></div>
        <div class="camp__health tone-${h}">${campHealth(c)}</div>
        <div class="camp__num num"><b>${rub(c.cost)}</b><small>${c.conv} ${c.drr != null ? plural(c.conv, 'заказ', 'заказа', 'заказов') : plural(c.conv, 'заявка', 'заявки', 'заявок')}</small></div>
        <button class="btn btn--quiet btn--sm" data-act="go:chat">${ic('sparkles', 15)}Спросить ИИ</button>
      </div>`).join('')}</div>`
    }).join('')
  }
  return `${head}
    <div class="filters"><button class="active">Все ${list.length}</button><button>Идут ${list.filter((c) => c.on).length}</button><button>Остановлены ${list.filter((c) => !c.on).length}</button><button>С проблемами ${list.filter((c) => c.health === 'bad' || c.health === 'warn').length}</button><span class="chip" style="margin-left:auto">Период: 7 дней ${ic('down', 13)}</span></div>
    <div class="card scroll-x"><table class="tbl num">
      <tr><th></th><th>Кампания</th><th>Статус</th><th class="r">Расход</th><th class="r">Клики</th><th class="r">Конверсии</th><th class="r">CPA / ДРР</th><th>По дням</th></tr>
      ${list.map((c) => `<tr class="${S.sel.has(c.id) ? 'is-sel' : ''}">
        <td><button class="check ${S.sel.has(c.id) ? 'on' : ''}" data-act="sel:${c.id}" aria-label="Выбрать">${ic('check', 14)}</button></td>
        <td><b>${c.name}</b><small>${c.type}${S.project ? '' : ' · ' + project(c.project).name}</small></td>
        <td><div style="display:flex;align-items:center;gap:10px"><button class="switch ${c.on ? 'on' : ''}" role="switch" aria-checked="${c.on}" data-act="toggle:${c.id}"></button>${badge(c.health)}</div></td>
        <td class="r">${rub(c.cost)}</td><td class="r">${num(c.clicks)}</td><td class="r">${c.conv}</td>
        <td class="r"><b class="tone-${c.health === 'off' ? 'off' : c.health}">${campKpi(c)}</b></td><td>${bars(c.spark)}</td></tr>`).join('')}
    </table></div>
    ${S.sel.size ? `<div class="bulk"><span>Выбрано: ${S.sel.size}</span><button class="btn btn--sm">${ic('pause', 14)}Пауза</button><button class="btn btn--sm">${ic('play', 14)}Запуск</button><button class="btn btn--sm">Бюджет</button><button class="btn btn--sm">${ic('copy', 14)}Копия</button><button class="btn btn--sm btn--primary" data-act="go:chat">${ic('sparkles', 14)}Спросить ИИ</button><button class="btn btn--sm" data-act="selnone" aria-label="Снять выбор">${ic('x', 14)}</button></div>` : ''}`
}

/* ---------- Запросы ---------- */


function queries() {
  const t = M.queryTotals
  const done = S.decided.minus === 'done'
  const head = `<div class="head"><div><p class="eyebrow">okna-profi.ru · новые запросы за 7 дней</p><h1>Чистка запросов</h1>
    <p class="lead">ИИ разметил ${t.all} запросов, по которым показывалась реклама. Вам остаётся подтвердить.</p></div></div>`
  if (!pro()) {
    const kept = M.queries.filter((q) => q.v === 'no' && S.verdicts[q.id] === 'yes').length
    return `${head}
    <div class="qblock card">
      <div class="qblock__head"><div><h3><span class="dot dot--bad"></span>Мусор — ${t.no - kept} ${plural(t.no - kept, 'запрос', 'запроса', 'запросов')} · ${rub(t.noCost)} за неделю</h3>
        <p>Люди искали не то, что вы продаёте. Добавьте эти запросы в минус-фразы — реклама перестанет по ним показываться.</p></div>
        ${done ? `<button class="btn" data-act="undo:minus">${ic('undo', 15)}Вернуть</button>` : `<button class="btn btn--primary" data-act="done:minus">${ic('check', 16)}Убрать все ${t.no - kept}</button>`}</div>
      ${done ? `<p class="good" style="margin-top:14px">${ic('check', 16)}<span>Готово: ${t.no - kept} минус-фраз добавлены. Экономия — около ${rub(t.noCost)} в неделю.</span></p>` : Object.entries(M.reasons).map(([k, r]) => {
        const qs = M.queries.filter((q) => q.r === k)
        return `<div class="reason"><small>${r.label} · ${r.count} · ${rub(r.cost)}</small><div class="phrases">${qs.map((q) => `<span class="phrase ${S.verdicts[q.id] === 'yes' ? 'is-kept' : ''}">${q.q}<button title="Это наш запрос — оставить" data-act="verdict:${q.id}=yes">${ic(S.verdicts[q.id] === 'yes' ? 'undo' : 'x', 14)}</button></span>`).join('')}${r.count > qs.length ? `<span class="chip" style="height:32px">ещё ${r.count - qs.length}</span>` : ''}</div></div>`
      }).join('')}
    </div>
    <div class="qblock card">
      <h3><span class="dot dot--warn"></span>Сомнительные — ${t.maybe}</h3><p>ИИ не уверен. Решите сами — он запомнит ваш выбор для похожих запросов.</p>
      <div style="margin-top:10px">${M.queries.filter((q) => q.v === 'maybe').map((q) => `<div class="doubt"><div><b>${q.q}</b><small>${q.why}</small></div>
        <span class="num">${q.clicks} ${plural(q.clicks, 'клик', 'клика', 'кликов')} · ${rub(q.cost)}${q.conv ? ' · 1 заявка' : ''}</span>
        <div class="pick"><button class="yes ${S.verdicts[q.id] === 'yes' ? 'on' : ''}" data-act="verdict:${q.id}=yes">Наш</button><button class="no ${S.verdicts[q.id] === 'no' ? 'on' : ''}" data-act="verdict:${q.id}=no">Не наш</button></div></div>`).join('')}</div>
    </div>
    <div class="qblock card"><div class="qblock__head"><div><h3><span class="dot dot--ok"></span>Полезные — ${t.yes}</h3><p>С ними всё в порядке, делать ничего не нужно.</p></div><button class="btn btn--quiet">Показать${ic('down', 15)}</button></div></div>`
  }
  const f = S.qfilter || 'all'
  const list = M.queries.filter((q) => f === 'all' || verdictOf(q) === f)
  const V = { no: ['bad', 'Мусор'], maybe: ['warn', 'Сомнительный'], yes: ['ok', 'Полезный'] }
  const toMinus = t.no + M.queries.filter((q) => q.v !== 'no' && S.verdicts[q.id] === 'no').length - M.queries.filter((q) => q.v === 'no' && S.verdicts[q.id] === 'yes').length
  return `${head}
    <div class="filters">${[['all', 'Все', t.all], ['no', 'Мусор', t.no], ['maybe', 'Сомнительные', t.maybe], ['yes', 'Полезные', t.yes]].map(([k, l, n]) => `<button class="${f === k ? 'active' : ''}" data-act="qfilter:${k}">${l} ${n}</button>`).join('')}
      <label class="search" style="margin-left:auto;height:34px">${ic('search', 15)}<input placeholder="Слово в запросе"></label><span class="chip" style="height:34px">Куда: список «Общий» ${ic('down', 13)}</span></div>
    <div class="card scroll-x"><table class="tbl num">
      <tr><th>Запрос</th><th>Разметка ИИ</th><th class="r">Клики</th><th class="r">Расход</th><th class="r">Конверсии</th><th class="r">Ваше решение</th></tr>
      ${list.map((q) => { const v = verdictOf(q); return `<tr><td><b>${q.q}</b></td><td><span class="tone-${V[q.v][0]}"><span class="dot dot--${V[q.v][0]}"></span> ${V[q.v][1]}</span><small>${q.r ? M.reasons[q.r].label : q.why || 'Есть заявки'}</small></td>
        <td class="r">${q.clicks}</td><td class="r">${rub(q.cost)}</td><td class="r">${q.conv || '—'}</td>
        <td class="r"><div class="pick" style="justify-content:flex-end"><button class="yes ${v === 'yes' ? 'on' : ''}" data-act="verdict:${q.id}=yes">Наш</button><button class="no ${v === 'no' ? 'on' : ''}" data-act="verdict:${q.id}=no">Не наш</button></div></td></tr>` }).join('')}
    </table></div>
    <div class="bulk"><span>${done ? 'Минус-фразы добавлены' : `В минус-фразы: ${toMinus} ${plural(toMinus, 'запрос', 'запроса', 'запросов')} · ${rub(t.noCost)} за неделю`}</span>${done ? `<button class="btn btn--sm" data-act="undo:minus">${ic('undo', 14)}Вернуть</button>` : `<button class="btn btn--sm btn--primary" data-act="done:minus">${ic('check', 14)}Добавить</button>`}</div>`
}

const SCREENS = { home, chat, camps, queries, stub: () => `<div class="stub card"><h1 style="font-size:22px">Этот раздел в макете не нарисован</h1><p class="lead">Аналитика, Автопилот, История и служебные разделы переедут в «Ещё» и будут оформлены так же.</p></div>` }

return { name: 'Сегодня', theme: 'light', render: () => topbar() + `<main class="wrap">${(SCREENS[S.screen] || home)()}</main>` }
