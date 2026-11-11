/* Кампании: список, страница кампании и создание вручную (только экспертный режим). */

const HEALTH = { bad: 'Тратят впустую', warn: 'Чуть дороже цели', ok: 'Работают хорошо', off: 'Остановлены' }
const CAB = { okna: 'okna-profi', msk: 'okna-profi', dent: 'ulybka-agency', kofe: 'kofe-shop' }
const CSTATES = ['Все, кроме архива', 'Идут показы', 'Остановлены', 'В архиве']
const CPERIODS = { 'За 7 дней': 1, 'За 30 дней': 4.3, 'С начала месяца': 1.3 }
const STRATEGIES = ['Максимум конверсий', 'Максимум кликов', 'Ручные ставки']
const SCHEDULES = ['Круглосуточно', 'Рабочее время', 'Своё расписание']
/* Отчёт о заголовках для масштабирования: заголовок, показы, CTR, заявки, вывод. */
const HEADLINES = [
  ['Остекление балконов под ключ — от 29 900 ₽', 18400, '8,4%', 11, 'ok', 'Лучший — в отдельную копию'],
  ['Застеклим балкон за 1 день. Замер бесплатно', 12100, '6,1%', 7, 'ok', 'Хороший — в отдельную копию'],
  ['Балконы и лоджии: тёплое остекление', 9800, '2,3%', 1, 'off', 'Слабый — не масштабируем'],
]
const CAMP_KEYS = ['cname', 'cbud', 'ccpa', 'creg', 'cneg', 'cstrat', 'csched', 'bedit', 'budget', 'grp']

const budgetOf = (c) => c.budget || Math.round((c.cost * 1.15) / 500) * 500
const search = (k, ph) => `<span class="inp inp--search">${ic('search', 15)}<input data-k="${k}" data-live value="${attr(val(k, ''))}" placeholder="${ph}" aria-label="${ph}"></span>`
const campSwitch = (c) => swBtn(`toggle:${c.id}`, c.on, c.on ? 'Остановить показы' : 'Запустить показы')
const chipFlag = (flag, label) => `<button class="chip chip--btn ${on(flag) ? 'is-on' : ''}" role="checkbox" aria-checked="${on(flag)}" data-act="flag:${flag}">${on(flag) ? ic('check', 13) : ''}${label}</button>`
/** Подпись и выпадающий список в форме. */
const ddField = (label, list, hint) => `<div class="field"><span>${label}</span>${list}${hint ? `<small>${hint}</small>` : ''}</div>`
const sampleNote = (c) => (c.id === 2 ? '' : '<p class="meta">В макете состав показан на примере кампании «Остекление балконов — Поиск».</p>')
const selWord = (n) => `${n} ${plural(n, 'кампания', 'кампании', 'кампаний')}`

function campList() {
  const st = pro() ? val('cstate', CSTATES[0]) : CSTATES[0]
  const q = val('cq', '').trim().toLowerCase()
  const cab = val('ccab', 'Все кабинеты')
  return visibleCampaigns().filter((c) => {
    if ((st === 'В архиве') !== !!c.archived) return false
    if (st === 'Идут показы' && !c.on) return false
    if (st === 'Остановлены' && c.on) return false
    if (!pro()) return true
    if (q && !c.name.toLowerCase().includes(q)) return false
    if (on('noconv') && c.conv) return false
    return S.project || cab === 'Все кабинеты' || CAB[c.project] === cab
  })
}

