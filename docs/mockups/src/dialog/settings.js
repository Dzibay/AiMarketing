/* Настройки: проект, подключения, аккаунт и тариф. В обычном режиме — только то, без чего не обойтись. */

/* Вкладки настроек проекта: id, иконка, название, только для экспертного режима. */
const PTABS = [
  ['main', 'sliders', 'Основное'], ['biz', 'file', 'О бизнесе'], ['ai', 'sparkles', 'Помощник'],
  ['access', 'lock', 'Доступ ИИ', 1], ['mem', 'archive', 'Что ИИ запомнил', 1], ['cabs', 'megaphone', 'Кабинеты и кампании', 1], ['utm', 'link', 'Шаблон ссылки', 1], ['client', 'folder', 'Клиент', 1],
]
const STABS = [['profile', 'user', 'Профиль'], ['notif', 'bell', 'Уведомления'], ['ui', 'sliders', 'Интерфейс']]
const BIZ = {
  okna: 'Пластиковые окна и остекление балконов в Москве и области. Частные клиенты. Замер бесплатно, установка за один день.',
  msk: 'Те же окна, отдельный сайт для Москвы: выезд замерщика в день обращения.',
  dent: 'Стоматология в Казани: имплантация, отбеливание, детский приём. Первичная консультация бесплатно.',
  kofe: 'Интернет-магазин кофе в зёрнах и капсулах. Доставка по России, обжарка каждую неделю.',
}
const GOALS = [['Заявка с формы', '412 за 30 дней'], ['Звонок с сайта', '96 за 30 дней'], ['Заказ обратного звонка', '54 за 30 дней'], ['Переход в мессенджер', '38 за 30 дней']]
const UTM = 'utm_source=yandex&utm_medium=cpc&utm_campaign={campaign_id}&utm_content={ad_id}&utm_term={keyword}'
const MODE_SIMPLE = ['Главная: что ждёт решения и итоги недели простыми словами', 'Чат с ИИ: вопрос, ответ и кнопка «Выполнить»', 'Кампании по состоянию: тратят впустую, дороже цели, работают хорошо', 'Запросы: мусор группами, одно решение на группу', 'Отчёты: три цифры и два графика', 'История с кнопкой «Вернуть как было»']
const MODE_EXPERT = ['Поиск, фильтры и массовые действия в кампаниях', 'Группы, объявления, фразы, ставки и настройки кампании', 'Создание кампании вручную и тест заголовков', 'Таблица запросов с клавишами, память решений, списки минус-фраз', 'Отчёты по площадкам и объявлениям, сравнение периодов', 'Автопилот: правила и эксперименты', 'Права ИИ, шаблон ссылки, кабинеты и синхронизация']

/** Боковые вкладки настроек. Экспертная вкладка в обычном режиме не видна — открывается первая. */
function snav(k, list) {
  const shown = list.filter((t) => pro() || !t[3])
  const cur = shown.some((t) => t[0] === sub(k, '')) ? sub(k, '') : shown[0][0]
  return { cur, html: `<nav class="snav">${shown.map(([id, icon, label]) => `<button class="${cur === id ? 'active' : ''}" data-act="sub:${k}=${id}">${ic(icon, 16)}<span>${label}</span></button>`).join('')}</nav>` }
}
const sec = (title, text, body, cls = '') => `<section class="sec card open pad ${cls}"><h3>${title}</h3>${text ? `<p class="muted">${text}</p>` : ''}${body}</section>`
const saveBar = (text = 'Настройки сохранены') => `<div class="form__foot"><button class="btn btn--pri" data-act="toast:${text}">Сохранить</button></div>`
/** Строка списка: название с пояснением слева, действия справа. */
const lrow = (title, text, acts, icon = '') => `<div class="lrow">${icon ? `<i class="lrow__ic">${ic(icon, 16)}</i>` : ''}<div><b>${title}</b>${text ? `<small>${text}</small>` : ''}</div><div class="lrow__acts">${acts}</div></div>`
/** Выключатель, который включён по умолчанию: флаг хранит «выключено». */
const offRow = (flag, title, text) => `<div class="trow"><div><b>${title}</b>${text ? `<p>${text}</p>` : ''}</div>${swBtn(`flag:${flag}`, !on(flag), title)}</div>`
const delBtn = (flag, label) => `<button class="iconbtn" data-act="flag:${flag}" aria-label="${attr(label)}" title="${attr(label)}">${ic('trash', 16)}</button>`

