/* История: всё, что меняли в Директе — человек, ИИ с согласия и правила. Любое изменение можно вернуть. */

/* Как выполненное предложение или сигнал называется в истории. */
const TASK_HIST = {
  t3: 'Снижены ставки в 3 группах на 15%',
  t4: 'Исключены 14 площадок РСЯ без заявок',
  t5: 'Добавлены 9 минус-фраз',
  t6: 'Остановлена кампания «Окна ПВХ — РСЯ»',
  t7: '2 объявления исправлены и отправлены на модерацию',
}
const HPERIODS = ['За 7 дней', 'За 30 дней', 'За всё время']
const HSRC = [['all', 'Все'], ['ai', 'ИИ'], ['user', 'Вы'], ['rule', 'Правила']]

/** Записи истории: готовые из данных и то, что человек сделал в макете сам. */
function historyEntries() {
  const now = { day: 'Сегодня', time: 'только что' }
  const out = S.log.map((l) => ({ ...now, id: `l:${l.id}`, who: 'user', project: l.project, title: l.title, params: [['Где', l.project ? project(l.project).name : 'Все проекты']], result: 'Директ принял изменение', undo: 'Вернём то, что было до этого изменения. Директ получит прежние значения сразу.' }))
  M.tasks.filter((t) => S.decided[t.id] === 'done' && TASK_HIST[t.id]).forEach((t) => out.push({
    ...now, id: `t:${t.id}`, who: t.group === 'ai' ? 'ai' : 'user', project: t.project, title: TASK_HIST[t.id],
    params: t.diff ? t.diff.map((d) => [d[0], `${d[1]} → ${d[2]}`]) : [['Основание', t.text]],
    result: 'Директ принял изменение', undo: 'Вернём то, что было до этого изменения. Директ получит прежние значения сразу.',
  }))
  Object.keys(M.reasons).filter((k) => S.minus.has(k)).forEach((k) => {
    const r = M.reasons[k]
    out.push({ ...now, id: `m:${k}`, who: 'user', project: 'okna', title: `Добавлены минус-фразы: «${r.label}», ${r.count} ${words(r.count)}`, params: [['Группа запросов', r.label], ['Расход за неделю', rub(r.cost)], ['Уровень', 'кампания']], result: `Директ принял все фразы: ${r.count}`, undo: `Уберём эти минус-фразы. Объявления снова смогут показываться по ${r.count} ${plural(r.count, 'запросу', 'запросам', 'запросам')}.` })
  })
  M.campaigns.filter((c) => c.was && !c.on && !c.deleted && !c.archived).forEach((c) => out.push({ ...now, id: `c:${c.id}`, who: 'user', project: c.project, title: `Остановлена кампания «${c.name}»`, params: [['Кампания', c.name]], result: 'Показы остановлены', undo: 'Запустим кампанию снова.' }))
  S.gone.forEach((g) => out.push(g))
  M.history.forEach((h) => out.push({ ...h, id: `h:${h.id}` }))
  return out
}

function hrow(e) {
  const undone = e.undone || on(`undone:${e.id}`)
  const p = e.project && project(e.project)
  const open = pro() && val('hopen', '') === e.id
  return `<div class="hrow ${undone ? 'is-undone' : ''}"><i class="hrow__who" title="${WHO[e.who][1]}">${ic(WHO[e.who][0], 16)}</i>
    <div><b>${e.title}</b><small>${e.time} · ${WHO[e.who][1]}${p && !S.project ? ` · ${p.name}` : ''}</small>
      ${open ? `${dl(e.params)}<p class="meta">Ответ Директа: ${e.result}.</p>` : ''}</div>
    <div class="hrow__acts">${pro() ? `<button class="btn btn--ghost btn--sm" data-act="set:hopen=${open ? '' : e.id}">${open ? 'Свернуть' : 'Подробнее'}</button>` : ''}
      ${undone ? `<span class="st st--off">${ic('undo', 13)}Возвращено</span>` : e.undo ? `<button class="btn btn--sm" data-act="modal:undo:${e.id}">${ic('undo', 14)}Вернуть как было</button>` : ''}</div>
  </div>`
}