function camps() {
  const list = campList()
  const sel = list.filter((c) => S.sel.has(c.id))
  const k = CPERIODS[val('cper', 'За 7 дней')]
  const cost = (c) => rub(Math.round((c.cost * k) / 10) * 10)
  const conv = (c) => Math.round(c.conv * k)
  const where = (c) => `${c.type}${S.project ? '' : ` · ${project(c.project).name}`}`
  const check = (c) => checkBtn(`sel:${c.id}`, S.sel.has(c.id), `Выбрать «${c.name}»`)
  const empty = `<div class="card empty empty--sm">${ic('search', 24)}<p>Под эти условия ничего не подходит.</p><div><button class="btn" data-act="resetf">Сбросить фильтры</button></div></div>`
  let body
  if (!list.length) body = pro() ? empty : `<div class="card empty empty--sm">${ic('megaphone', 24)}<p>Кампаний пока нет. ИИ соберёт первую по шагам — от целей до объявлений.</p><div><button class="btn btn--pri" data-act="chat:c4">${ic('sparkles', 16)}Собрать кампанию с ИИ</button></div></div>`
  else if (pro()) {
    const all = list.every((c) => S.sel.has(c.id))
    body = `<div class="card scroll-x"><table class="tbl num">
      <tr><th>${checkBtn('selall', all, 'Выбрать все')}</th><th>Кампания</th><th>Состояние</th><th class="r lg">Бюджет в неделю</th><th class="r">Расход</th><th class="r xl">Клики</th><th class="r md">Конверсии</th><th class="r">CPA / ДРР</th><th class="xl">По дням</th><th></th></tr>
      ${list.map((c) => `<tr class="${S.sel.has(c.id) ? 'is-change' : ''}"><td>${check(c)}</td><td>${campName(c)}<small>${where(c)} · ${CAB[c.project]}</small></td><td>${c.archived ? '<span class="st st--off">В архиве</span>' : badge(c.health)}</td>
        <td class="r lg">${rub(budgetOf(c))}</td><td class="r">${cost(c)}</td><td class="r xl">${num(c.clicks * k)}</td><td class="r md">${conv(c)}</td><td class="r"><b>${campKpi(c)}</b></td><td class="xl">${bars(c.spark)}</td>
        <td>${c.archived ? `<button class="btn btn--ghost btn--sm" data-act="unarch:${c.id}">Вернуть</button>` : campSwitch(c)}</td></tr>`).join('')}
    </table></div>`
  } else {
    body = Object.entries(HEALTH).map(([h, label]) => {
      const rows = list.filter((c) => c.health === h)
      if (!rows.length) return ''
      return `<h2>${badge(h, label)}<span>${rows.length}</span></h2><div class="card">${rows.map((c) => `<div class="crow ${S.sel.has(c.id) ? 'is-sel' : ''}">${check(c)}
        <div>${campName(c)}<small>${where(c)}</small></div>
        <div class="crow__health">${campHealth(c)}</div>
        <div class="crow__num num"><b>${cost(c)}</b><small>${conv(c)} ${convWord(conv(c), project(c.project))}</small></div>
        ${campSwitch(c)}</div>`).join('')}</div>`
    }).join('')
  }
  const fbar = pro() ? `<div class="fbar">${search('cq', 'Найти кампанию')}${dd('cstate', CSTATES, CSTATES[0])}${S.project ? '' : dd('ccab', ['Все кабинеты', ...new Set(Object.values(CAB))], 'Все кабинеты')}${chipFlag('noconv', 'Без конверсий')}</div>` : ''
  const bulk = pro() && sel.length ? `<div class="bulk"><b>Выбрано: ${sel.length}</b>
    <button class="btn btn--sm" data-act="bulk:start">${ic('play', 14)}Запустить</button><button class="btn btn--sm" data-act="bulk:stop">${ic('pause', 14)}Остановить</button>
    <button class="btn btn--sm" data-act="modal:bulkbudget">Бюджет и стратегия…</button><button class="btn btn--sm" data-act="bulk:copy">${ic('copy', 14)}Копировать</button>
    <button class="btn btn--sm" data-act="bulk:archive">${ic('archive', 14)}В архив</button><button class="btn btn--sm" data-act="modal:delcamps">${ic('trash', 14)}Удалить</button>
    <button class="btn btn--ghost btn--sm" data-act="selnone">Снять выбор</button></div>` : ''
  const prompts = sel.length ? ['Почему у них дорогая заявка?', 'Что из этого отключить?', 'Сравни их между собой'] : ['Какие тратят впустую?', 'Что отключить?', 'Куда добавить бюджет?']
  return `<div class="page" data-scroll="main"><div class="wide">
      <div class="head"><div><p class="eyebrow">${projectName(S.project)}</p><h1>Кампании</h1></div>
        <div class="head__acts">${dd('cper', Object.keys(CPERIODS), 'За 7 дней')}
          ${pro() ? `<button class="btn" data-act="go:wizard">${ic('plus', 16)}Создать вручную</button>` : ''}<button class="btn btn--pri" data-act="chat:c4">${ic('sparkles', 16)}${pro() ? 'Собрать с ИИ' : 'Собрать новую с ИИ'}</button></div></div>
      ${fbar}${bulk}${body}
      ${list.length ? expertHint('Фильтры, архив, массовые действия и создание кампании вручную') : ''}
    </div></div>
    <div class="askbar"><div class="askbar__in">
      <div class="askbar__ctx">${ic('sparkles', 15)}${sel.length
        ? `<b>Выбрано ${sel.length}:</b>${sel.map((c) => `<span class="chip">${c.name}<button data-act="sel:${c.id}" aria-label="Убрать">${ic('x', 12)}</button></span>`).join('')}`
        : `<span>Вопрос про <b>все ${selWord(list.length).replace('кампания', 'кампанию')}</b> — или отметьте нужные галочкой</span>`}</div>
      <div class="askbar__box"><input placeholder="Спросите про эти кампании…" aria-label="Вопрос про кампании"><button class="send" data-act="chat:c1" aria-label="Спросить">${ic('up')}</button></div>
      <div class="starters">${prompts.map((s) => `<button data-act="chat:c1">${s}</button>`).join('')}</div>
    </div></div>`
}

