/* Запросы: чистка поисковых запросов. Мусор ИИ раскладывает по причинам, человек решает группами. */

/* В каком состоянии разбор у каждого проекта: размечен, новые запросы, пусто, ждёт решения. */
const QSTATE = { okna: 'marked', msk: 'new', dent: 'empty', kofe: 'pending' }
const QWHY = {
  job: 'Люди ищут работу или отзывы сотрудников — покупать окна они не собираются.',
  diy: 'Ищут инструкции и чертежи, чтобы сделать самим. Заявок по таким запросам не было ни разу.',
  brand: 'Ищут сайты конкретных производителей и уходят к ним, а клик оплачиваете вы.',
  free: 'Ищут даром или с рук. Ваши цены им не подойдут — заявок нет.',
}
/* Мусор, который ИИ найдёт в msk.okna-profi.ru после разметки: запрос, клики, расход. */
const QMSK = [['пластиковые окна бу', 9, 210], ['окна вакансии подольск', 6, 150], ['как снять створку окна', 11, 190], ['окна калева личный кабинет', 5, 130], ['чертёж окна пвх', 4, 90]]
const QPERIODS = ['За 7 дней', 'За 30 дней']
const words = (n) => plural(n, 'запрос', 'запроса', 'запросов')
const qCamp = (q) => M.campaigns[+q.id.slice(1) % 2]
const pick = (q) => `<div class="pick"><button class="yes ${verdictOf(q) === 'yes' ? 'on' : ''}" data-act="verdict:${q.id}=yes">Наш</button><button class="no ${verdictOf(q) === 'no' ? 'on' : ''}" data-act="verdict:${q.id}=no">Не наш</button></div>`
const groupAct = (k) => (S.minus.has(k)
  ? `${badge('ok', 'В минус-фразах')}<button class="btn btn--ghost btn--sm" data-act="minus:${k}">${ic('undo', 14)}Вернуть</button>`
  : `<button class="btn btn--sm" data-act="minus:${k}">Минусовать группу</button>`)

/** Запросы под фильтры экспертного режима, в порядке показа. */
function queryRows() {
  const text = val('qq', '').trim().toLowerCase()
  const min = parseInt(String(val('qmin', '')).replace(/\D/g, ''), 10) || 0
  const camp = val('qcamp', 'Все кампании')
  const ok = (q) => (!text || q.q.includes(text)) && q.cost >= min && (!on('qnoconv') || !q.conv) && (camp === 'Все кампании' || qCamp(q).name === camp)
  const order = (q) => (q.r ? Object.keys(M.reasons).indexOf(q.r) : q.v === 'maybe' ? 10 : 11)
  return M.queries.filter(ok).sort((a, b) => order(a) - order(b))
}

/* ---------- разбор ---------- */

function triageSummary() {
  const t = M.queryTotals
  const keys = Object.keys(M.reasons)
  const left = keys.filter((k) => !S.minus.has(k))
  const leftCount = sum(left.map((k) => M.reasons[k].count))
  const leftCost = sum(left.map((k) => M.reasons[k].cost))
  return `<div class="summary card">${ic('sparkles', 20)}<p>${left.length
    ? `ИИ просмотрел <b>${t.all} ${words(t.all)}</b> и разложил мусорные по причинам. Осталось разобрать <b>${leftCount} ${words(leftCount)} на ${rub(leftCost)}</b> за неделю.`
    : `Готово: ${t.no} мусорных ${words(t.no)} добавлены в минус-фразы. Экономия — около <b>${rub(t.noCost)} в неделю</b>.`}</p>
    ${left.length ? `<button class="btn btn--pri" data-act="minusall">Минусовать ${left.length === keys.length ? 'все ' : ''}${left.length} ${plural(left.length, 'группу', 'группы', 'групп')}</button>` : `<button class="btn" data-act="minusnone">${ic('undo', 15)}Вернуть</button>`}</div>`
}

