<template>
  <div class="hc" :class="{ 'hc--compact': compact }">
    <form class="hc__box" :class="{ 'is-focus': focused }" @submit.prevent="submit()">
      <label class="sr-only" :for="inputId">Ваш вопрос о рекламе или адрес сайта</label>
      <input
        :id="inputId"
        ref="input"
        v-model="text"
        class="hc__input"
        type="text"
        autocomplete="off"
        enterkeyhint="send"
        maxlength="300"
        :placeholder="placeholder"
        @focus="focused = true"
        @blur="focused = false"
      />
      <button class="hc__send" type="submit" :disabled="busy" :aria-label="isUrl ? 'Проверить сайт' : 'Спросить'">
        <span v-if="isUrl" class="hc__send-text">Проверить сайт</span>
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
          <path d="M5 12h14M13 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
    </form>

    <div class="hc__chips" role="list">
      <button
        v-for="(s, i) in SCENARIOS"
        :key="s.ask"
        role="listitem"
        class="hc__chip"
        :class="{ 'is-active': active === i }"
        type="button"
        :disabled="busy"
        @click="ask(i, 'chip')"
      >
        {{ s.ask }}
      </button>
    </div>

    <div v-if="thread" class="hc__thread" aria-live="polite">
      <p class="msg msg--user">{{ thread.ask }}</p>

      <div class="msg msg--ai">
        <span class="msg__avatar" aria-hidden="true">
          <svg viewBox="0 0 40 40"><path d="M10 8h20a4 4 0 0 1 4 4v11a4 4 0 0 1-4 4H19l-6 5v-5h-3a4 4 0 0 1-4-4V12a4 4 0 0 1 4-4z" fill="#141413" /><path d="M14 17.5l4 4 8-8" stroke="#c6f04a" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round" /></svg>
        </span>
        <div class="msg__body">
          <p v-if="!typed" class="msg__dots" aria-label="Толк печатает"><i></i><i></i><i></i></p>
          <p v-else>{{ typed }}<span v-if="typing" class="msg__caret"></span></p>

          <article v-if="thread.card && showCard" class="prop" :class="{ 'is-ok': approved }">
            <header class="prop__head">
              <span class="chip chip--lime">Предложение</span>
              <span class="prop__where">{{ thread.card.where }}</span>
            </header>
            <h3 class="prop__title">{{ thread.card.title }}</h3>
            <div class="delta">
              <div v-for="row in thread.card.rows" :key="row[0]" class="delta__row">
                <span>{{ row[0] }}</span>
                <span class="delta__val"><s>{{ row[1] }}</s> → <b>{{ row[2] }}</b></span>
              </div>
            </div>
            <footer class="prop__foot">
              <template v-if="!approved">
                <button class="btn btn--lime btn--sm" type="button" @click="approve">
                  <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
                  Одобрить
                </button>
                <button class="btn btn--ghost btn--sm" type="button" @click="reset">Отклонить</button>
              </template>
              <p v-else class="prop__done">
                <span class="prop__stamp" aria-hidden="true">✓</span>
                Одобрено — записано в журнал, можно откатить
              </p>
            </footer>
            <p class="demo-note">Демо-сценарий: цифры для примера</p>
          </article>

          <article v-if="thread.report && showCard" class="prop report">
            <header class="prop__head">
              <span class="chip chip--lime">Проверка сайта</span>
              <span class="prop__where">{{ thread.domain }}</span>
            </header>
            <div v-if="thread.report.headlines.length" class="report__part">
              <h3 class="report__title">Заголовки объявлений</h3>
              <ol class="report__heads">
                <li v-for="h in thread.report.headlines" :key="h">{{ h }}</li>
              </ol>
            </div>
            <div v-if="thread.report.minus_words.length" class="report__part">
              <h3 class="report__title">Минус-слова сразу</h3>
              <p class="report__words">
                <span v-for="w in thread.report.minus_words" :key="w" class="report__word">−{{ w }}</span>
                <span v-if="thread.report.more.minus_words" class="report__word report__word--more">
                  ещё {{ thread.report.more.minus_words }}
                </span>
              </p>
            </div>
            <p v-if="thread.report.more.groups || thread.report.more.headlines" class="report__locked">
              <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2" fill="none" stroke="currentColor" stroke-width="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" fill="none" stroke="currentColor" stroke-width="2" /></svg>
              В кабинете: {{ lockedText(thread.report.more) }}
            </p>
          </article>

          <div v-if="thread.cta && showCard" class="hc__cta" :class="{ 'is-ok': approved }">
            <p>{{ approved ? 'Так это работает в кабинете. Подключите Директ — это пара минут.' : thread.ctaText }}</p>
            <a
              class="btn btn--sm"
              :href="ctaHref"
              :data-goal="thread.report ? 'check_signup' : 'signup_click'"
              :data-goal-place="place || (compact ? 'final' : 'hero')"
            >
              Попробовать на своих данных
            </a>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, useId } from 'vue'
