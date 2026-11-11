/* Чат с ИИ: разговор слева, данные и предложения — в панели рядом. Сборка кампании идёт по шагам на доске. */

const DONE_TEXT = { t3: 'Выполнено — ставки изменены в Директе', t4: 'Выполнено — площадки исключены', t5: 'Выполнено — минус-фразы добавлены' }
/* Расход, заявки и цена заявки по группам из предложения — для экспертного вида. */
const GROUPS = [[4100, 8, 513], [3900, 7, 557], [2900, 7, 414]]
const STAGE_MSG = [
  'Записал цели: заявки с сайта не дороже 600 ₽, бюджет 15 000 ₽ в неделю.',
  'Посмотрел спрос в Вордстате: шесть основных направлений, около 47 тысяч запросов в месяц.',
  'Собрал 4 группы и по 3 объявления в каждой. Посмотрите тексты — их можно поправить прямо на доске.',
  'Показал, как объявления выглядят на компьютере и на телефоне.',
  'Подобрал 40 минус-фраз, чтобы не платить за случайные запросы.',
  'Добавил быстрые ссылки и уточнения — с ними объявление занимает больше места.',
  'Картинки нужны только для показов в сетях. Для поиска этот шаг можно согласовать как есть.',
  'Предлагаю две аудитории, чтобы возвращать посетителей сайта.',
  'Собрал настройки кампании: стратегия, бюджет и расписание.',
  'Всё готово. Создам черновик в Директе — без вашего решения он не запустится.',
]
const APPROVAL_NOTE = '<small>Ничего не изменится, пока вы не нажмёте «Выполнить». Вернуть как было можно в «Истории».</small>'
const hasCanvas = () => S.chat !== 'c5'
const aiMeta = (n) => (pro() ? `<p class="meta">Стандартная модель · обращений к данным: ${n} · около 1% месячного лимита</p>` : '')

function decision(id) {
  const st = S.decided[id]
  if (!st) return `<button class="btn btn--pri" data-act="done:${id}">${ic('check', 16)}Выполнить</button><button class="btn" data-act="reject:${id}">Отклонить</button>`
  return `${st === 'done' ? badge('ok', DONE_TEXT[id]) : rejected}<button class="btn btn--ghost btn--sm" data-act="undo:${id}">${ic('undo', 14)}${st === 'done' ? 'Вернуть как было' : 'Вернуть'}</button>`
}

const canvasShell = (head, body, foot) => `<aside class="canvas">
  <header class="canvas__head">${head}<button class="iconbtn" data-act="panel" aria-label="Закрыть панель">${ic('x')}</button></header>
  <div class="canvas__body" data-scroll="canvas">${body}</div>${foot ? `<footer class="canvas__foot">${foot}</footer>` : ''}</aside>`

const thread = (inner) => `<section class="thread">
  <div class="thread__scroll" data-scroll="main"><div class="thread__in">${inner}</div></div>
  <div class="thread__foot">${composer(false, 'Уточните или попросите что-то ещё…')}</div></section>`

/** Карточка предложения внутри ответа ИИ. */
const refCard = (t) => `<div class="ref card ${S.decided[t.id] ? 'is-closed' : ''}">
  <div class="ref__head"><span class="tag">${ic('sparkles', 13)}Предложение</span><b>${t.title}</b></div>
  <p>${t.text}</p>${S.panel || !t.diff ? '' : diffTable(t)}
  <div class="ref__acts">${decision(t.id)}<button class="btn btn--ghost" data-act="tab:changes">${S.panel ? `Что изменится${ic('right', 15)}` : `${ic('panel', 15)}Открыть рядом`}</button></div></div>`

/* ---------- чат с разбором (c1) ---------- */

function canvasAnalysis() {
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
  return canvasShell(
    `<div class="tabs">${tabs.map(([k, label]) => `<button class="${S.tab === k ? 'active' : ''}" data-act="tab:${k}">${label}</button>`).join('')}</div>`,
    body,
    S.tab === 'changes' ? `<div>${decision('t3')}</div>${APPROVAL_NOTE}` : '',
  )
}

function threadAnalysis() {
  const c = M.chat
  return thread(`<div class="msg-user"><div>${c.question}</div></div>
    <div class="msg-ai">
      <p><b>${c.lead}</b></p>
      <ol>${c.points.map((p) => `<li><b>${p[0]}</b> ${p[1]}</li>`).join('')}</ol>
      ${S.panel
        ? `<div class="refs"><button data-act="tab:chart">${ic('chart', 15)}График цены заявки</button><button data-act="tab:camps">${ic('megaphone', 15)}Кампании проекта</button></div>`
        : `<figure class="fig card"><figcaption>Цена заявки по дням, ₽</figcaption>${cpaChart()}</figure>`}
      <p class="good">${ic('check', 16)}<span>${c.good}</span></p>
      <details class="steps" ${pro() ? 'open' : ''}><summary>Что я посмотрел · ${c.steps.length}</summary><ul>${c.steps.map((s) => `<li>${s}</li>`).join('')}</ul></details>
      ${refCard(M.tasks[2])}${aiMeta(3)}
    </div>`)
}