/* ---------- настройки проекта ---------- */

function psMain(p) {
  const k = (x) => `${p.id}.${x}`
  const kind = val(k('kpi'), isDrr(p) ? 'drr' : 'cpa')
  const same = kind === (isDrr(p) ? 'drr' : 'cpa')
  return `${sec('Цель рекламы', 'По этой цели сервис решает, что работает хорошо, а что требует внимания.', `<div class="form">
      ${ddField('Что считаем результатом', segm(k('kpi'), [['cpa', 'Заявки'], ['drr', 'Заказы магазина']], isDrr(p) ? 'drr' : 'cpa'), kind === 'drr' ? 'Следим за долей расходов на рекламу в выручке.' : 'Следим за ценой одной заявки.')}
      ${field(k(`target-${kind}`), kind === 'drr' ? 'Доля расходов на рекламу — не выше' : 'Цена заявки — не выше', same ? num(p.target) : '', { unit: kind === 'drr' ? '%' : '₽', w: 90, ph: 'Не задана' })}
      ${field(k('budget'), 'Бюджет на месяц', num(p.budget), { unit: '₽', w: 110, hint: 'Сервис предупредит, если деньги уходят быстрее плана.' })}
    </div>`)}
    ${sec('Что считать заявкой', 'Цели из Метрики. Отмеченные идут в «заявки» во всём кабинете.', `<div class="card">${GOALS.map((g, i) => `<div class="trow"><div><b>${g[0]}</b><p>${g[1]}</p></div>${swBtn(`flag:goal:${p.id}:${i}`, i < 2 !== on(`goal:${p.id}:${i}`), g[0])}</div>`).join('')}</div>`)}
    ${saveBar('Настройки проекта сохранены')}
    ${pro() ? sec('Отключить сайт', 'Сервис перестанет загружать данные и считать сигналы. Кампании в Директе продолжат работать как сейчас.', `<div class="acts acts--tight"><button class="btn btn--danger btn--sm" data-act="toast:В макете сайт отключить нельзя">Отключить ${p.name}</button></div>`) : ''}`
}

function psBiz(p) {
  const k = (x) => `${p.id}.${x}`
  const files = M.aiFiles.map((f, i) => [f, i]).filter(([, i]) => !on(`filedel:${i}`))
  return `${sec('О бизнесе', 'ИИ читает это перед каждым ответом — чем точнее, тем меньше придётся поправлять.', `<div class="form form--one">
      ${field(k('about'), 'Что вы продаёте и кому', BIZ[p.id], { area: true, rows: 3 })}
      ${field(k('usp'), 'Чем вы лучше других', '', { area: true, rows: 2, ph: 'Например: своё производство, гарантия 5 лет, оплата после монтажа' })}
      ${field(k('stop'), 'О чём не писать и что не предлагать', '', { area: true, rows: 2, ph: 'Например: не обещать скидки, не упоминать рассрочку' })}
    </div>`)}
    ${sec('Файлы для ИИ', 'Прайс, сценарий звонка, описание акций. ИИ заглядывает в них, когда пишет объявления и отвечает на вопросы.', `<div class="card">${files.length
      ? files.map(([f, i]) => lrow(f[0], f[1], delBtn(`filedel:${i}`, `Удалить файл ${f[0]}`), 'file')).join('')
      : '<p class="pad muted">Файлов пока нет.</p>'}</div>
      <div class="acts acts--tight"><button class="btn btn--sm" data-act="toast:В рабочем кабинете откроется выбор файла: PDF, DOCX или TXT до 10 МБ">${ic('upload', 14)}Загрузить файл</button></div>`)}
    ${saveBar('Описание бизнеса сохранено')}`
}

