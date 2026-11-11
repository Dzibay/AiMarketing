/* Автопилот: правила, которые сами следят за цифрами, и эксперименты. Раздел только экспертного режима. */

const RULE_WHAT = ['фразы', 'кампании', 'запроса', 'объявления']
const RULE_METRIC = ['расход без заявок больше', 'цена заявки выше', 'CTR ниже', 'расход за день больше']
const RULE_PERIOD = ['за 7 дней', 'за 14 дней', 'за 30 дней']
const RULE_THEN = ['только уведомить', 'остановить', 'снизить ставку на 20%', 'добавить в минус-фразы']
/* Что показала бы проверка правила на вчерашних данных. */
const RULE_DRY = { r1: 'сработало бы на 2 фразах', r2: 'не нашло бы подходящих кампаний', r3: 'нашло бы 6 запросов' }

/** «Только проверка» у правила: значение из данных, которое переключатель переворачивает. */
const isDry = (r) => r.dry !== on(`dry:${r.id}`)
const ruleScope = () => ['Все проекты', ...M.projects.map((p) => p.name)]

function ruleRow(r) {
  const active = on(`rule:${r.id}`)
  const menu = S.menu === `rule-${r.id}` ? `<div class="menu menu--right">
    <button data-act="flag:dry:${r.id}">${ic(isDry(r) ? 'zap' : 'check', 16)}${isDry(r) ? 'Разрешить менять рекламу' : 'Перевести в «только проверку»'}</button>
    <button data-act="toast:В рабочем кабинете правило откроется в том же конструкторе">${ic('edit', 16)}Изменить условие</button>
    <hr><button data-act="flag:ruledel:${r.id}">${ic('trash', 16)}Удалить правило</button></div>` : ''
  return `<div class="rule ${active ? '' : 'is-off'}">${swBtn(`flag:rule:${r.id}`, active, `Правило «${r.name}»`)}
    <div><b>${esc(r.name)}</b>${isDry(r) ? '<span class="chip">Только проверка</span>' : ''}<p>${esc(r.text)}</p><small>${r.scope} · ${!active ? 'выключено, ничего не проверяет' : r.last ? `последний запуск ${r.last}` : 'первый запуск — завтра в 09:00'}</small></div>
    <div class="rule__acts"><button class="btn btn--sm" data-act="toast:«${attr(r.name)}» ${RULE_DRY[r.id] || 'сработало бы на 3 фразах'}. Ничего не изменено" title="Посчитать на вчерашних данных, ничего не меняя">Проверить</button>
      <div class="pop"><button class="iconbtn" data-act="menu:rule-${r.id}" aria-label="Действия с правилом">${ic('more')}</button>${menu}</div></div>
  </div>`
}

/** Конструктор правила: условие читается как предложение. */
function ruleBuilder() {
  const unit = val('rmetric', RULE_METRIC[0]).includes('CTR') ? '%' : '₽'
  return `<div class="card open pad builder"><h3 class="cap">Новое правило</h3>
    <div class="sentence"><span>Если у</span>${dd('rwhat', RULE_WHAT, RULE_WHAT[0])}${dd('rmetric', RULE_METRIC, RULE_METRIC[0])}
      <span class="inp inp--sm"><input data-k="rnum" value="${attr(val('rnum', '1500'))}" aria-label="Порог" style="width:64px"><i>${unit}</i></span>
      ${dd('rterm', RULE_PERIOD, RULE_PERIOD[1])}<span>—</span>${dd('rthen', RULE_THEN, RULE_THEN[0])}</div>
    <div class="form">${field('rname', 'Название', '', { ph: 'Например: дорогие фразы' })}${ddField('Где работает', dd('rscope', ruleScope(), S.project ? project(S.project).name : 'Все проекты'))}</div>
    <div class="card">${toggleRow('rdry', 'Сначала только проверять', 'Правило считает, что сделало бы, но рекламу не трогает. Удобно в первую неделю.')}</div>
    <div class="form__foot"><button class="btn btn--pri" data-act="ruleadd">Сохранить правило</button>
      <button class="btn" data-act="toast:На вчерашних данных правило сработало бы на 3 фразах. Ничего не изменено">Проверить на вчерашних данных</button>
      <button class="btn btn--ghost" data-act="flag:newrule">Отмена</button><small>Правила запускаются каждый день в 09:00 по данным за вчера.</small></div>
  </div>`
}

function autoRules() {
  const p = S.project && project(S.project)
  const list = M.rules.filter((r) => !on(`ruledel:${r.id}`) && (!p || r.scope === p.name || r.scope === 'Все проекты'))
  const names = list.map((r) => r.name)
  const runs = M.ruleRuns.filter((r) => names.includes(r[1]))
  return `${on('newrule') ? ruleBuilder() : ''}
    <h2>Правила <span>${list.length}</span><small>работают без ИИ — лимит не тратится</small></h2>
    ${list.length ? `<div class="card open">${list.map(ruleRow).join('')}</div>` : `<div class="card empty empty--sm">${ic('zap', 24)}<p>Для этого проекта правил пока нет.</p><div><button class="btn btn--sm" data-act="flag:newrule">Создать правило</button></div></div>`}
    <h2>Последние запуски<small><button class="lnk" data-act="set:hsrc=rule" data-then="history">Что правила изменили</button></small></h2>
    ${runs.length ? `<div class="card scroll-x"><table class="tbl"><tr><th>Когда</th><th>Правило</th><th>Что сделало</th></tr>
      ${runs.map((r) => `<tr><td class="num">${r[0]}</td><td><b>${r[1]}</b></td><td>${r[2]}</td></tr>`).join('')}</table></div>` : '<div class="card pad muted">Запусков ещё не было.</div>'}
    <p class="meta">Правило с пометкой «Только проверка» ничего не меняет в Директе — только пишет, что нашло.</p>`
}