import { goal } from '../lib/goal.js'

const props = defineProps({
  compact: { type: Boolean, default: false },
  registerHref: { type: String, default: '/register' },
  // Свои сценарии и подсказки для посадочных; по умолчанию — сценарии главной.
  scenarios: { type: Array, default: null },
  hints: { type: Array, default: null },
  place: { type: String, default: '' },
})

// Демо-сценарии первого экрана (docs/landing-plan.md, раздел 3). Цифры выдуманы и подписаны как демо.
const DEFAULT_SCENARIOS = [
  {
    ask: 'Где я сливаю бюджет?',
    keys: ['слив', 'бюджет', 'утека', 'трат', 'площадк', 'рся'],
    answer:
      'За 30 дней 27 площадок РСЯ потратили 18 400 ₽ и не принесли ни одной заявки. Остальные 187 работают нормально — их не трогаю.',
    card: {
      where: 'РСЯ — ремонт квартир',
      title: 'Отключить 27 площадок без конверсий',
      rows: [
        ['Площадок в показах', '214', '187'],
        ['Расход без заявок', '18 400 ₽', '0 ₽'],
      ],
    },
  },
  {
    ask: 'Какие запросы минусовать?',
    keys: ['минус', 'запрос', 'мусор', 'ключ', 'фраз'],
    answer:
      'Нашёл 63 запроса, по которым кликают, но не оставляют заявок: «бесплатно», «своими руками», «вакансии»… Это 22% всех кликов.',
    card: {
      where: '4 поисковые кампании',
      title: 'Добавить 63 минус-фразы',
      rows: [
        ['Мусорных кликов', '22%', '4%'],
        ['Цена заявки', '1 240 ₽', '980 ₽'],
      ],
    },
  },
  {
    ask: 'Почему подорожала заявка?',
    keys: ['дорож', 'дорог', 'цена', 'заявк', 'cpa', 'лид', 'стоимост'],
    answer:
      'Кампания «Поиск — Москва» идёт 8 месяцев: CTR падает третью неделю, ставки выросли на 30%. Похоже на выгорание — копия с теми же настройками обычно стартует лучше.',
    card: {
      where: 'Поиск — Москва',
      title: 'Запустить копию, старую — на паузу',
      rows: [
        ['Заявок в неделю', '31', '42'],
        ['Цена заявки', '1 240 ₽', '~900 ₽'],
      ],
    },
  },
]

const DEFAULT_HINTS = [
  'Где я сливаю бюджет?',
  'Почему подорожала заявка?',
  'Вставьте адрес сайта — подберу заголовки и минус-слова',
  'Какие площадки РСЯ отключить?',
]

const SCENARIOS = props.scenarios?.length ? props.scenarios : DEFAULT_SCENARIOS
const HINTS = props.hints?.length ? props.hints : DEFAULT_HINTS

const inputId = useId()
const input = ref(null)
const text = ref('')
const focused = ref(false)
const placeholder = ref(props.compact ? 'Спросите о своей рекламе…' : HINTS[0])
const thread = ref(null)
const active = ref(-1)
const typed = ref('')
const typing = ref(false)
const showCard = ref(false)
const approved = ref(false)
const busy = ref(false)

const URL_RE = /^(https?:\/\/)?([a-zа-яё0-9-]+\.)+[a-zа-яё]{2,}(\/\S*)?$/i
const isUrl = computed(() => URL_RE.test(text.value.trim()))

let timers = []
const later = (fn, ms) => timers.push(setTimeout(fn, ms))
const clearTimers = () => {
  timers.forEach(clearTimeout)
  timers = []
}
const reduced = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

// Плейсхолдер сам «печатает» примеры вопросов, пока поле пустое и не в фокусе.
let hintTimer = 0
function cycleHints() {
  if (props.compact || reduced) return
  let i = 0
  let pos = HINTS[0].length
  let dir = -1
  let pause = 40
  hintTimer = setInterval(() => {
    if (focused.value || text.value) return
    if (pause > 0) {
      pause -= 1
      return
    }
    pos += dir
    if (pos <= 0) {
      dir = 1
      i = (i + 1) % HINTS.length
    } else if (pos >= HINTS[i].length) {
      dir = -1
      pause = 45
    }
    placeholder.value = HINTS[i].slice(0, Math.max(pos, 0)) || ' '
  }, 45)
}