function triageSimple() {
  const t = M.queryTotals
  const stats = (q) => `${q.clicks} ${plural(q.clicks, 'клик', 'клика', 'кликов')} · ${rub(q.cost)}${q.conv ? ` · ${q.conv} ${plural(q.conv, 'заявка', 'заявки', 'заявок')}` : ''}`
  return `${triageSummary()}<div class="rgrid">${Object.keys(M.reasons).map((k) => {
      const r = M.reasons[k]
      const qs = M.queries.filter((q) => q.r === k)
      const why = val('qwhy', '') === k
      return `<article class="rcard card ${S.minus.has(k) ? 'is-done' : ''}"><header><h3>${r.label}</h3><span class="num">${r.count} ${words(r.count)} · ${rub(r.cost)}</span></header>
        <ul>${qs.map((q) => `<li>${q.q}<span class="num">${rub(q.cost)}</span></li>`).join('')}${r.count > qs.length ? `<li class="muted">и ещё ${r.count - qs.length}</li>` : ''}</ul>
        ${why ? `<p class="why">${ic('sparkles', 15)}<span>${QWHY[k]}</span></p>` : ''}
        <footer>${groupAct(k)}${S.minus.has(k) ? '' : `<button class="btn btn--ghost btn--sm" data-act="set:qwhy=${why ? '' : k}" aria-expanded="${why}">Почему это мусор?</button>`}</footer></article>`
    }).join('')}</div>
    <h2>ИИ не уверен — решите сами <span>${t.maybe}</span></h2>
    <div class="card">${M.queries.filter((q) => q.v === 'maybe').map((q) => `<div class="qrow"><div><b>${q.q}</b><small>${q.why}</small></div><span class="num muted">${stats(q)}</span>${pick(q)}</div>`).join('')}</div>
    <div class="okrow card">${badge('ok', `${t.yes} полезных ${words(t.yes)}`)}<span>С ними всё в порядке, делать ничего не нужно.</span><button class="btn btn--ghost btn--sm" data-act="flag:qyes" aria-expanded="${on('qyes')}">${on('qyes') ? 'Скрыть' : 'Показать'}${ic(on('qyes') ? 'up' : 'down', 15)}</button></div>
    ${on('qyes') ? `<div class="card">${M.queries.filter((q) => q.v === 'yes').map((q) => `<div class="qrow"><div><b>${q.q}</b></div><span class="num muted">${stats(q)}</span></div>`).join('')}<p class="pad muted">и ещё ${t.yes - 3} — у них есть заявки или клики по делу.</p></div>` : ''}
    ${expertHint('Фильтры, таблица всех запросов, память решений и общие списки минус-фраз')}`
}

function triageExpert() {
  const t = M.queryTotals
  const rows = queryRows()
  const row = (q) => `<tr class="${S.qcur === q.id ? 'is-cur' : ''}"><td>${q.q}${q.why ? `<small>${q.why}</small>` : ''}</td><td class="muted md">${qCamp(q).name}</td><td class="r md">${q.clicks}</td><td class="r">${rub(q.cost)}</td><td class="r">${q.conv || '—'}</td><td class="r">${pick(q)}</td></tr>`
  const grp = (title, list, act) => (list.length ? `<tr class="grp"><td colspan="3">${title}</td><td class="md" colspan="2"></td><td class="r">${act ? `<div class="grp__act">${act}</div>` : ''}</td></tr>${list.map(row).join('')}` : '')
  const body = rows.length
    ? `<div class="card scroll-x"><table class="tbl num"><tr><th>Запрос</th><th class="md">Кампания</th><th class="r md">Клики</th><th class="r">Расход</th><th class="r">Заявки</th><th class="r">Решение</th></tr>
        ${Object.keys(M.reasons).map((k) => grp(`<b>${M.reasons[k].label}</b> · ${M.reasons[k].count} ${words(M.reasons[k].count)} · ${rub(M.reasons[k].cost)}`, rows.filter((q) => q.r === k), groupAct(k))).join('')}
        ${grp(`<b>ИИ не уверен</b> · ${t.maybe} ${words(t.maybe)}`, rows.filter((q) => q.v === 'maybe'))}
        ${grp(`<b>Полезные</b> · ${t.yes} ${words(t.yes)}`, rows.filter((q) => q.v === 'yes'))}
      </table></div>`
    : `<div class="card empty empty--sm">${ic('search', 24)}<p>Под эти условия запросов нет.</p><div><button class="btn" data-act="qreset">Сбросить фильтры</button></div></div>`
  return `${triageSummary()}
    <div class="fbar">${search('qq', 'Найти запрос')}${dd('qcamp', ['Все кампании', M.campaigns[0].name, M.campaigns[1].name], 'Все кампании')}
      <label class="field field--row"><span>Расход от</span><span class="inp"><input data-k="qmin" data-live value="${attr(val('qmin', ''))}" placeholder="0" style="width:56px"><i>₽</i></span></label>
      ${chipFlag('qnoconv', 'Без конверсий')}</div>
    ${body}
    <div class="card open pad applybar">
      <div><b>Мусорные — в минус-фразы</b><span class="muted">Куда добавлять:</span>${dd('qlevel', ['В кампанию', 'В группу объявлений', 'В общий список'], 'В кампанию')}</div>
      <div><b>Полезные — в ключевые фразы</b>${field('qbid', 'Ставка для новых фраз', '45', { unit: '₽', w: 56, cls: 'field--row' })}<button class="btn btn--sm" data-act="toast:Запросы с решением «Наш» добавлены как фразы. Запись появилась в «Истории»">${ic('plus', 14)}Добавить «наши» как фразы</button></div>
    </div>
    <p class="meta">С клавиатуры: ↑ и ↓ — выбрать запрос, 1 — «наш», 2 — «не наш».</p>`
}