/* ---------- страница кампании ---------- */

function campBudget(c) {
  if (val('bedit', '0') === '1') return `<div class="card pad brow">${field('budget', 'Недельный бюджет', num(budgetOf(c)), { unit: '₽', w: 120 })}
    <div class="brow__acts"><button class="btn btn--pri" data-act="savebudget">Сохранить</button><button class="btn" data-act="set:bedit=0">Отмена</button></div></div>`
  return `<div class="card pad brow"><div><small class="cap">Недельный бюджет</small><b class="big num">${rub(budgetOf(c))}</b>
    <p class="muted">За эту неделю потрачено ${rub(c.cost)} — ${Math.round((c.cost / budgetOf(c)) * 100)}% бюджета.</p></div><button class="btn" data-act="editbudget">Изменить</button></div>`
}

function campGroupsTab(c) {
  const open = +val('grp', '0')
  return `${sampleNote(c)}${M.campGroups.map((g, i) => {
    const body = open !== i ? '' : `<div class="acc__body">
      <h4>Объявления</h4>
      <div class="scroll-x"><table class="tbl num"><tr><th>Заголовок</th><th class="r">CTR на поиске</th><th class="r">CTR в сетях</th><th>Статус</th><th></th></tr>
        ${M.campAds.slice(0, g.ads).map((a) => `<tr><td>${a[0]}</td><td class="r">${a[1]}</td><td class="r">${a[2]}</td><td>${badge(a[3], a[4])}</td>
          <td class="r">${a[3] === 'bad' ? `<button class="btn btn--sm" data-act="toast:ИИ предложит новый текст — он уйдёт на модерацию только после вашего согласия">${ic('sparkles', 14)}Исправить</button>` : `<button class="btn btn--ghost btn--sm" data-act="toast:Объявление остановлено. Вернуть можно в «Истории»">Остановить</button>`}</td></tr>`).join('')}</table></div>
      <h4>Фразы и ставки</h4>
      <div class="scroll-x"><table class="tbl num"><tr><th>Фраза</th><th class="r">Ставка</th><th class="r">Доля кликов</th><th class="r">Доля заявок</th></tr>
        ${M.campPhrases.map((p, j) => `<tr><td>${p[0]}</td><td class="r"><span class="inp inp--sm"><input data-k="bid${i}-${j}" value="${attr(val(`bid${i}-${j}`, p[1]))}" aria-label="Ставка для «${p[0]}»"><i>₽</i></span></td><td class="r">${Math.round(p[2] * 100)}%</td><td class="r">${Math.round(p[3] * 100)}%</td></tr>`).join('')}</table></div>
      <div class="acc__acts"><button class="btn btn--pri btn--sm" data-act="toast:Ставки сохранены. Запись появилась в «Истории»">Сохранить ставки</button><button class="btn btn--sm" data-act="toast:Вставьте фразы списком — добавим их в эту группу">${ic('plus', 14)}Добавить фразы</button><button class="btn btn--sm" data-act="toast:ИИ напишет вариант объявления и покажет его до отправки">${ic('sparkles', 14)}Новое объявление с ИИ</button></div>
    </div>`
    return `<div class="acc card ${open === i ? 'is-open' : ''}"><button class="acc__head" data-act="set:grp=${open === i ? -1 : i}" aria-expanded="${open === i}">${ic(open === i ? 'down' : 'right', 16)}<b>${g.name}</b>
      <span class="muted num">${g.phrases} ${plural(g.phrases, 'фраза', 'фразы', 'фраз')} · ${g.ads} ${plural(g.ads, 'объявление', 'объявления', 'объявлений')} · ${rub(c.cost * g.share)}</span></button>${body}</div>`
  }).join('')}`
}