function psAi(p) {
  const k = (x) => `${p.id}.${x}`
  return `${sec('Как ИИ пишет объявления', '', `<div class="form">
      ${ddField('Тон', segm(k('tone'), [['calm', 'Спокойный'], ['sell', 'Продающий'], ['expert', 'Экспертный']], 'calm'))}
      ${ddField('Обращение', segm(k('you'), [['vy', 'На «вы»'], ['ty', 'На «ты»']], 'vy'))}
    </div>`)}
    ${sec('Какой моделью отвечать', 'Чем больше точек, тем умнее ответ и тем больше лимита он тратит. В любом чате модель можно сменить.', segm(k('model'), M.models.map((m, i) => [String(i), `${m[0]}${dots(m[1])}`]), '1'))}
    ${sec('ИИ ничего не меняет сам', 'Каждое изменение ждёт вашего «Выполнить». Всё выполненное можно вернуть в «Истории».', `<div class="acts acts--tight"><button class="btn btn--sm" data-act="go:history">${ic('history', 14)}Открыть историю</button></div>`)}
    ${saveBar('Настройки помощника сохранены')}`
}

function psAccess(p) {
  return `${sec('Что ИИ может делать без согласования', 'По умолчанию всё выключено: ИИ только предлагает, а вы решаете.', `<div class="card">
      ${toggleRow(`acc:minus:${p.id}`, 'Добавлять минус-фразы', 'Только запросы без единой заявки за 30 дней')}
      ${toggleRow(`acc:bids:${p.id}`, 'Менять ставки в пределах 20%', 'Не чаще одного раза в сутки на группу')}
      ${toggleRow(`acc:stop:${p.id}`, 'Останавливать фразы и объявления без заявок', 'Кампании целиком ИИ не останавливает никогда')}
    </div><p class="meta">Создание кампаний, бюджеты и стратегии всегда идут через согласование.</p>`)}
    ${sec('Платные тесты', 'Эксперименты и тесты заголовков тратят деньги в Директе, поэтому у них есть потолок.', `<div class="form">${field(`${p.id}.testcap`, 'Не больше в неделю', '6 000', { unit: '₽', w: 90, hint: 'Не может быть больше недельного бюджета кампаний в Директе.' })}</div>`)}
    ${saveBar('Доступ ИИ сохранён')}`
}

function psMem() {
  const list = M.aiMemory.map((t, i) => [t, i]).filter(([, i]) => !on(`memdel:${i}`))
  return sec('Что ИИ запомнил', 'ИИ записывает ваши поправки и решения из чатов, чтобы не спрашивать дважды. Лишнее можно убрать.', `<div class="card">${list.length
    ? list.map(([t, i]) => lrow(t, '', delBtn(`memdel:${i}`, 'Забыть'))).join('')
    : '<p class="pad muted">Пока ничего: память появится после первых поправок в чате.</p>'}</div>
    <div class="irow">${field('memnew', 'Добавить вручную', '', { ph: 'Например: не показывать рекламу ночью' })}<button class="btn" data-act="toast:ИИ запомнил. Это учтётся со следующего ответа">Запомнить</button></div>`)
}

function psCabs(p) {
  const list = M.campaigns.filter((c) => c.project === p.id && !c.deleted)
  return sec('Кабинеты и кампании проекта', 'Сервис сам подбирает кампании по адресу сайта в объявлениях. Здесь можно поправить вручную.', `
    <p class="lead"><span class="chip">${ic('megaphone', 14)}Кабинет Директа: ${CAB[p.id]}</span></p>
    <div class="card scroll-x"><table class="tbl"><tr><th>Кампания</th><th>Тип</th><th class="r">Считать в проекте</th></tr>
      ${list.map((c) => `<tr><td><b>${c.name}</b></td><td>${c.type}</td><td class="r">${swBtn(`flag:campout:${c.id}`, !on(`campout:${c.id}`), `Считать «${c.name}» в проекте`)}</td></tr>`).join('')}</table></div>
    <div class="acts acts--tight"><button class="btn btn--sm" data-act="go:connect">${ic('plug', 14)}Все кабинеты и счётчики</button></div>`)
}

