import { reactive } from 'vue'
import { saveUi, useProfile } from './useProfile'

/**
 * Подсказки у элементов интерфейса («коуч»): v-coach="'ai'" на элементе — и при первом его появлении рядом
 * всплывёт пояснение. Не тур: не больше одной подсказки за заход на страницу, каждая — один раз.
 * «Понятно» прячет эту подсказку навсегда, «Больше не показывать» — все. Что уже видели — в users.ui.hints_seen
 * (id вида coach:ai, выключены все — coach-off), так подсказки не повторяются на другом устройстве.
 * Набор зависит от роли: специалисту по рекламе не объясняем то, что он и так знает.
 */
const OFF = 'coach-off'
const WAIT_MS = 900 // даём странице догрузиться, чтобы подсказка не прыгала

// Порядок — важность: если на странице несколько новых элементов, покажем первый по списку.
export const COACH = {
  proposal: {
    title: 'Предложение ИИ',
    owner:
      'Так ИИ предлагает правку. «Выполнить» — применить в Директе, «Отклонить» — отказаться, «Позже» — решить потом. Без вашего «да» ничего не меняется.',
    pro: '«Выполнить» применяет правку в Директе, «Позже» убирает карточку из счётчика решений. Откат — в «Истории».',
  },
  undo: {
    title: 'Вернуть как было',
    owner: 'Отменяет эту правку в Директе: вернутся настройки, которые были до неё.',
    pro: 'Откат правки: в Директ уйдут прежние значения. Если с тех пор настройку меняли вручную, сервис предупредит.',
  },
  pending: {
    title: 'Ждёт решения',
    owner: 'Здесь собираются предложения ИИ, по которым нужно ваше «да» или «нет». Число — сколько их ждёт.',
  },
  ai: {
    title: 'ИИ-помощник',
    owner:
      'Спросите обычными словами, например «почему мало заявок?». ИИ посмотрит кампании и статистику и предложит, что сделать.',
    pro: 'Чат с ИИ: разбор кампаний, запросов и статистики, правки — карточками на подтверждение. Через @ можно сослаться на другой проект.',
  },
  project: {
    title: 'Проект',
    owner: 'Проект — это сайт со своей рекламой. Выберите проект, чтобы смотреть только его, или «Все проекты».',
    pro: 'Проект — счётчик Метрики с кампаниями и KPI. Фильтр для всех разделов; «Все проекты» — сводка по всем.',
  },
  mode: {
    title: 'Экспертный режим',
    owner:
      'Включает все отчёты и ручные инструменты Директа. Без него — только главное. Переключайте когда угодно: данные не меняются.',
    pro: 'Выключите, чтобы оставить только главное: сводку, решения и чат. Данные не меняются.',
  },
}
const ORDER = Object.keys(COACH)

const { user } = useProfile()
const candidates = new Map() // элемент → id подсказки
export const coach = reactive({ id: '', el: null, title: '', text: '' })
let shownOnPage = false
let timer = 0

function seen() {
  return user.value?.ui?.hints_seen || []
}

function roleOf() {
  return user.value?.ui?.role === 'pro' ? 'pro' : 'owner'
}

function onScreen(el) {
  if (!el.isConnected) return false
  const r = el.getBoundingClientRect()
  return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < window.innerHeight && r.right > 0 && r.left < window.innerWidth
}

function pick() {
  timer = 0
  if (shownOnPage || coach.id || !user.value) return
  const done = seen()
  if (done.includes(OFF)) return
  const role = roleOf()
  let best = null
  for (const [el, id] of candidates) {
    const hint = COACH[id]
    if (!hint?.[role] || done.includes(`coach:${id}`) || !onScreen(el)) continue
    if (!best || ORDER.indexOf(id) < ORDER.indexOf(best.id)) best = { id, el }
  }
  if (!best) return
  shownOnPage = true
  Object.assign(coach, { id: best.id, el: best.el, title: COACH[best.id].title, text: COACH[best.id][role] })
}

function schedule() {
  if (shownOnPage || timer) return
  timer = window.setTimeout(pick, WAIT_MS)
}

/** Новая страница — можно показать следующую подсказку. Текущая закрывается, не считаясь увиденной. */
export function coachNewPage() {
  shownOnPage = false
  hideCoach()
  schedule()
}

/** Прокрутили страницу — элемент мог появиться на экране. */
export function coachRecheck() {
  schedule()
}

/** Убрать с экрана, не отмечая увиденной (элемент пропал). */
export function hideCoach() {
  Object.assign(coach, { id: '', el: null, title: '', text: '' })
}

/** «Понятно» — эта подсказка больше не появится; all — «Больше не показывать» все. */
export function closeCoach(all = false) {
  if (!coach.id) return
  const ids = [`coach:${coach.id}`, ...(all ? [OFF] : [])]
  hideCoach()
  saveUi({ hints_seen: ids }).catch(() => {})
}

/** v-coach="'ai'" — элемент, к которому при первом появлении всплывёт подсказка. */
export const vCoach = {
  mounted(el, { value }) {
    if (!COACH[value]) return
    candidates.set(el, value)
    schedule()
  },
  updated(el, { value }) {
    if (candidates.get(el) !== value && COACH[value]) {
      candidates.set(el, value)
      schedule()
    }
  },
  unmounted(el) {
    candidates.delete(el)
    if (coach.el === el) hideCoach()
  },
}