/* ---------- состояния проектов без готового разбора ---------- */

function queriesNew(p) {
  if (!S.marked[p.id]) return `<div class="card empty">${ic('filter', 30)}<h2>36 новых запросов за неделю</h2>
    <p>ИИ ещё не смотрел их. Он разложит запросы на «наши» и мусор и объяснит почему — вам останется решить группами.</p>
    <div><button class="btn btn--pri" data-act="mark:${p.id}">${ic('sparkles', 16)}Разметить с ИИ</button></div><small class="muted">Разметка потратит около 1% месячного лимита</small></div>`
  const done = on('qmsk')
  return `<div class="summary card">${ic('sparkles', 20)}<p>${done ? 'Готово: 7 минус-фраз добавлены. Экономия — около <b>890 ₽ в неделю</b>.' : 'ИИ просмотрел <b>36 запросов</b>: 7 похожи на мусор — на них ушло <b>890 ₽</b> за неделю. Остальные 29 — ваши.'}</p>
      ${done ? `<button class="btn" data-act="flag:qmsk">${ic('undo', 15)}Вернуть</button>` : `<button class="btn btn--pri" data-act="flag:qmsk">Добавить 7 минус-фраз</button>`}</div>
    <div class="card scroll-x"><table class="tbl num ${done ? 'is-struck' : ''}"><tr><th>Запрос</th><th class="r">Клики</th><th class="r">Расход</th><th class="r">Заявки</th></tr>
      ${QMSK.map((q) => `<tr><td>${q[0]}</td><td class="r">${q[1]}</td><td class="r">${rub(q[2])}</td><td class="r">—</td></tr>`).join('')}
      <tr><td class="muted">и ещё 2 запроса</td><td class="r">5</td><td class="r">${rub(120)}</td><td class="r">—</td></tr></table></div>`
}

function queriesPending() {
  const st = S.decided.t5
  if (st === 'done') return `<div class="card empty">${ic('check', 30)}<h2>Запросы разобраны</h2><p>9 минус-фраз добавлены в кампании. Новые запросы появятся после следующего обновления из Директа.</p><div><button class="btn" data-act="go:history">Открыть историю</button></div></div>`
  if (st === 'rejected') return `<div class="card empty">${ic('filter', 30)}<h2>Предложение отклонено</h2><p>9 запросов остались без решения. Можно разобрать их заново или вернуть предложение ИИ.</p><div><button class="btn btn--pri" data-act="undo:t5">${ic('undo', 16)}Вернуть предложение</button></div></div>`
  return `<div class="card empty">${ic('sparkles', 30)}<h2>ИИ уже разобрал запросы и ждёт вашего решения</h2>
    <p>Из 64 новых запросов 9 — мусор: аренда кофемашин и чужие бренды. На них ушло 1 150 ₽ за неделю.</p>
    <div><button class="btn btn--pri" data-act="done:t5">Добавить 9 минус-фраз</button><button class="btn" data-act="chat:c3">Посмотреть в чате</button></div></div>`
}