function typeOut(full, done) {
  typed.value = ''
  typing.value = true
  if (reduced) {
    typed.value = full
    typing.value = false
    done()
    return
  }
  const words = full.split(' ')
  let n = 0
  const step = () => {
    n += 1
    typed.value = words.slice(0, n).join(' ')
    if (n < words.length) later(step, 34 + Math.random() * 40)
    else {
      typing.value = false
      done()
    }
  }
  later(step, 500)
}

function play(item) {
  clearTimers()
  approved.value = false
  showCard.value = false
  typed.value = ''
  thread.value = item
  busy.value = true
  typeOut(item.answer, () => {
    later(() => {
      showCard.value = true
      busy.value = false
    }, reduced ? 0 : 260)
  })
}

function ask(i, how) {
  checkSeq += 1
  active.value = i
  goal('hero_chip', { chip: SCENARIOS[i].ask, how })
  play({ ...SCENARIOS[i], cta: true, ctaText: 'Это демо. На ваших данных Толк найдёт своё.' })
}

function submit() {
  const value = text.value.trim()
  if (!value || busy.value) return
  text.value = ''
  active.value = -1
  if (URL_RE.test(value)) {
    const domain = value.replace(/^https?:\/\//i, '').split('/')[0].toLowerCase()
    goal('check_start', { domain })
    checkSite(value, domain)
    return
  }
  const lower = value.toLowerCase()
  const found = SCENARIOS.findIndex((s) => s.keys.some((k) => lower.includes(k)))
  if (found >= 0) {
    active.value = found
    goal('hero_chip', { chip: SCENARIOS[found].ask, how: 'typed' })
    play({ ...SCENARIOS[found], ask: value, cta: true, ctaText: 'Это демо. На ваших данных Толк найдёт своё.' })
    return
  }
  play({
    ask: value,
    answer:
      'Хороший вопрос — чтобы ответить по делу, мне нужны цифры вашего кабинета. Подключите Директ и Метрику, и я отвечу на ваших данных. А пока нажмите любой пример выше — покажу, как это выглядит.',
    card: null,
    cta: true,
    ctaText: 'Подключение — через вход Яндекса, пара минут.',
  })
}

// Живая проверка сайта без регистрации (docs/landing-plan.md, раздел 5): POST создаёт проверку, дальше опрос.
// Готовую проверку кабинет забирает после регистрации: id уходит в ссылке ?check=… и в localStorage
// (тот же ключ читает кабинет, если сайт и кабинет на одном домене) — полный результат откроется в чате.
const CHECK_KEY = 'aimarketing-check'
const ctaHref = computed(() => {
  const id = thread.value?.checkId
  if (!id) return props.registerHref
  return `${props.registerHref}${props.registerHref.includes('?') ? '&' : '?'}check=${encodeURIComponent(id)}`
})

function rememberCheck(id) {
  try {
    localStorage.setItem(CHECK_KEY, JSON.stringify({ id, at: Date.now() }))
  } catch {
    // приватный режим — останется ссылка с ?check=
  }
}
const CHECK_FAILED = 'Не получилось проверить сайт — попробуйте ещё раз чуть позже. В кабинете проверка работает и бесплатна в пробный период.'
const CHECK_TIMEOUT = 150_000
let checkSeq = 0
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function api(url, init) {
  const res = await fetch(url, { ...init, headers: { Accept: 'application/json', 'Content-Type': 'application/json' } })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data?.detail?.message || CHECK_FAILED)
  return data
}

function plural(n, one, few, many) {
  const m10 = n % 10
  const m100 = n % 100
  if (m10 === 1 && m100 !== 11) return one
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few
  return many
}

function lockedText(more) {
  const parts = []
  if (more.groups) parts.push(`${more.groups} ${plural(more.groups, 'группа', 'группы', 'групп')} объявлений с ключевыми фразами`)
  if (more.headlines) parts.push(`ещё ${more.headlines} ${plural(more.headlines, 'заголовок', 'заголовка', 'заголовков')}`)
  return parts.join(', ') + ' — и черновик кампании из них'
}