function historyPage() {
  const src = pro() ? val('hsrc', 'all') : 'all'
  const per = pro() ? val('hper', HPERIODS[1]) : HPERIODS[1]
  const all = historyEntries().filter((e) => !S.project || e.project === S.project || !e.project)
  const list = all.filter((e) => (src === 'all' || e.who === src) && (per !== HPERIODS[0] || e.day !== '3 ноября'))
  const days = [...new Set(list.map((e) => e.day))]
  return `<div class="page" data-scroll="main"><div class="wide wide--form">
    <div class="head"><div><p class="eyebrow">${projectName(S.project)}</p><h1>История</h1></div>
      ${pro() ? `<div class="head__acts">${dd('hper', HPERIODS, HPERIODS[1])}</div>` : ''}</div>
    <p class="lead"><span>Всё, что меняли в Директе: вы, ИИ с вашего согласия и правила. Любое изменение можно вернуть одной кнопкой.</span></p>
    ${pro() ? `<div class="fbar">${segm('hsrc', HSRC, 'all')}<span class="muted">${list.length} ${plural(list.length, 'запись', 'записи', 'записей')}</span></div>` : ''}
    ${days.map((d) => `<h2>${d}</h2><div class="card">${list.filter((e) => e.day === d).map(hrow).join('')}</div>`).join('')}
    ${list.length ? '' : `<div class="card empty empty--sm">${ic('history', 24)}<p>${all.length ? 'Под этот фильтр записей нет.' : 'В этом проекте ещё ничего не меняли.'}</p>${all.length ? '<div><button class="btn btn--sm" data-act="hreset">Показать все</button></div>' : ''}</div>`}
    ${expertHint('Кто сделал изменение, с какими параметрами и что ответил Директ')}
  </div></div>`
}

/** Действие с пометкой про «Историю» оставляет в ней запись. Вызывается после каждого клика. */
function noteHistory(name, arg, flagsBefore) {
  const t = S.toast
  if (!t || !t.includes('Истори')) return
  /* У этих действий своя запись: её собирает historyEntries из состояния. */
  if (['done', 'toggle', 'minusall', 'undoh'].includes(name) || (name === 'bulk' && (arg === 'stop' || arg === 'start'))) return
  const changed = Object.keys(S.flags).filter((k) => S.flags[k] !== flagsBefore[k])
  const own = S.revert
  const c = S.screen === 'camp' && M.campaigns.find((x) => x.id === S.camp)
  S.log.unshift({
    id: String(S.log.length + 1),
    title: t.replace(/[.:]?\s*(Вернуть( как было)? можно|Запись появилась) в «Истории».*$/, ''),
    project: c ? c.project : S.project,
    revert: () => {
      changed.forEach((k) => (S.flags[k] = flagsBefore[k]))
      if (own) own()
    },
  })
}

Object.assign(MODALS, {
  undo: (id) => {
    const e = historyEntries().find((x) => x.id === id)
    if (!e) return { title: 'Запись не найдена', body: '<p>Похоже, это изменение уже вернули.</p>', foot: '<button class="btn" data-act="close">Закрыть</button>' }
    return {
      title: 'Вернуть как было?',
      body: `<p><b>${e.title}</b></p><p>${e.undo}</p><p class="muted">Возврат тоже попадёт в историю. Статистика за прошедшие дни не изменится.</p>`,
      foot: `<button class="btn btn--pri" data-act="undoh:${id}" data-close>Вернуть</button><button class="btn" data-act="close">Отмена</button>`,
    }
  },
})

Object.assign(ACT, {
  undoh: (id) => {
    const e = historyEntries().find((x) => x.id === id)
    if (!e) return
    const i = id.indexOf(':')
    const kind = id.slice(0, i)
    const key = id.slice(i + 1)
    if (kind === 'h' || kind === 'l') {
      S.flags[`undone:${id}`] = true
      const l = kind === 'l' && S.log.find((x) => x.id === key)
      if (l) l.revert()
    } else {
      /* Запись собрана из состояния: сохраняем её снимок и возвращаем само состояние. */
      S.gone.unshift({ ...e, id: `g:${S.gone.length + 1}`, undone: true })
      if (kind === 't') delete S.decided[key]
      if (kind === 'm') S.minus.delete(key)
      if (kind === 'c') ACT.toggle(key)
    }
    S.toast = 'Возвращено как было'
  },
  hreset: () => {
    delete S.form.hsrc
    delete S.form.hper
  },
})