/** Без выбранного проекта — очередь: где есть что разбирать. */
function queriesQueue() {
  const t = M.queryTotals
  const line = {
    okna: allMinus() ? ['ok', 'Мусорные запросы в минус-фразах', 'Посмотреть'] : ['warn', `${t.all} новых ${words(t.all)}, ${t.no} похожи на мусор · ${rub(t.noCost)} за неделю`, 'Разобрать'],
    msk: on('qmsk') ? ['ok', '7 минус-фраз добавлены', 'Посмотреть'] : ['warn', S.marked.msk ? '7 мусорных запросов на 890 ₽ — ждут решения' : '36 новых запросов, ИИ ещё не размечал', 'Открыть'],
    dent: ['ok', 'Новых запросов нет', ''],
    kofe: S.decided.t5 === 'done' ? ['ok', '9 минус-фраз добавлены', 'Посмотреть'] : ['warn', 'ИИ предлагает 9 минус-фраз · 1 150 ₽ за неделю', 'Открыть'],
  }
  return `<div class="card">${M.projects.map((p) => {
    const [st, text, btn] = line[p.id]
    return `<div class="prow"><div><small>${p.client || 'Без клиента'}</small><b>${p.name}</b><p>${text}</p></div>
      <div class="prow__acts">${st === 'ok' ? badge('ok', 'Разобрано') : badge('warn', 'Есть что разобрать')}${btn ? `<button class="btn ${st === 'warn' ? 'btn--pri' : ''}" data-act="project:${p.id}">${btn}</button>` : ''}</div></div>`
  }).join('')}</div>
  <p class="meta">Новые запросы сервис забирает из Директа каждое утро. Размечает их ИИ — только когда вы нажмёте кнопку.</p>`
}

/* ---------- память решений и списки минус-фраз (экспертный режим) ---------- */

function queriesMemory() {
  if (!S.project) return needProject('Решения по запросам')
  return `<div class="card pad"><p><b>Сервис помнит ваши решения.</b> Запрос, который вы назвали своим, больше не попадёт в мусор, а отклонённый — не вернётся в разбор. ИИ учитывает эту память при каждой разметке.</p></div>
    <div class="card scroll-x"><table class="tbl num"><tr><th>Запрос</th><th>Решение</th><th>Когда</th><th></th></tr>
      ${M.memory.map((m, i) => `<tr><td>${on(`forget:${i}`) ? `<s>${m[0]}</s>` : m[0]}</td><td>${m[1] === 'Наш' ? badge('ok', 'Наш') : `<span class="st st--off">${ic('x', 13)}Не наш</span>`}</td><td class="muted">${m[2]}</td>
        <td class="r"><button class="btn btn--ghost btn--sm" data-act="flag:forget:${i}">${on(`forget:${i}`) ? `${ic('undo', 14)}Вернуть` : 'Забыть'}</button></td></tr>`).join('')}</table></div>
    ${S.project === 'okna' ? '' : '<p class="meta">В макете показана память проекта okna-profi.ru.</p>'}`
}