function expCard(e) {
  const head = (st) => `<header><div><small>${e.project}</small><h3>${e.name}</h3></div>${st}</header><p>${e.hyp}</p>`
  if (e.state === 'done') {
    return `<article class="exp card pad">${head('<span class="chip">Завершён</span>')}
      ${dl([['Цель', e.goal], ['Лимит расхода', e.limit], ['Срок', e.term]])}<p class="verdict">${ic('check', 16)}<span><b>Вывод.</b> ${e.verdict}</span></p></article>`
  }
  const stopped = on('expstop')
  const row = (r, note) => `<tr><td><b>${r[0]}</b><small>${note}</small></td><td class="r">${r[1]}</td><td class="r">${r[2]}</td><td class="r"><b>${r[3]}</b></td></tr>`
  return `<article class="exp card pad">${head(stopped ? '<span class="chip">Остановлен досрочно</span>' : badge('ok', 'Идёт · день 5 из 14'))}
    ${dl([['Цель', e.goal], ['Лимит расхода', `${e.limit} · потрачено ${e.test[1]}`], ['Срок', e.term]])}
    <div class="scroll-x"><table class="tbl num"><tr><th>Кампания</th><th class="r">Расход</th><th class="r">Заявки</th><th class="r">Цена заявки</th></tr>
      ${row(e.test, 'копия с ценой в заголовке')}${row(e.base, 'исходная кампания, без изменений')}</table></div>
    <p class="muted">${stopped ? 'Тестовая кампания на паузе, образец работает как раньше. Данных мало, поэтому вывода нет.' : 'Пока рано судить: 11 заявок на двоих — разница может быть случайной. Итог будет на 14-й день или когда тест потратит весь лимит.'}</p>
    <div class="acts">${stopped
      ? `<button class="btn btn--sm" data-act="flag:expstop">${ic('play', 14)}Продолжить</button>`
      : '<button class="btn btn--sm" data-act="expstop">Завершить досрочно</button>'}
      <button class="btn btn--ghost btn--sm" data-act="chat:c1">${ic('sparkles', 14)}Обсудить с ИИ</button></div>
  </article>`
}

function autoExp() {
  const p = S.project && project(S.project)
  const list = M.experiments.filter((e) => !p || e.project === p.name)
  return `<h2>Эксперименты <span>${list.length}</span><small>тест идёт на копии кампании, образец не меняется</small></h2>
    ${list.length ? list.map(expCard).join('') : `<div class="card empty empty--sm">${ic('sparkles', 24)}<p>В этом проекте экспериментов ещё не было.</p></div>`}
    <p class="meta">Каждый эксперимент ограничен по деньгам и по сроку. Когда лимит или срок выйдет, тестовая кампания остановится сама.</p>`
}

function auto() {
  const tab = sub('auto', 'rules')
  return `<div class="page" data-scroll="main"><div class="wide">
    <div class="head"><div><p class="eyebrow">${projectName(S.project)} · экспертный режим</p><h1>Автопилот</h1></div>
      <div class="head__acts">${tab === 'exp'
        ? `<button class="btn btn--pri" data-act="toast:ИИ подберёт гипотезу по вашим цифрам и пришлёт её в чат на согласование. Это потратит около 2% лимита">${ic('sparkles', 15)}Предложить эксперимент</button>`
        : `<button class="btn btn--pri" data-act="flag:newrule">${ic('plus', 15)}Новое правило</button>`}</div></div>
    ${ptabs('auto', [['rules', 'Правила'], ['exp', 'Эксперименты']], 'rules')}
    ${tab === 'exp' ? autoExp() : autoRules()}
  </div></div>`
}

Object.assign(ACT, {
  ruleadd: () => {
    const f = (k, def) => String(val(k, def)).trim()
    const unit = f('rmetric', RULE_METRIC[0]).includes('CTR') ? '%' : ' ₽'
    const id = `r${M.rules.length + 1}`
    M.rules.push({
      id,
      name: f('rname', '') || 'Новое правило',
      text: `Если у ${f('rwhat', RULE_WHAT[0])} ${f('rmetric', RULE_METRIC[0])} ${f('rnum', '1500') || '0'}${unit} ${f('rterm', RULE_PERIOD[1])} — ${f('rthen', RULE_THEN[0])}`,
      scope: f('rscope', S.project ? project(S.project).name : 'Все проекты'),
      last: null,
      dry: on('rdry'),
    })
    S.flags[`rule:${id}`] = true
    S.flags.newrule = false
    S.flags.rdry = false
    ;['rname', 'rnum', 'rwhat', 'rmetric', 'rterm', 'rthen', 'rscope'].forEach((k) => delete S.form[k])
    S.toast = 'Правило сохранено. Первый запуск — завтра в 09:00'
  },
  expstop: () => {
    S.flags.expstop = true
    S.toast = 'Эксперимент остановлен: тестовая кампания на паузе. Запись появилась в «Истории»'
  },
})