async function checkSite(value, domain) {
  clearTimers()
  const seq = ++checkSeq
  approved.value = false
  showCard.value = false
  busy.value = true
  thread.value = { ask: value, domain, card: null }
  typing.value = true
  typed.value = `Открываю ${domain} и читаю главную страницу…`
  try {
    const started = await api('/api/site/check', { method: 'POST', body: JSON.stringify({ url: value }) })
    const since = Date.now()
    let check = { status: 'running' }
    while (check.status === 'running') {
      if (Date.now() - since > CHECK_TIMEOUT) throw new Error(CHECK_FAILED)
      if (!started.cached) await pause(2000)
      if (seq !== checkSeq) return
      check = await api(`/api/site/check/${encodeURIComponent(started.id)}`)
      if (seq !== checkSeq) return
      if (Date.now() - since > 6000) typed.value = 'Понял, о чём сайт. Подбираю заголовки и минус-слова…'
    }
    if (check.status === 'error') throw new Error(check.error || CHECK_FAILED)
    const r = check.result
    goal('check_done', { domain })
    rememberCheck(started.id)
    const about = [r.niche && `${r.niche[0].toUpperCase()}${r.niche.slice(1)}.`, r.offer].filter(Boolean).join(' ')
    play({
      ask: value,
      domain,
      answer: about ? `Вот что понял про ${domain}: ${about}` : `Посмотрел ${domain} — вот с чего начать рекламу.`,
      card: null,
      report: r,
      checkId: started.id,
      cta: true,
      ctaText: 'Зарегистрируйтесь — Толк соберёт из этого черновик кампании. Пробный период бесплатный.',
    })
  } catch (err) {
    if (seq !== checkSeq) return
    goal('check_error', { domain })
    play({
      ask: value,
      answer: err?.message && err.message !== 'Failed to fetch' ? err.message : CHECK_FAILED,
      card: null,
      cta: true,
      ctaText: 'В кабинете Толк проверит сайт и соберёт черновик кампании.',
    })
  }
}

function approve() {
  approved.value = true
  goal('demo_approve', { card: thread.value?.card?.title })
}

function reset() {
  clearTimers()
  checkSeq += 1
  thread.value = null
  active.value = -1
  busy.value = false
  input.value?.focus()
}

onMounted(cycleHints)
onBeforeUnmount(() => {
  clearTimers()
  clearInterval(hintTimer)
})
</script>

<style scoped>
.hc {
  display: grid;
  gap: 14px;
  width: min(720px, 100%);
  margin-inline: auto;
  text-align: left;
}

.hc__box {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 8px 8px 22px;
  border-radius: 22px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  box-shadow:
    0 1px 2px rgba(20, 20, 19, 0.04),
    0 18px 50px rgba(20, 20, 19, 0.09);
  transition:
    box-shadow 0.2s ease,
    border-color 0.2s ease;
}

.hc__box.is-focus {
  border-color: var(--ink);
  box-shadow:
    0 0 0 4px var(--lime),
    0 18px 50px rgba(20, 20, 19, 0.1);
}

.hc__input {
  flex: 1;
  min-width: 0;
  height: 48px;
  border: 0;
  outline: 0;
  background: transparent;
  font-size: 18px;
  font-weight: 500;
}

.hc__input::placeholder {
  color: var(--faint);
}

.hc__send {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 52px;
  min-width: 52px;
  padding: 0 16px;
  border: 0;
  border-radius: 16px;
  background: var(--solid);
  color: var(--on-solid);
  font-weight: 700;
  justify-content: center;
  cursor: pointer;
  transition: transform 0.15s ease;
}

.hc__send:hover {
  transform: translateY(-1px);
}

.hc__send:disabled {
  opacity: 0.6;
  cursor: progress;
}

.hc__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
}

.hc__chip {
  padding: 9px 15px;
  border-radius: var(--pill);
  border: 1px solid var(--line-2);
  background: color-mix(in srgb, var(--surface) 70%, transparent);
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition:
    background 0.15s ease,
    border-color 0.15s ease;
}

.hc__chip:hover {
  background: var(--surface);
  border-color: var(--ink);
}

.hc__chip.is-active {
  background: var(--lime);
  border-color: var(--lime);
}

.hc__chip:disabled:not(.is-active) {
  opacity: 0.55;
  cursor: progress;
}

.hc__thread {
  display: grid;
  gap: 14px;
  margin-top: 10px;
  padding: 20px;
  border-radius: 24px;
  background: color-mix(in srgb, var(--surface) 72%, transparent);
  border: 1px solid var(--line);
  backdrop-filter: blur(10px);
  animation: rise 0.35s ease both;
}