function psUtm(p) {
  const rows = p.id === 'okna' ? M.linkCheck : M.campaigns.filter((c) => c.project === p.id && !c.deleted).map((c) => [c.name, true])
  return `${sec('Шаблон ссылки', 'Метки для Метрики и вашей CRM. Шаблон записывается в параметры отслеживания кампании — сами ссылки в объявлениях не меняются.', `<div class="form form--one">${field(`${p.id}.utm`, 'Параметры', UTM, { area: true, rows: 2, hint: 'Подставятся сами: {campaign_id}, {ad_id}, {keyword}.' })}</div>
    <div class="acts acts--tight"><button class="btn btn--pri btn--sm" data-act="toast:Шаблон сохранён. Новые кампании получат его сразу">Сохранить шаблон</button></div>`)}
    ${sec('Проверка кампаний', 'У каких кампаний параметры совпадают с шаблоном.', `<div class="card">${rows.map((r, i) => {
      const ok = r[1] || on(`utmfix:${p.id}:${i}`)
      return lrow(r[0], '', ok ? badge('ok', 'По шаблону') : `${badge('warn', 'Свои метки')}<button class="btn btn--sm" data-act="utmfix:${p.id}:${i}">Привести к шаблону</button>`)
    }).join('')}</div>`)}`
}

function psClient(p) {
  const clients = [...new Set(M.projects.map((x) => x.client).filter(Boolean)), 'Без клиента']
  return sec('Клиент', 'Клиент — это папка над проектами. Удобно, если ведёте несколько бизнесов или работаете как агентство.', `<div class="form">
      ${ddField('Папка клиента', dd(`${p.id}.client`, clients, p.client || 'Без клиента'))}
      ${field('clientnew', 'Или новая папка', '', { ph: 'Название клиента' })}
    </div>${saveBar('Проект перенесён в папку клиента')}`)
}

function projectSettings() {
  const p = S.project && project(S.project)
  if (!p) {
    return `<div class="page" data-scroll="main"><div class="wide">
      <div class="head"><div><p class="eyebrow">Все проекты</p><h1>Настройки проекта</h1></div></div>
      <div class="card empty empty--sm">${ic('folder', 24)}<p>У каждого сайта свои цель, бюджет и описание бизнеса. Выберите сайт.</p><div>${M.projects.map((x) => `<button class="btn btn--sm" data-act="project:${x.id}">${x.name}</button>`).join('')}</div></div>
    </div></div>`
  }
  const nav = snav('project', PTABS)
  const body = { main: psMain, biz: psBiz, ai: psAi, access: psAccess, mem: psMem, cabs: psCabs, utm: psUtm, client: psClient }[nav.cur](p)
  return `<div class="page" data-scroll="main"><div class="wide">
    <div class="head"><div><p class="eyebrow">${p.name}${pro() && p.client ? ` · ${p.client}` : ''}</p><h1>Настройки проекта</h1></div></div>
    <div class="set">${nav.html}<div class="set__body">${body}${expertHint('Права ИИ, память помощника, кабинеты, шаблон ссылки и папка клиента')}</div></div>
  </div></div>`
}

/* ---------- подключения ---------- */