/* ---------- короткие чаты с предложением (c2, c3) и общий чат (c5) ---------- */

function canvasList(th) {
  const t = M.tasks.find((x) => x.id === th.task)
  return canvasShell(
    `<div class="tabs"><button class="active" data-act="tab:changes">Изменения</button></div>`,
    `<p class="eyebrow">Предложение ИИ · ${projectName(t.project)}</p><h3>${t.title}</h3><p class="muted">${t.text}</p>
      <table class="tbl num"><tr><th>${th.head[0]}</th><th class="r">${th.head[1]}</th><th class="r">${th.head[2]}</th></tr>
      ${th.rows.map((r) => `<tr><td>${r[0]}</td><td class="r">${r[1]}</td><td class="r">${r[2]}</td></tr>`).join('')}</table>
      <div class="effect num">${th.effect.map((e) => `<div><small>${e[0]}</small><b>${e[1]}</b></div>`).join('')}</div>
      ${pro() ? '<p class="muted">Полный список можно открыть в «Отчётах» и снять отметку с того, что хотите оставить.</p>' : ''}`,
    `<div>${decision(t.id)}</div>${APPROVAL_NOTE}`,
  )
}

function threadTask(th) {
  const t = M.tasks.find((x) => x.id === th.task)
  return thread(`<div class="msg-user"><div>${th.q}</div></div>
    <div class="msg-ai">
      <p><b>${th.lead}</b></p>
      <ol>${th.points.map((p) => `<li><b>${p[0]}</b> ${p[1]}</li>`).join('')}</ol>
      <details class="steps" ${pro() ? 'open' : ''}><summary>Что я посмотрел · 2</summary><ul><li>Статистика за 30 дней</li><li>Заявки из Метрики по источникам</li></ul></details>
      ${refCard(t)}${aiMeta(2)}
    </div>`)
}

function threadAll(th) {
  return thread(`<div class="msg-user"><div>${th.q}</div></div>
    <div class="msg-ai">
      <p><b>${th.lead}</b></p>
      <div class="card scroll-x"><table class="tbl num"><tr><th>Проект</th><th class="r">Расход за октябрь</th><th>Цель</th><th>Итог</th></tr>
        ${M.projects.map((p) => `<tr><td><button class="cname" data-act="at:${p.id}=home"><b>${p.name}</b></button></td><td class="r">${rub(Math.round((p.week * 4.4) / 100) * 100)}</td><td>${kpiText(p)}</td><td>${badge(p.status)}</td></tr>`).join('')}</table></div>
      <ol>${th.points.map((p) => `<li><b>${p[0]}</b> ${p[1]}</li>`).join('')}</ol>
      <p class="good">${ic('check', 16)}<span>В общем чате я вижу все проекты сразу. Изменения предлагаю по каждому проекту отдельно — в его собственном чате.</span></p>
      <div class="refs"><button data-act="chat:c2">${ic('chat', 15)}Чат msk.okna-profi.ru</button><button data-act="at:dent=camps">${ic('megaphone', 15)}Кампании ulybka-dent.ru</button></div>
      ${aiMeta(4)}
    </div>`)
}

/* ---------- сборка кампании по шагам (c4) ---------- */

const adPrev = (g) => `<div class="adprev"><small>Реклама · okna-profi.ru/dveri</small><b>${g.title}</b><p>${g.text}</p></div>`