.msg--user {
  justify-self: end;
  max-width: 80%;
  padding: 10px 16px;
  border-radius: 18px 18px 6px 18px;
  background: var(--solid);
  color: var(--on-solid);
  font-weight: 600;
}

.msg--ai {
  display: grid;
  grid-template-columns: 34px 1fr;
  gap: 12px;
  align-items: start;
}

.msg__avatar {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 11px;
  background: var(--lime);
}

.msg__avatar svg {
  width: 26px;
  height: 26px;
}

.msg__body {
  display: grid;
  gap: 14px;
  padding-top: 5px;
  font-size: 16px;
  line-height: 1.5;
}

.msg__dots {
  display: flex;
  gap: 5px;
  padding-top: 8px;
}

.msg__dots i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--faint);
  animation: blink 1s infinite ease-in-out;
}

.msg__dots i:nth-child(2) {
  animation-delay: 0.15s;
}

.msg__dots i:nth-child(3) {
  animation-delay: 0.3s;
}

.msg__caret {
  display: inline-block;
  width: 2px;
  height: 1.05em;
  margin-left: 2px;
  vertical-align: -2px;
  background: var(--ink);
  animation: blink 0.8s infinite steps(1);
}

.prop {
  display: grid;
  gap: 12px;
  padding: 18px;
  border-radius: 18px;
  background: var(--surface);
  border: 1px solid var(--line);
  box-shadow: var(--shadow);
  animation: rise 0.35s ease both;
  transition:
    box-shadow 0.3s ease,
    border-color 0.3s ease;
}

.prop.is-ok {
  border-color: var(--lime);
  box-shadow:
    0 0 0 3px var(--lime-soft),
    var(--shadow);
}

.prop__head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
}

.prop__where {
  font-size: 13px;
  color: var(--faint);
  font-weight: 600;
}

.prop__title {
  font-size: 19px;
  letter-spacing: -0.02em;
  line-height: 1.25;
}

.prop__foot {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding-top: 4px;
}

.prop__done {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 700;
  font-size: 15px;
}

.prop__stamp {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--lime);
  color: var(--on-lime);
  font-weight: 800;
  animation: pop 0.4s cubic-bezier(0.3, 1.6, 0.5, 1) both;
}

.report__part {
  display: grid;
  gap: 8px;
}

.report__title {
  font-size: 14px;
  font-weight: 700;
  color: var(--faint);
  letter-spacing: 0;
}

.report__heads {
  display: grid;
  gap: 6px;
  margin: 0;
  padding-left: 1.3em;
  font-weight: 600;
}

.report__words {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.report__word {
  padding: 3px 10px;
  border-radius: var(--pill);
  background: var(--surface-2);
  border: 1px solid var(--line);
  font-size: 14px;
}

.report__word--more {
  border-style: dashed;
  color: var(--faint);
}

.report__locked {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 14px;
  font-weight: 600;
  color: var(--ink-2);
}

.report__locked svg {
  flex: none;
}

.hc__cta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 14px;
  align-items: center;
  justify-content: space-between;
  padding: 12px 12px 12px 16px;
  border-radius: 16px;
  background: var(--surface-2);
  border: 1px dashed var(--line-2);
  font-size: 15px;
  color: var(--ink-2);
  animation: rise 0.35s 0.1s ease both;
}

.hc__cta.is-ok {
  background: var(--lime-soft);
  border: 1px solid transparent;
  color: var(--ink);
  font-weight: 600;
}

@keyframes rise {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
}

@keyframes pop {
  from {
    transform: scale(0.3);
    opacity: 0;
  }
}

@keyframes blink {
  50% {
    opacity: 0.25;
  }
}

@media (max-width: 640px) {
  .hc__box {
    padding-left: 16px;
    border-radius: 18px;
  }
  .hc__input {
    font-size: 16px;
  }
  .hc__send-text {
    display: none;
  }
  .hc__chips {
    flex-wrap: nowrap;
    justify-content: flex-start;
    overflow-x: auto;
    margin-inline: -20px;
    padding-inline: 20px;
    scrollbar-width: none;
  }
  .hc__chip {
    flex: none;
  }
  .hc__thread {
    padding: 14px;
  }
  .msg--ai {
    grid-template-columns: 1fr;
  }
  .msg__avatar {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .hc__thread,
  .prop,
  .hc__cta,
  .prop__stamp {
    animation: none;
  }
}
</style>