function connect() {
  const max = 5
  const free = M.counters.filter((c) => !c[2])
  const count = M.projects.length
  const addBox = on('addsite') ? `<div class="card open pad"><h3 class="cap">Какой сайт добавить?</h3>
    <p class="muted">Это счётчики Метрики из вашего аккаунта Яндекса, которые ещё не подключены. На тарифе «${M.plan.name}» можно ещё ${max - count}.</p>
    <div class="card">${free.map((c) => lrow(c[0], `счётчик ${c[1]}`, `<button class="btn btn--sm" data-act="toast:${c[0]} добавлен. Загружаем кампании и статистику — обычно это занимает около минуты" data-then="connect">Добавить</button>`)).join('')}</div>
    <div class="acts acts--tight"><button class="btn btn--ghost btn--sm" data-act="flag:addsite">Отмена</button></div></div>` : ''
  const expert = `<h2>Счётчики Метрики <span>${M.counters.length}</span></h2>
    <div class="card scroll-x"><table class="tbl"><tr><th>Сайт</th><th>Счётчик</th><th>Проект</th><th></th></tr>
      ${M.counters.map((c) => `<tr><td><b>${c[0]}</b></td><td class="num">${c[1]}</td><td>${c[2] ? badge('ok', 'Подключён') : '<span class="muted">Не подключён</span>'}</td><td class="r">${c[2] ? `<button class="btn btn--ghost btn--sm" data-act="at:${c[2]}=project">Настройки</button>` : `<button class="btn btn--sm" data-act="toast:${c[0]} добавлен. Загружаем кампании и статистику — обычно это занимает около минуты">Добавить</button>`}</td></tr>`).join('')}</table></div>
    <h2>Кабинеты Директа <span>${M.cabinets.length}</span><small>за 7 дней</small></h2>
    <div class="card scroll-x"><table class="tbl num"><tr><th>Логин</th><th class="r">Кампании</th><th class="r">Баланс</th><th class="r">Расход</th><th class="r">Конверсии</th></tr>
      ${M.cabinets.map((c) => `<tr><td><b>${c.login}</b></td><td class="r">${c.camps}</td><td class="r">${rub(c.balance)}</td><td class="r">${rub(c.cost)}</td><td class="r">${c.conv}</td></tr>`).join('')}</table></div>
    <div class="acts acts--tight"><button class="btn btn--sm" data-act="toast:В рабочем кабинете откроется вход в Яндекс под другим логином">${ic('plus', 14)}Добавить кабинет</button></div>
    <h2>Яндекс Аудитории</h2>
    <div class="card">${lrow('Аудитории', 'Нужны, чтобы показывать рекламу похожим на ваших клиентов и не показывать тем, кто уже купил. Подключаются отдельным разрешением Яндекса.',
      on('audoff') ? `<span class="muted">Не подключены</span><button class="btn btn--sm" data-act="flag:audoff">Подключить</button>` : `${badge('ok', 'Подключены')}<button class="btn btn--ghost btn--sm" data-act="flag:audoff">Отключить</button>`, 'user')}</div>
    <h2>История обновлений</h2>
    <div class="card scroll-x"><table class="tbl"><tr><th>Когда</th><th>Что загружали</th><th>Результат</th></tr>
      ${M.syncs.map((s) => `<tr><td class="num">${s[0]}</td><td>${s[1]}</td><td>${badge('ok', s[2])}</td></tr>`).join('')}</table></div>
    <div class="acts acts--tight"><button class="btn btn--sm" data-act="toast:Полная загрузка запущена: обычно занимает 5–7 минут. Работать можно как обычно">${ic('refresh', 14)}Загрузить всё заново</button></div>`
  return `<div class="page" data-scroll="main"><div class="wide wide--form">
    <div class="head"><div><p class="eyebrow">Откуда сервис берёт данные</p><h1>Подключения</h1></div></div>
    <div class="card">${lrow(`Яндекс: ${M.user.yandex}`, 'Доступ к Директу и Метрике. Сервис читает статистику, а менять рекламу может только после вашего «Выполнить».',
      `${badge('ok', 'Подключён')}<button class="btn btn--ghost btn--sm" data-act="toast:В рабочем кабинете откроется вход в Яндекс">Переподключить</button>`, 'plug')}
      ${lrow('Данные обновлены 10 минут назад', 'Обновляются сами каждые полчаса. Поисковые запросы — раз в сутки.', `<button class="btn btn--sm" data-act="toast:Обновляем данные — обычно это занимает около минуты">${ic('refresh', 14)}Обновить сейчас</button>`, 'refresh')}</div>
    <h2>Сайты <span>${count}</span><small>тариф «${M.plan.name}»: ${count} из ${max}</small></h2>
    <div class="card">${M.projects.map((p) => lrow(`<span class="dot dot--${p.status}"></span>${p.name}`, `${pro() ? `счётчик ${(M.counters.find((c) => c[2] === p.id) || [])[1]} · ` : ''}${p.camps} ${plural(p.camps, 'кампания', 'кампании', 'кампаний')}${p.client ? ` · ${p.client}` : ''}`,
      `<button class="btn btn--ghost btn--sm" data-act="at:${p.id}=project">${ic('gear', 14)}Настройки</button>`)).join('')}</div>
    ${addBox || `<div class="acts acts--tight"><button class="btn btn--sm" data-act="flag:addsite">${ic('plus', 14)}Добавить сайт</button></div>`}
    ${pro() ? expert : expertHint('Счётчики Метрики, кабинеты Директа, Яндекс Аудитории и история обновлений')}
  </div></div>`
}