function boardBody(i) {
  const B = M.board
  if (i > S.stageDone) return `<div class="empty empty--sm">${ic('lock', 24)}<p>ИИ соберёт этот шаг после согласования предыдущего.</p><div><button class="btn" data-act="stage:${S.stageDone}">К шагу ${S.stageDone + 1} «${B.stages[S.stageDone]}»</button></div></div>`
  if (i === 0) return `<h3>Цели кампании</h3><p class="muted">ИИ уточнил их в чате и записал. Поменять можно в любой момент — просто напишите.</p>${dl(B.goals)}`
  if (i === 1) return `<h3>Что ищут люди</h3><p class="muted">Данные Вордстата по Москве и области за месяц.</p>
    <table class="tbl num"><tr><th>Запрос</th><th class="r">В месяц</th><th>Группа</th></tr>${B.demand.map((d) => `<tr><td>${d[0]}</td><td class="r">${num(d[1])}</td><td>${d[2]}</td></tr>`).join('')}</table>`
  if (i === 2) return `<h3>Группы и объявления</h3><p class="muted">В каждой группе по три объявления — здесь показано первое.</p>
    ${B.groups.map((g) => {
      const phrases = B.demand.filter((d) => d[2] === g.name).map((d) => d[0])
      return `<div class="gcard"><header><b>${g.name}</b><span class="muted">${g.phrases} ${plural(g.phrases, 'фраза', 'фразы', 'фраз')} · 3 объявления</span></header>${adPrev(g)}
        ${pro() ? `<div class="chips">${phrases.map((x) => `<span class="chip">${x}</span>`).join('')}<span class="chip">ещё ${g.phrases - phrases.length}</span></div>
          <div class="gcard__acts"><button class="btn btn--sm" data-act="toast:Название группы меняется прямо на доске">${ic('edit', 14)}Переименовать</button><button class="btn btn--sm" data-act="toast:Выберите вторую группу — ИИ объединит фразы и объявления">Объединить</button><button class="btn btn--sm" data-act="toast:Отметьте фразы и выберите группу, куда их перенести">Перенести фразы</button></div>` : ''}</div>`
    }).join('')}`
  if (i === 3) {
    const phone = val('pv', 'desk') === 'phone'
    return `<div class="between"><h3>Как это увидят люди</h3>${segm('pv', [['desk', 'Компьютер'], ['phone', 'Телефон']], 'desk')}</div>
      <div class="pframe ${phone ? 'pframe--phone' : ''}"><div class="pframe__q">${ic('search', 15)}входные двери купить</div>${adPrev(B.groups[0])}
        <div class="chips">${B.links.slice(0, phone ? 2 : 4).map((l) => `<span class="chip">${l[0]}</span>`).join('')}</div>
        <p class="meta">${B.callouts.slice(0, phone ? 2 : 4).join(' · ')}</p></div>`
  }
  if (i === 4) return `<h3>Минус-фразы</h3><p class="muted">По запросам с этими словами объявления не покажутся.</p>
    <div class="chips">${B.minus.map((x) => `<span class="chip">${x}</span>`).join('')}<span class="chip">и ещё 28</span></div>
    ${pro() ? field('bminus', 'Добавить свои', '', { ph: 'Через запятую' }) : ''}`
  if (i === 5) return `<h3>Быстрые ссылки и уточнения</h3>
    <div class="links">${B.links.map((l) => `<div><b>${l[0]}</b><small>${l[1]}</small></div>`).join('')}</div>
    <h4>Уточнения</h4><div class="chips">${B.callouts.map((x) => `<span class="chip">${x}</span>`).join('')}</div>`
  if (i === 6) return `<h3>Картинки</h3><p class="muted">Нужны для показов в сетях. Для поиска этот шаг можно согласовать без картинок.</p>
    <div class="thumbs">${[1, 2, 3].map((n) => `<div>${ic('image', 26)}<small>Фото ${n}</small></div>`).join('')}</div>
    <div class="gcard__acts"><button class="btn btn--sm" data-act="toast:Откроется выбор файла">${ic('upload', 14)}Загрузить</button><button class="btn btn--sm" data-act="toast:Покажем картинки из других кампаний проекта">Из кампаний</button>${lockChip('Бизнес')}</div>
    <p class="meta">Генерация картинок ИИ доступна в тарифе «Бизнес».</p>`
  if (i === 7) return `<h3>Аудитории</h3><p class="muted">Кому показывать рекламу дополнительно.</p>
    <div class="card">${toggleRow('aud:1', 'Были на сайте, но не оставили заявку', 'Около 4 200 человек за 30 дней')}${toggleRow('aud:2', 'Похожие на ваших клиентов', 'Яндекс подберёт людей с похожим поведением')}</div>`
  if (i === 8) return `<h3>Настройки кампании</h3>${pro()
    ? `<div class="form">${B.settings.map((s, n) => field(`bset${n}`, s[0], s[1])).join('')}</div>`
    : `${dl(B.settings)}<p class="meta">ИИ подобрал их под цель и бюджет. Изменить можно словами в чате.</p>`}`
  return `<h3>Кампания готова</h3><p class="muted">Вот что появится в Директе после создания черновика.</p>
    <ul class="checklist">${B.ready.map((x) => `<li>${ic('check', 15)}${x}</li>`).join('')}</ul>`
}