function campSettingsTab(c, p) {
  return `<div class="card open pad"><div class="form">
    ${field('cname', 'Название', c.name, { cls: 'field--wide' })}
    ${ddField('Стратегия', dd('cstrat', STRATEGIES, STRATEGIES[0]))}
    ${field('cbud', 'Недельный бюджет', num(budgetOf(c)), { unit: '₽' })}
    ${field('ccpa', c.drr != null ? 'Доля расходов, не выше' : 'Цена заявки, не выше', c.drr != null ? p.target : num(p.target), { unit: c.drr != null ? '%' : '₽' })}
    ${field('creg', 'Регион показа', 'Москва и область')}
    ${ddField('Расписание', dd('csched', SCHEDULES, SCHEDULES[0]))}
    ${field('cneg', 'Минус-фразы кампании', 'вакансии, своими руками, бу, авито, чертёж', { area: true, rows: 2, cls: 'field--wide', hint: 'Общие для всех групп. Минус-фразы из чистки запросов добавляются сюда же.' })}
  </div>
  <div class="form__foot"><button class="btn btn--pri" data-act="toast:Настройки сохранены. Запись появилась в «Истории»">Сохранить</button><small>Изменения сразу уйдут в Директ. Корректировки ставок — в «Отчётах», на вкладке «Площадки».</small></div></div>`
}

function campHeadlinesTab() {
  return `<div class="card pad"><p><b>Масштабирование заголовков.</b> Загрузите отчёт о заголовках из Директа — сервис найдёт те, что приносят заявки, а ИИ соберёт под каждый отдельную копию кампании: одно объявление — один заголовок.</p>
      <p class="muted">Копии создаются черновиками. Каждый шаг ждёт вашего согласования, метки в ссылках ставятся по шаблону проекта.</p></div>
    <h2>Отчёт о заголовках<small>загружен 8 ноября · 3 заголовка</small></h2>
    <div class="card scroll-x"><table class="tbl num"><tr><th>Заголовок</th><th class="r">Показы</th><th class="r">CTR</th><th class="r">Заявки</th><th>Вывод</th></tr>
      ${HEADLINES.map((h) => `<tr><td>${h[0]}</td><td class="r">${num(h[1])}</td><td class="r">${h[2]}</td><td class="r">${h[3]}</td><td>${badge(h[4], h[5])}</td></tr>`).join('')}</table></div>
    <div class="acc__acts"><button class="btn btn--pri" data-act="toast:В рабочем кабинете откроется чат: ИИ предложит шаги, каждый ждёт вашего «Согласовать»">${ic('sparkles', 16)}Масштабировать с ИИ</button><button class="btn" data-act="toast:Откроется выбор файла с отчётом">${ic('upload', 16)}Загрузить другой отчёт</button></div>`
}