/* ---------- настройки аккаунта ---------- */

function setProfile() {
  return `${sec('Профиль', '', `<div class="form">
      ${field('uname', 'Имя', M.user.name)}
      ${field('umail', 'Почта', M.user.email, { hint: 'Подтверждена. Сюда приходят дайджест и чеки.' })}
    </div>${saveBar('Профиль сохранён')}`)}
    ${sec('Вход', '', `<div class="card">
      ${lrow('Пароль', 'Меняли 3 месяца назад', '<button class="btn btn--sm" data-act="toast:Письмо со ссылкой для смены пароля отправлено на почту">Сменить пароль</button>', 'lock')}
      ${lrow('Устройства', 'Сейчас вход выполнен на 2 устройствах', '<button class="btn btn--sm" data-act="toast:Вы вышли на всех устройствах, кроме этого">Выйти на остальных</button>', 'monitor')}
    </div>`)}
    ${sec('Удаление аккаунта', 'Сотрём данные кабинета и отключим доступ к Яндексу. Кампании в Директе останутся как есть.', '<div class="acts acts--tight"><button class="btn btn--danger btn--sm" data-act="toast:В макете аккаунт удалить нельзя">Удалить аккаунт</button></div>')}`
}

function setNotif() {
  return `${sec('Что присылать', 'Дайджест собирается из цифр, без ИИ — лимит не тратится.', `<div class="card">
      ${toggleRow('digest', 'Утренний дайджест на почту', 'Раз в день: расход, заявки и что требует внимания')}
      ${toggleRow('tg', 'Дайджест в Telegram', 'То же самое сообщением от бота')}
      ${offRow('nourgent', 'Срочное — сразу', 'Закончились деньги, реклама остановилась, объявление отклонила модерация')}
      ${offRow('noplan', 'Напоминания о тарифе', 'За 3 дня до окончания и когда лимит ИИ подходит к концу')}
      ${pro() ? offRow('norules', 'Срабатывания правил автопилота', 'Что правило изменило или нашло в режиме проверки') : ''}
    </div>`)}
    ${sec('Telegram', '', `<div class="card">${lrow('@anton_okna', 'Бот пишет в личные сообщения', `${badge('ok', 'Подключён')}<button class="btn btn--ghost btn--sm" data-act="toast:Telegram отключён. Дайджест останется на почте">Отключить</button>`, 'send')}</div>`)}
    ${pro() ? sec('Когда присылать дайджест', '', `<div class="form">${ddField('Время', dd('digtime', ['08:00', '09:00', '10:00', '12:00'], '09:00'), 'По московскому времени.')}</div>`) : ''}`
}

function setUi() {
  const list = (items) => `<ul class="checklist">${items.map((t) => `<li>${ic('check', 15)}<span>${t}</span></li>`).join('')}</ul>`
  return `<section class="sec card pad modebox ${pro() ? 'is-on' : ''}">
      <div class="modebox__top"><div><h3>Экспертный режим</h3><p class="muted">Для тех, кто ведёт рекламу сам и хочет видеть все цифры и ручки. Включить и выключить можно в любой момент — данные и настройки не меняются.</p></div>
        ${swBtn('expert:toggle', pro(), 'Экспертный режим')}</div>
      <div class="duo modes"><div><h4>Обычный режим показывает</h4>${list(MODE_SIMPLE)}</div><div><h4>Экспертный добавляет</h4>${list(MODE_EXPERT)}</div></div>
      <p class="meta">В обычном режиме эти инструменты не пропадают: сервис считает то же самое сам и приходит с готовым сигналом или предложением.</p>
      <div class="acts acts--tight"><button class="btn btn--sm" data-act="go:map">${ic('map', 14)}Карта функций: что где лежит</button></div>
    </section>
    ${sec('Тема', '', `<div class="seg"><button class="${S.theme === 'light' ? 'active' : ''}" data-act="theme:light">${ic('sun', 14)}Светлая</button><button class="${S.theme === 'dark' ? 'active' : ''}" data-act="theme:dark">${ic('moon', 14)}Тёмная</button></div>`)}`
}