function queriesLists() {
  const prev = val('mlprev', '')
  return `<div class="card pad"><p><b>Общие списки минус-фраз</b> подключаются сразу к нескольким кампаниям. Добавили фразу в список — она применилась везде, где он подключён.</p></div>
    ${M.minusLists.map((l, i) => `<div class="card pad mlist"><div class="between"><div><b>${l.name}</b><p class="muted">${l.count} ${plural(l.count, 'фраза', 'фразы', 'фраз')} · подключён к ${l.camps} ${plural(l.camps, 'кампании', 'кампаниям', 'кампаниям')}</p></div>
        <div class="acts acts--tight"><button class="btn btn--sm" data-act="set:mlprev=${prev === String(i) ? '' : i}" aria-expanded="${prev === String(i)}">Обновить из разбора</button><button class="btn btn--ghost btn--sm" data-act="toast:Список откроется для правки: фразы можно добавить или убрать вручную">${ic('edit', 14)}Изменить</button></div></div>
      ${prev === String(i) ? `<div class="effect num"><div><small>Добавим</small><b>${6 - i * 2} ${plural(6 - i * 2, 'фразу', 'фразы', 'фраз')}</b></div><div><small>Уберём</small><b>0</b></div><div><small>Пропустим</small><b>${2 + i} — уже есть</b></div></div>
        <div class="acts acts--tight"><button class="btn btn--pri btn--sm" data-act="toast:Список обновлён и применён к кампаниям. Запись появилась в «Истории»">Применить</button><button class="btn btn--sm" data-act="set:mlprev=">Отмена</button></div>` : ''}</div>`).join('')}
    <div class="acts"><button class="btn" data-act="toast:Назовите список и выберите кампании — фразы добавите следующим шагом">${ic('plus', 16)}Создать список</button></div>`
}

function queries() {
  const p = S.project && project(S.project)
  const tab = pro() ? sub('queries', 'triage') : 'triage'
  const st = p && QSTATE[p.id]
  let body
  if (tab === 'memory') body = queriesMemory()
  else if (tab === 'lists') body = queriesLists()
  else if (!p) body = queriesQueue()
  else if (st === 'marked') body = pro() ? triageExpert() : triageSimple()
  else if (st === 'new') body = queriesNew(p)
  else if (st === 'pending') body = queriesPending()
  else body = `<div class="card empty">${ic('check', 30)}<h2>Новых запросов нет</h2><p>Все запросы за неделю уже разобраны 6 ноября. Следующая загрузка из Директа — завтра утром.</p></div>`
  return `<div class="page" data-scroll="main"><div class="wide">
    <div class="head"><div><p class="eyebrow">${projectName(S.project)} · новые поисковые запросы</p><h1>${p ? 'Чистка запросов' : 'Запросы'}</h1></div>
      <div class="head__acts">${dd('qper', QPERIODS, QPERIODS[0])}<button class="btn" data-act="toast:Запросы обновлены из Директа: новых нет">${ic('refresh', 16)}Обновить из Директа</button></div></div>
    ${pro() ? ptabs('queries', [['triage', 'Разбор'], ['memory', 'Память решений'], ['lists', 'Списки минус-фраз']], 'triage') : ''}
    ${body}
  </div></div>`
}

/** Клавиатура в таблице разбора: стрелки выбирают запрос, 1 и 2 ставят решение. */
function onKey(e) {
  if (S.screen !== 'queries' || !pro() || S.project !== 'okna' || sub('queries', 'triage') !== 'triage' || S.modal) return false
  if (/^(INPUT|TEXTAREA)$/.test((e.target && e.target.tagName) || '')) return false
  const rows = queryRows()
  const i = rows.findIndex((q) => q.id === S.qcur)
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    if (!rows.length) return false
    S.qcur = rows[Math.max(0, Math.min(rows.length - 1, i < 0 ? 0 : i + (e.key === 'ArrowDown' ? 1 : -1)))].id
  } else if ((e.key === '1' || e.key === '2') && i >= 0) S.verdicts[S.qcur] = e.key === '1' ? 'yes' : 'no'
  else return false
  e.preventDefault()
  return true
}

Object.assign(ACT, {
  minusall: () => {
    Object.keys(M.reasons).forEach((k) => S.minus.add(k))
    S.toast = 'Минус-фразы добавлены. Вернуть как было можно в «Истории»'
  },
  minusnone: () => S.minus.clear(),
  mark: (v) => {
    S.marked[v] = true
    S.toast = 'Разметка готова: потрачено около 1% месячного лимита'
  },
  qreset: () => {
    ;['qq', 'qmin', 'qcamp'].forEach((k) => delete S.form[k])
    S.flags.qnoconv = false
  },
})