function camp() {
  const c = M.campaigns.find((x) => x.id === S.camp && !x.deleted) || M.campaigns[1]
  const p = project(c.project)
  const drr = c.drr != null
  const groups = `<h2>Что внутри<small>группы объявлений и их доля расхода</small></h2>
    <div class="card">${M.campGroups.map((g) => `<div class="gline"><div><b>${g.name}</b><small>${g.phrases} ${plural(g.phrases, 'фраза', 'фразы', 'фраз')} · ${g.ads} ${plural(g.ads, 'объявление', 'объявления', 'объявлений')}</small></div>
      <span class="meter meter--wide" title="${Math.round(g.share * 100)}% расхода"><i style="width:${g.share * 100}%"></i></span><b class="num">${rub(c.cost * g.share)}</b></div>`).join('')}</div>`
  /* Только записи про эту кампанию: чужие изменения в её карточке сбивают с толку. */
  const hist = M.history.filter((h) => h.project === c.project && (h.title.includes(c.name) || h.params.some((x) => x[1] === c.name))).slice(0, 2)
  const recent = `<h2>Недавние изменения<small><button class="lnk" data-act="at:${c.project}=history">Вся история</button></small></h2>
    <div class="card">${hist.length ? hist.map(histLine).join('') : '<p class="pad muted">Эту кампанию давно не меняли.</p>'}</div>`
  const tiles = `<div class="tiles">
    ${tile('Потрачено за 7 дней', rub(c.cost), `${Math.round((c.cost / budgetOf(c)) * 100)}% недельного бюджета`)}
    ${tile(drr ? 'Заказы' : 'Заявки', c.conv, `${num(c.clicks)} ${plural(c.clicks, 'клик', 'клика', 'кликов')}`)}
    ${tile(drr ? 'Доля расходов на рекламу' : 'Цена заявки', campKpi(c), `цель — до ${drr ? `${p.target}%` : rub(p.target)}`)}
    ${pro() ? tile('Конверсия из клика', `${((c.conv / c.clicks) * 100).toFixed(1).replace('.', ',')}%`, 'заявки к кликам') : ''}
  </div>`
  const tab = sub('camp', 'over')
  const body = !pro()
    ? `<p class="lead">${badge(c.health)}<span>${campHealth(c)}.</span></p>${tiles}${campBudget(c)}${groups}${recent}${expertHint('Объявления, фразы, ставки и настройки кампании')}`
    : `${ptabs('camp', [['over', 'Обзор'], ['groups', 'Группы, объявления, фразы'], ['set', 'Настройки'], ['head', 'Заголовки']], 'over')}
      ${tab === 'groups' ? campGroupsTab(c) : tab === 'set' ? campSettingsTab(c, p) : tab === 'head' ? campHeadlinesTab()
        : `${tiles}<h2>${drr ? 'Заказы' : 'Заявки'} по дням</h2><div class="card pad">${barChart({ data: c.spark, labels: daily(7).labels, h: 170, label: 'Конверсии кампании по дням' })}</div>${campBudget(c)}${groups}${recent}`}`
  const menu = `<div class="pop"><button class="iconbtn" data-act="menu:camp" aria-label="Действия с кампанией" title="Действия с кампанией">${ic('more')}</button>
    ${S.menu === 'camp' ? `<div class="menu menu--right"><button data-act="toast:Кампания отправлена на модерацию">${ic('send', 16)}Отправить на модерацию</button><button data-act="toast:Копия создана как черновик — она появится в списке через минуту">${ic('copy', 16)}Создать копию</button>
      <button data-act="camparch">${ic('archive', 16)}В архив</button><hr><button data-act="modal:delcamp">${ic('trash', 16)}Удалить</button></div>` : ''}</div>`
  return `<div class="page" data-scroll="main"><div class="wide">
    <button class="lnk back" data-act="go:camps">${ic('left', 15)}Кампании</button>
    <div class="head"><div><p class="eyebrow">${c.type} · ${p.name}${pro() ? ` · кабинет ${CAB[c.project]}` : ''}</p><h1>${c.name}</h1></div>
      <div class="head__acts">${c.archived ? '<span class="chip">В архиве</span>' : `<span class="swlabel">${campSwitch(c)}<span>${c.on ? 'Идут показы' : 'Остановлена'}</span></span>`}
        <button class="btn btn--pri" data-act="chat:c1">${ic('sparkles', 16)}Разобрать с ИИ</button>${pro() ? menu : ''}</div></div>
    ${body}
  </div></div>`
}