function boardFoot() {
  const i = S.stage
  const d = S.stageDone
  const st = M.board.stages
  if (i < d) return `<div>${badge('ok', 'Шаг согласован')}<button class="btn btn--ghost btn--sm" data-act="reopen:${i}">${ic('undo', 14)}Вернуть на доработку</button></div>`
  if (i > d) return `<small>Сначала согласуйте шаг ${d + 1} «${st[d]}».</small>`
  if (i === 9) return on('draft')
    ? `<div>${badge('ok', 'Черновик создан в Директе')}<button class="btn" data-act="go:camps">Открыть кампании</button></div><small>На модерацию и в показ кампанию отправляете вы — в Директе или на странице кампании.</small>`
    : `<div><button class="btn btn--pri" data-act="draft">${ic('check', 16)}Создать черновик в Директе</button></div><small>Черновик сам не запустится: на модерацию его отправляете вы.</small>`
  return `<div><button class="btn btn--pri" data-act="approve">${ic('check', 16)}Согласовать шаг</button><button class="btn" data-act="toast:Напишите в чате, что изменить — ИИ пересоберёт этот шаг">Попросить переделать</button></div>
    <small>После согласования ИИ перейдёт к шагу ${i + 2} «${st[i + 1]}».</small>`
}

function board() {
  const B = M.board
  const d = S.stageDone
  const state = S.stage < d ? 'согласован' : S.stage === d ? 'ждёт вашего решения' : 'ещё не собран'
  return `<aside class="canvas">
    <header class="canvas__head"><div class="bname">${ic('megaphone', 16)}<b>${B.name}</b><span class="chip">Сборка кампании</span></div><button class="iconbtn" data-act="panel" aria-label="Закрыть панель">${ic('x')}</button></header>
    <div class="bprog"><div class="stages">${B.stages.map((s, i) => `<button class="${i < d ? 'done' : i === d ? 'cur' : ''} ${i === S.stage ? 'view' : ''}" data-act="stage:${i}" title="Шаг ${i + 1}. ${s}" aria-label="Шаг ${i + 1}. ${s}"></button>`).join('')}</div>
      <p><b>Шаг ${S.stage + 1} из 10 · ${B.stages[S.stage]}</b><span>${state}</span></p></div>
    <div class="canvas__body" data-scroll="canvas">${boardBody(S.stage)}</div>
    <footer class="canvas__foot">${boardFoot()}</footer></aside>`
}

function threadBuild() {
  const B = M.board
  const d = S.stageDone
  return thread(`<div class="msg-user"><div>Собери кампанию на входные двери</div></div>
    <div class="msg-ai">
      <p><b>Соберу по шагам.</b> На каждом покажу результат на доске справа и дождусь вашего согласия. В Директе ничего не появится, пока вы не создадите черновик на последнем шаге.</p>
      <div class="qcard card"><small>ИИ спросил</small><b>Какую цену заявки считаем хорошей?</b><p>${ic('check', 15)}<span>Вы ответили: до 600 ₽ · запомнено для проекта</span></p></div>
      <div class="plan card"><div class="ref__head"><span class="tag">${ic('sparkles', 13)}План</span><b>${B.name}</b></div>
        <ol>${B.stages.map((s, i) => `<li class="${i < d ? 'done' : i === d ? 'cur' : ''}"><button data-act="stage:${i}"><i>${i < d ? ic('check', 13) : i + 1}</i><span>${s}</span></button></li>`).join('')}</ol></div>
      <p><b>Шаг ${d + 1} из 10 · ${B.stages[d]}.</b> ${STAGE_MSG[d]}</p>
      <div class="refs"><button data-act="stage:${d}">${ic('panel', 15)}Открыть шаг на доске</button></div>
      ${aiMeta(4)}
    </div>`)
}

function chat() {
  const id = chatNow().id
  const th = M.threads[id]
  const left = id === 'c4' ? threadBuild() : id === 'c5' ? threadAll(th) : th ? threadTask(th) : threadAnalysis()
  const right = !hasCanvas() || !S.panel ? '' : id === 'c4' ? board() : th ? canvasList(th) : canvasAnalysis()
  return `<div class="chatwrap ${right ? 'has-canvas' : ''}">${left}${right}</div>`
}

Object.assign(ACT, {
  stage: (v) => {
    S.stage = +v
    S.panel = true
  },
  approve: () => {
    S.stageDone = Math.min(9, S.stageDone + 1)
    S.stage = S.stageDone
  },
  reopen: (v) => {
    S.stageDone = +v
    S.stage = +v
    S.flags.draft = false
  },
  draft: () => {
    S.flags.draft = true
    S.toast = 'Черновик создан в Директе. Запись появилась в «Истории»'
  },
})