function settings() {
  const nav = snav('settings', STABS)
  return `<div class="page" data-scroll="main"><div class="wide">
    <div class="head"><div><p class="eyebrow">${M.user.email}</p><h1>Настройки</h1></div></div>
    <div class="set">${nav.html}<div class="set__body">${{ profile: setProfile, notif: setNotif, ui: setUi }[nav.cur]()}</div></div>
  </div></div>`
}

/* ---------- тариф и лимит ---------- */

function usageBlocks() {
  const labels = M.usage.days.map((_, i) => {
    const d = new Date(2026, 9, 12 + i)
    return `${d.getDate()}.${d.getMonth() + 1}`
  })
  const rows = (list) => hbars(list.map(([label, v]) => ({ label, value: v, text: `${v}%`, note: `${Math.round((v / M.plan.used) * 100)}% расхода` })))
  return `<h2>На что ушёл лимит<small>с 12 октября, в процентах от месячного лимита</small></h2>
    <div class="duo"><div class="card pad"><h3 class="cap">По типу запросов</h3>${rows(M.usage.models)}</div><div class="card pad"><h3 class="cap">По проектам</h3>${rows(M.usage.projects)}</div></div>
    <div class="card pad"><h3 class="cap">Расход лимита по дням, %</h3>${barChart({ data: M.usage.days, labels, fmt: (v) => `${v}%`, w: 980, h: 200, label: 'Расход лимита ИИ по дням' })}</div>`
}

function plan() {
  const cur = M.plans.find((x) => x.name === M.plan.name)
  const count = M.projects.length
  return `<div class="page" data-scroll="main"><div class="wide">
    <div class="head"><div><p class="eyebrow">${M.user.email}</p><h1>Тариф и лимит</h1></div></div>
    <div class="duo">
      <div class="card pad"><small class="cap">Ваш тариф</small><b class="big">${M.plan.name} <span>оплачен до ${M.plan.until}</span></b>
        ${dl([['Сайты', `${count} из 5`], ['Лимит ИИ', cur.limit.replace('Лимит ИИ ', '') + ' от базового'], ['Следующая оплата', `${M.plan.until}, ${cur.price}`]])}
        <div class="acts acts--tight"><button class="btn btn--pri btn--sm" data-act="modal:pay:${cur.id}">Продлить</button></div></div>
      <div class="card pad"><small class="cap">Лимит ИИ в этом месяце</small><b class="big num">${M.plan.used}% <span>использовано</span></b>
        <span class="meter meter--wide"><i style="width:${M.plan.used}%"></i></span>
        <p class="muted">Обновится 12 ноября, остаток не переносится. Лимит тратят ответы ИИ и запросы в Вордстат. Сигналы, отчёты, дайджест и правила работают без него.</p>
        <div class="acts acts--tight"><button class="lnk" data-act="flag:limit80">${on('limit80') ? 'Убрать пример предупреждения' : 'Показать, как сервис предупредит на 80%'}</button></div></div>
    </div>
    ${pro() ? usageBlocks() : ''}
    <h2>Тарифы<small>цена за месяц</small></h2>
    <div class="plan-grid">${M.plans.map((x) => `<article class="pcard card ${x === cur ? 'is-cur' : ''}"><header><h3>${x.name}</h3>${x === cur ? '<span class="chip chip--lime">Ваш тариф</span>' : ''}</header>
      <b class="big num">${x.price}</b><p class="muted">${x.sites} · ${x.limit}</p>
      <ul class="checklist">${x.feats.map((f) => `<li>${ic('check', 15)}<span>${f}</span></li>`).join('')}</ul>
      <button class="btn ${M.plans.indexOf(x) > M.plans.indexOf(cur) ? 'btn--pri' : ''}" data-act="modal:pay:${x.id}">${x === cur ? 'Продлить' : `Перейти на «${x.name}»`}</button></article>`).join('')}</div>
    <div class="duo">
      <div class="card pad"><h3 class="cap">Промокод</h3>
        ${on('promo') ? `<p class="lead">${badge('ok', 'Промокод принят')}<span>Скидка 10% на следующую оплату.</span></p>` : `<div class="irow">${field('promo', 'Код', '', { ph: 'Например: OKNA10' })}<button class="btn" data-act="promo">Применить</button></div>`}</div>
      <div class="card pad"><h3 class="cap">Ссылка для друзей</h3>
        <div class="irow"><label class="field"><span>Ваша ссылка</span><span class="inp"><input readonly value="aimarketing.ru/r/anton-7f3k" aria-label="Реферальная ссылка"></span></label><button class="btn" data-act="toast:Ссылка скопирована">${ic('copy', 15)}Скопировать</button></div>
        <p class="meta">Зарегистрировались по ссылке: 2 · оплатили тариф: 1. Бонус приходит, когда приглашённый оплатит тариф.</p></div>
    </div>
    <h2>Оплаты</h2>
    <div class="card scroll-x"><table class="tbl"><tr><th>Дата</th><th>За что</th><th class="r">Сумма</th><th>Статус</th><th></th></tr>
      ${M.payments.map((x) => `<tr><td class="num">${x[0]}</td><td>${x[1]}</td><td class="r num">${x[2]}</td><td>${x[3]}</td><td class="r"><button class="btn btn--ghost btn--sm" data-act="toast:Чек отправлен на ${M.user.email}">Чек</button></td></tr>`).join('')}</table></div>
    ${expertHint('На что ушёл лимит: по моделям, проектам и дням')}
  </div></div>`
}