/* ---------- создание вручную ---------- */

function wizard() {
  const sec = (n, title, inner) => `<section class="wsec"><h2><i>${n}</i>${title}</h2><div class="card open pad"><div class="form">${inner}</div></div></section>`
  return `<div class="page" data-scroll="main"><div class="wide wide--form">
    <button class="lnk back" data-act="go:camps">${ic('left', 15)}Кампании</button>
    <div class="head"><div><p class="eyebrow">${projectName(S.project)}</p><h1>Новая кампания вручную</h1></div>
      <div class="head__acts"><button class="btn" data-act="chat:c4">${ic('sparkles', 16)}Лучше собрать с ИИ</button></div></div>
    ${sec(1, 'Основное', `${field('wname', 'Название', '', { ph: 'Например: Окна ПВХ — Поиск — Москва', cls: 'field--wide' })}
      <div class="field"><span>Где показывать</span>${segm('wtype', [['search', 'Поиск'], ['net', 'Сети'], ['goods', 'Товары']], 'search')}</div>
      ${S.project ? ddField('Кабинет Директа', `<span class="chip">${CAB[S.project]}</span>`) : ddField('Сайт', dd('wproj', M.projects.map((p) => p.name), M.projects[0].name))}`)}
    ${sec(2, 'Бюджет и стратегия', `${ddField('Стратегия', dd('wstrat', STRATEGIES, STRATEGIES[0]))}${field('wbud', 'Недельный бюджет', '10 000', { unit: '₽' })}${field('wcpa', 'Цена заявки, не выше', '450', { unit: '₽', hint: 'Подставили цель проекта' })}`)}
    ${sec(3, 'Показы', `${field('wreg', 'Регион показа', 'Москва и область')}${ddField('Расписание', dd('wsched', SCHEDULES, SCHEDULES[0]))}`)}
    ${sec(4, 'Ссылки', `${field('wurl', 'Страница сайта', '', { ph: 'https://…', cls: 'field--wide' })}
      <div class="field field--wide"><span>Метки в ссылках</span><p class="muted">Подставятся по шаблону проекта. <button class="lnk" data-act="to:project=utm">Открыть шаблон</button></p></div>`)}
    ${sec(5, 'Первая группа объявлений', `${field('wgrp', 'Название группы', '', { ph: 'Например: Общие запросы' })}${field('wtitle', 'Заголовок объявления', '', { ph: 'До 56 символов' })}
      ${field('wphr', 'Ключевые фразы', '', { area: true, rows: 3, ph: 'Каждая фраза с новой строки', cls: 'field--wide' })}
      ${field('wtext', 'Текст объявления', '', { area: true, rows: 2, ph: 'До 81 символа', cls: 'field--wide' })}
      <div class="field field--wide"><button class="btn btn--sm" data-act="toast:ИИ напишет три варианта по названию и фразам — около 1% месячного лимита">${ic('sparkles', 14)}Написать тексты с ИИ</button></div>`)}
    <div class="form__foot form__foot--page"><button class="btn btn--pri" data-act="toast:Черновик создан в Директе. Запись появилась в «Истории»" data-then="camps">Создать черновик</button><button class="btn" data-act="go:camps">Отмена</button>
      <small>Черновик сам не запустится: на модерацию его отправляете вы.</small></div>
  </div></div>`
}

Object.assign(MODALS, {
  bulkbudget: () => {
    const n = S.sel.size
    return {
      title: `Бюджет и стратегия для ${n} ${plural(n, 'кампании', 'кампаний', 'кампаний')}`,
      body: `<p class="muted">Пустое поле не меняет значение — у каждой кампании останется своё.</p>
        <div class="form form--one">${field('bbud', 'Недельный бюджет', '', { unit: '₽', ph: 'Не менять' })}<div class="field"><span>Стратегия</span>${segm('bstrat', [['keep', 'Не менять'], ['conv', 'Максимум конверсий'], ['click', 'Максимум кликов']], 'keep')}</div></div>`,
      foot: `<button class="btn btn--pri" data-act="bulk:budget" data-close>Применить</button><button class="btn" data-act="close">Отмена</button>`,
    }
  },
  delcamps: () => ({
    title: `Удалить ${selWord(S.sel.size).replace('кампания', 'кампанию')}?`,
    body: `<p>Кампании исчезнут из Директа и из кабинета. Статистика за прошлые дни останется в отчётах.</p><p class="muted">Удаление нельзя вернуть из «Истории». Если кампании могут пригодиться, отправьте их в архив.</p>`,
    foot: `<button class="btn btn--danger" data-act="bulk:delete" data-close>Удалить</button><button class="btn" data-act="bulk:archive" data-close>В архив</button><button class="btn" data-act="close">Отмена</button>`,
  }),
  delcamp: () => ({
    title: 'Удалить кампанию?',
    body: `<p>«${(M.campaigns.find((c) => c.id === S.camp) || {}).name}» исчезнет из Директа и из кабинета. Статистика за прошлые дни останется в отчётах.</p><p class="muted">Удаление нельзя вернуть из «Истории».</p>`,
    foot: `<button class="btn btn--danger" data-act="campdel" data-then="camps" data-close>Удалить</button><button class="btn" data-act="close">Отмена</button>`,
  }),
})

Object.assign(ACT, {
  camp: (v) => {
    S.camp = +v
    CAMP_KEYS.forEach((k) => delete S.form[k])
    ACT.go('camp')
  },
  selall: () => {
    const ids = campList().map((c) => c.id)
    const all = ids.every((id) => S.sel.has(id))
    ids.forEach((id) => (all ? S.sel.delete(id) : S.sel.add(id)))
  },
  selnone: () => S.sel.clear(),
  resetf: () => {
    ;['cq', 'cstate', 'ccab'].forEach((k) => delete S.form[k])
    S.flags.noconv = false
  },
  bulk: (v) => {
    const list = M.campaigns.filter((c) => S.sel.has(c.id))
    const word = selWord(list.length)
    if (v === 'start' || v === 'stop') list.filter((c) => c.on !== (v === 'start') && !c.archived).forEach((c) => ACT.toggle(String(c.id)))
    if (v === 'archive') list.forEach((c) => Object.assign(c, { archived: true, on: false, was: c.health === 'off' ? c.was : c.health, health: 'off' }))
    if (v === 'delete') list.forEach((c) => (c.deleted = true))
    S.toast = { start: `Запущено: ${word}`, stop: `Остановлено: ${word}. Вернуть можно в «Истории»`, budget: `Бюджет и стратегия изменены: ${word}. Запись появилась в «Истории»`, copy: `Копии созданы черновиками: ${word}`, archive: `В архиве: ${word}. Найти их можно фильтром «В архиве»`, delete: `Удалено: ${word}` }[v]
    if (v !== 'start' && v !== 'stop') S.sel.clear()
  },
  unarch: (v) => {
    const c = M.campaigns.find((x) => x.id === +v)
    c.archived = false
    S.toast = `«${c.name}» возвращена из архива и остановлена`
  },
  camparch: () => {
    const c = M.campaigns.find((x) => x.id === S.camp)
    Object.assign(c, { archived: true, on: false, was: c.health === 'off' ? c.was : c.health, health: 'off' })
    ACT.go('camps')
    S.toast = `«${c.name}» в архиве`
  },
  campdel: () => {
    const c = M.campaigns.find((x) => x.id === S.camp)
    c.deleted = true
    S.toast = `«${c.name}» удалена`
  },
  editbudget: () => {
    delete S.form.budget
    S.form.bedit = '1'
  },
  savebudget: () => {
    const c = M.campaigns.find((x) => x.id === S.camp)
    const was = budgetOf(c)
    const raw = c.budget
    S.revert = () => (c.budget = raw)
    c.budget = parseInt(String(S.form.budget || '').replace(/\D/g, ''), 10) || was
    S.form.bedit = '0'
    S.toast = c.budget === was ? 'Бюджет не изменился' : `Недельный бюджет: ${rub(was)} → ${rub(c.budget)}. Вернуть можно в «Истории»`
  },
})