Object.assign(MODALS, {
  pay: (id) => {
    const x = M.plans.find((pl) => pl.id === id) || M.plans[1]
    const n = +val('payper', '1')
    const price = parseInt(x.price.replace(/\D/g, ''), 10)
    const total = Math.round(price * n * (on('promo') ? 0.9 : 1))
    const cur = x.name === M.plan.name
    const down = !cur && price < parseInt(M.plans.find((pl) => pl.name === M.plan.name).price.replace(/\D/g, ''), 10)
    return {
      title: cur ? `Продлить тариф «${x.name}»` : `Перейти на тариф «${x.name}»`,
      body: `<div class="field"><span>На сколько</span>${segm('payper', [['1', '1 месяц'], ['3', '3 месяца'], ['12', '12 месяцев']], '1')}</div>
        ${dl([['Тариф', `${x.name} · ${x.sites} · ${x.limit}`], ['Срок', `${n} ${plural(n, 'месяц', 'месяца', 'месяцев')}`], ...(on('promo') ? [['Промокод', 'скидка 10%']] : []), ['К оплате', `<b class="num">${rub(total)}</b>`]])}
        <p class="muted">${cur ? 'Срок добавится к текущему — до 12 декабря ничего не сгорит.' : down ? `На «${x.name}» останется ${x.sites}: после оплаты сервис спросит, какой оставить. Остальные сайты не удалятся, просто перестанут обновляться.` : 'Тариф сменится сразу после оплаты. За оставшиеся дни текущего тарифа сделаем перерасчёт.'}</p>
        <p class="meta">Оплата картой или через СБП на странице ЮKassa. Чек придёт на ${M.user.email}.</p>`,
      foot: `<button class="btn btn--pri" data-act="toast:В макете оплата не проводится: здесь откроется страница ЮKassa" data-close>Перейти к оплате</button><button class="btn" data-act="close">Отмена</button>`,
    }
  },
})

Object.assign(ACT, {
  promo: () => {
    const code = String(S.form.promo || '').trim()
    if (code) S.flags.promo = true
    S.toast = code ? 'Промокод принят: скидка 10% на следующую оплату' : 'Введите промокод'
  },
  utmfix: (v) => {
    S.flags[`utmfix:${v}`] = true
    S.toast = 'Параметры кампании приведены к шаблону. Запись появилась в «Истории»'
  },
})
