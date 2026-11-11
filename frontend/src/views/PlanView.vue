<template>
  <p v-if="expert" class="page-lead">Условия, расход лимита за период, оплата и переход на другой тариф.</p>
  <p v-else class="page-lead">Ваш тариф, сколько лимита ИИ осталось в этом месяце, оплата и промокод.</p>

  <div v-if="payNotice" class="notice pay-notice" :class="payNotice.cls" role="status">
    <span>
      {{ payNotice.text }}
      <a v-if="payNotice.url" :href="payNotice.url">Вернуться к оплате</a>
    </span>
    <button class="icon-btn" type="button" aria-label="Скрыть" @click="closeNotice">
      <X aria-hidden="true" />
    </button>
  </div>

  <div v-if="loading" class="panel"><p class="empty">Загружаем…</p></div>

  <div v-else-if="loadError" class="panel">
    <p class="empty">{{ loadError }}</p>
    <button class="btn btn--ghost" type="button" @click="load">Повторить</button>
  </div>

  <template v-else-if="data">
    <div v-if="sub.state === 'expired'" class="notice notice--warn">
      <strong>Подписка закончилась {{ formatDay(sub.last_day) }}.</strong>
      Кабинет закрыт, но все данные сохранены. Загрузка из Яндекса и правила остановлены — после продления догрузим
      пропущенное за время перерыва.
    </div>

    <div class="plan-duo">
      <section class="ccard plan-card">
        <small class="plan-cap">Ваш тариф</small>
        <p class="plan-big">
          {{ sub.plan ? sub.plan.name : 'Без тарифа' }} <span>{{ termShort }}</span>
        </p>
        <dl class="plan-dl">
          <div>
            <dt>Сайты</dt>
            <dd>{{ sitesText }}</dd>
          </div>
          <div>
            <dt>Лимит ИИ</dt>
            <dd>{{ sub.units_limited ? 'на месяц, обновляется сам' : 'без лимита' }}</dd>
          </div>
          <div v-if="nextPayText">
            <dt>{{ sub.state === 'expired' ? 'Оплатить' : 'Следующая оплата' }}</dt>
            <dd>{{ nextPayText }}</dd>
          </div>
        </dl>
        <div class="plan-acts">
          <button v-if="currentOffer" class="btn btn--accent" type="button" @click="choose(currentOffer)">
            Продлить
          </button>
          <a v-else class="btn btn--accent" href="#offers">Выбрать тариф</a>
        </div>
      </section>

      <section class="ccard plan-card">
        <small class="plan-cap">Лимит ИИ в этом месяце</small>
        <template v-if="!usage">
          <p class="plan-big">Недоступен</p>
          <p class="plan-muted">Подписка закончилась — после продления лимит снова будет полным.</p>
        </template>
        <template v-else-if="!usage.limited">
          <p class="plan-big">Без лимита</p>
          <p class="plan-muted">ИИ и Вордстат работают без ограничений на вашем тарифе.</p>
        </template>
        <template v-else>
          <p class="plan-big">{{ pct(usage.used_percent, 0) }} <span>использовано</span></p>
          <div
            class="meter plan-meter"
            :class="meterTone"
            role="progressbar"
            :aria-valuenow="usage.used_percent"
            aria-valuemin="0"
            aria-valuemax="100"
            aria-label="Использовано лимита ИИ"
          >
            <span :style="{ width: `${Math.min(100, usage.used_percent)}%` }"></span>
          </div>
          <p v-if="usage.exhausted" class="plan-muted plan-muted--bad">
            Лимит исчерпан: ИИ и Вордстат недоступны до {{ formatDay(usage.period_end) }} или до перехода на тариф
            выше. Сигналы, отчёты, дайджест и правила продолжают работать.
          </p>
          <p v-else class="plan-muted">
            Обновится {{ formatDay(usage.period_end) }}, остаток не переносится. Лимит тратят ответы ИИ и запросы в
            Вордстат. Сигналы, отчёты, дайджест и правила работают без него.
          </p>
        </template>
      </section>
    </div>

    <template v-if="expert">
      <UsagePanel v-if="usage" :usage="usage" breakdown />
      <section class="panel">
        <div class="panel__head">
          <h2>Что входит в тариф</h2>
          <span class="chip" :class="stateChip.cls">{{ stateChip.text }}</span>
        </div>
        <ul class="plan-facts">
          <li>
            <span>Проекты</span>
            <strong>{{ sub.projects_limit === null ? 'без ограничений' : `до ${sub.projects_limit}` }}</strong>
          </li>
          <li>
            <span>ИИ-ассистент, Вордстат и картинки</span>
            <strong>{{ sub.units_limited ? 'лимит на период' : 'без лимита' }}</strong>
          </li>
          <li v-for="(label, key) in data.features" :key="key" :class="{ 'is-off': sub.features?.[key] === false }">
            <span>{{ label }}</span>
            <strong v-if="sub.features?.[key] === false"><Lock aria-hidden="true" /> на тарифе выше</strong>
            <strong v-else><Check aria-hidden="true" /> есть</strong>
          </li>
        </ul>
      </section>
    </template>

    <section id="offers" class="plan-sec">
      <h2 class="plan-h">
        {{ sub.state === 'expired' ? 'Продлить или сменить тариф' : 'Тарифы' }}
        <small>цена за срок тарифа, скидка за переход уже учтена</small>
      </h2>

      <div v-if="chosen" id="pay" class="pay-box" role="region" aria-label="Оплата тарифа">
        <div class="pay-box__head">
          <h3>{{ chosen.current ? 'Продлить тариф' : 'Перейти на тариф' }} «{{ chosen.name }}»</h3>
          <button class="icon-btn" type="button" aria-label="Закрыть" @click="closeChosen">
            <X aria-hidden="true" />
          </button>
        </div>

        <p v-if="!data.payments_enabled" class="pay-box__text">
          Онлайн-оплата пока не подключена. Чтобы {{ chosen.current ? 'продлить' : 'перейти на' }} «{{ chosen.name }}»
          сейчас, напишите в поддержку — переключим тариф вручную.
        </p>
        <div v-else class="pay-term">
          <span class="pay-term__label">На сколько</span>
          <div class="seg" role="group" aria-label="Срок оплаты">
            <button
              v-for="m in TERMS"
              :key="m"
              type="button"
              :class="{ active: months === m }"
              :aria-pressed="months === m"
              :disabled="payBusy"
              @click="setMonths(m)"
            >
              {{ monthsText(m) }}
              <small v-if="termPct(m)" class="pay-term__off">−{{ num(termPct(m), 2) }}%</small>
            </button>
          </div>
        </div>
        <p v-if="data.payments_enabled && quoteError" class="form-error">{{ quoteError }}</p>
        <p v-else-if="data.payments_enabled && !quote" class="empty">Считаем стоимость…</p>
        <template v-else-if="data.payments_enabled">
          <dl class="plan-dl pay-sum">
            <div>
              <dt>Тариф</dt>
              <dd>{{ quote.plan_name }} · {{ offerSites(chosen) }}</dd>
            </div>
            <div>
              <dt>Срок</dt>
              <dd>{{ monthsText(quote.months) }}</dd>
            </div>
            <div v-if="quote.change_discount_pct">
              <dt>Скидка за переход</dt>
              <dd>{{ num(quote.change_discount_pct, 2) }}%</dd>
            </div>
            <div v-if="quote.term_discount_pct">
              <dt>Скидка за срок</dt>
              <dd>{{ num(quote.term_discount_pct, 2) }}% · −{{ rubles(quote.full_rub - quote.amount_rub, 2) }}</dd>
            </div>
            <div class="pay-sum__total">
              <dt>К оплате</dt>
              <dd>{{ rubles(quote.amount_rub, 2) }}</dd>
            </div>
          </dl>
          <p class="pay-box__text">{{ quoteTerm }}</p>
          <p v-if="data.payments_test" class="notice notice--warn pay-box__test">
            Оплата в тестовом режиме: деньги не списываются. Для проверки используйте тестовую карту из документации
            ЮKassa.
          </p>
          <p class="pay-box__terms">
            Оплата через ЮKassa — картой или СБП. Нажимая «Оплатить», вы принимаете
            <a :href="docs.offer_url" target="_blank" rel="noopener">оферту</a>. Продление — вручную, автосписаний нет.
          </p>
          <div class="row-actions">
            <button class="btn btn--accent" type="button" :disabled="payBusy" @click="pay">
              {{ payBusy ? 'Переходим к оплате…' : `Оплатить ${rubles(quote.amount_rub, 2)}` }}
            </button>
            <button class="btn btn--ghost" type="button" @click="closeChosen">Отмена</button>
          </div>
        </template>
      </div>

      <p v-if="!data.offers.length" class="empty">Тарифы ещё не настроены — напишите в поддержку, подберём условия.</p>
      <PlanOffers v-else :offers="data.offers" :features="data.features" @choose="choose" />
    </section>

    <div class="plan-duo plan-duo--low">
      <section class="ccard plan-card">
        <small class="plan-cap">Промокод</small>
        <form class="plan-irow" @submit.prevent="redeem">
          <div v-field class="field">
            <label>Код</label>
            <input v-model.trim="promo" type="text" maxlength="32" autocomplete="off" placeholder="Например, START" />
          </div>
          <button class="btn" type="submit" :disabled="promoBusy || !promo">
            {{ promoBusy ? 'Проверяем…' : 'Применить' }}
          </button>
        </form>
        <p v-if="promoError" class="form-error">{{ promoError }}</p>
        <p v-else-if="promoDone" class="form-ok">{{ promoDone }}</p>
        <p v-else class="plan-muted">Промокод может продлить тариф, подключить другой или добавить лимит ИИ на этот месяц.</p>
      </section>

      <section class="ccard plan-card">
        <small class="plan-cap">Ссылка для друзей</small>
        <p v-if="refError" class="form-error">{{ refError }}</p>
        <p v-else-if="!referral" class="plan-muted">Загружаем…</p>
        <template v-else>
          <div class="plan-irow">
            <div v-field class="field">
              <label>Ваша ссылка</label>
              <input :value="referral.url" type="text" readonly @focus="$event.target.select()" />
            </div>
            <button class="btn" type="button" @click="copyRef"><Copy aria-hidden="true" /> Скопировать</button>
          </div>
          <p class="plan-muted plan-meta">
            Зарегистрировались по ссылке: {{ num(referral.registrations) }} · оплатили тариф: {{ num(referral.payers) }}.
            {{ bonusText }}
          </p>
        </template>
      </section>
    </div>

    <section v-if="data.payments?.length" class="plan-sec">
      <h2 class="plan-h">Оплаты</h2>
      <div class="ccard table-scroll">
        <table class="table">
          <thead>
            <tr>
              <th>Дата</th>
              <th>За что</th>
              <th class="num">Сумма</th>
              <th>Статус</th>
              <th v-if="expert">Срок</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in data.payments" :key="p.id">
              <td>{{ expert ? formatDateTime(p.created_at) : formatDate(p.created_at) }}</td>
              <td>
                {{ p.kind === 'renew' ? 'Продление' : 'Переход на' }} «{{ p.plan_name || '—' }}»
              </td>
              <td class="num">
                {{ rubles(p.amount_rub, 2) }}
                <small v-if="p.refunded_rub" class="cell-note">возврат {{ rubles(p.refunded_rub, 2) }}</small>
              </td>
              <td>
                <span class="chip" :class="statusChip(p)">{{ p.status_label }}</span>
                <span v-if="p.test" class="chip">тест</span>
                <small v-if="p.cancel_reason" class="cell-note">{{ p.cancel_reason }}</small>
                <a v-if="p.confirmation_url" class="cell-note" :href="p.confirmation_url">Оплатить</a>
              </td>
              <td v-if="expert">{{ p.last_day ? `по ${formatDay(p.last_day)}` : '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="plan-muted plan-note">Возврат — по запросу в поддержку, деньги вернутся тем же способом оплаты.</p>
    </section>

    <ExpertHint v-if="!expert">На что ушёл лимит: по типам запросов, проектам и дням, и история тарифа</ExpertHint>

    <section v-if="expert && history.length" class="panel">
      <div class="panel__head">
        <h2>История тарифа</h2>
      </div>
      <div class="table-scroll">
        <table class="table">
          <thead>
            <tr>
              <th>Когда</th>
              <th>Событие</th>
              <th>Тариф</th>
              <th>Срок</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(h, i) in history" :key="i">
              <td>{{ formatDateTime(h.created_at) }}</td>
              <td>{{ historyLabel(h) }}</td>
              <td>{{ h.plan_name || '—' }}</td>
              <td>{{ h.last_day ? `по ${formatDay(h.last_day)}` : h.kind === 'plan_changed' ? 'бессрочно' : '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </template>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Check, Copy, Lock, X } from '@lucide/vue'
import PlanOffers from '../components/PlanOffers.vue'
import ExpertHint from '../components/ExpertHint.vue'
import UsagePanel from '../components/UsagePanel.vue'
import { errorText, webApi } from '../api/web'
import { useExpert } from '../composables/useExpert'
import { loadPlan, usePlan } from '../composables/usePlan'
import { useSiteConfig } from '../composables/useSiteConfig'
import { loadWorkspaces, useWorkspaces } from '../composables/useWorkspaces'
import { useHead } from '../utils/useHead'
import { pushToast } from '../composables/useToasts'
import { formatDate, formatDateTime, formatDay, isoDay, num, pct, plural, rubles } from '../utils/format'

useHead('Тариф и лимит — AiMarketing', true)

const route = useRoute()
const router = useRouter()
const { plan: data } = usePlan()
const { config, load: loadConfig } = useSiteConfig()
const docs = computed(() => config.value || { offer_url: '/offer' })

const loading = ref(!data.value)
const loadError = ref('')

const sub = computed(() => data.value?.subscription || {})
// Оплаты — в таблице «Платежи», в истории остаются смены тарифа и промокоды.
const history = computed(() => (data.value?.history || []).filter((h) => h.kind !== 'payment'))

const { expert } = useExpert()
const { workspaces } = useWorkspaces()
const usage = computed(() => data.value?.usage || null)
const currentOffer = computed(() => data.value?.offers?.find((o) => o.current) || null)

const termShort = computed(() => {
  const s = sub.value
  if (s.state === 'expired') return `закончился ${formatDay(s.last_day)}`
  if (!s.last_day) return 'бессрочно'
  return `${s.plan?.is_trial ? 'пробный до' : 'оплачен до'} ${formatDay(s.last_day)}`
})

const sitesText = computed(() => {
  const used = workspaces.value.filter((w) => !w.archived).length
  const limit = sub.value.projects_limit
  return limit === null || limit === undefined ? `${used}, без ограничений` : `${used} из ${limit}`
})

const nextPayText = computed(() => {
  const s = sub.value
  if (!s.last_day || s.plan?.is_trial) return ''
  const price = currentOffer.value?.price_with_discount ?? s.plan?.price_rub
  const day = s.state === 'expired' ? 'сейчас' : formatDay(s.last_day)
  return price ? `${day}, ${rubles(price)}` : day
})

const meterTone = computed(() => {
  const p = usage.value?.used_percent ?? 0
  return p >= 100 ? 'is-over' : p >= 80 ? 'is-near' : ''
})

const stateChip = computed(() => {
  const s = sub.value
  if (s.state === 'expired') return { text: 'закончилась', cls: 'chip--bad' }
  if (s.plan?.is_trial) return { text: 'пробный', cls: 'chip--info' }
  return { text: 'действует', cls: 'chip--ok' }
})

function historyLabel(h) {
  if (h.kind === 'promo') return h.code ? `Промокод ${h.code}` : 'Промокод'
  if (h.kind === 'plan_changed' && h.renew) return 'Продление'
  return h.label
}

function statusChip(p) {
  if (p.status === 'succeeded') return p.refunded_rub ? 'chip--warning' : 'chip--ok'
  if (p.status === 'canceled' || p.status === 'error') return 'chip--bad'
  return 'chip--info'
}

async function load() {
  loadError.value = ''
  try {
    const fresh = await loadPlan(true)
    // Продлили, пока была открыта эта страница, — вернём переключатель проектов в шапку.
    if (fresh.subscription?.state === 'ok') loadWorkspaces().catch(() => null)
  } catch (err) {
    loadError.value = errorText(err)
  } finally {
    loading.value = false
  }
}

// --- Выбор тарифа и оплата ---

const chosen = ref(null)
const quote = ref(null)
const quoteError = ref('')
const payBusy = ref(false)
// На сколько месяцев (сроков тарифа) платим сразу; скидка за 3 и 12 — из тарифа.
const TERMS = [1, 3, 12]
const months = ref(1)
let quoteSeq = 0

function monthsText(m) {
  return `${m} ${plural(m, 'месяц', 'месяца', 'месяцев')}`
}

function termPct(m) {
  return Number(chosen.value?.term_discounts?.[String(m)] || 0)
}

function offerSites(o) {
  const n = o?.projects_limit
  return n === null || n === undefined ? 'сайтов без ограничений' : `до ${n} ${plural(n, 'сайта', 'сайтов', 'сайтов')}`
}

async function loadQuote() {
  const seq = ++quoteSeq
  quote.value = null
  quoteError.value = ''
  try {
    const q = await webApi.paymentQuote(chosen.value.id, months.value)
    if (seq === quoteSeq) quote.value = q
  } catch (err) {
    if (seq === quoteSeq) quoteError.value = errorText(err)
  }
}

function setMonths(m) {
  if (months.value === m) return
  months.value = m
  if (chosen.value && data.value?.payments_enabled) loadQuote()
}

/** «ГГГГ-ММ-ДД» + n дней. */
function addDays(day, n) {
  const d = new Date(`${day}T00:00:00`)
  d.setDate(d.getDate() + n)
  return isoDay(d)
}

const quoteTerm = computed(() => {
  const q = quote.value
  const s = sub.value
  if (!q) return ''
  if (q.extends && s.last_day) {
    return `Продлим с конца текущего срока: тариф будет действовать по ${formatDay(addDays(s.last_day, q.days))} включительно.`
  }
  const until = formatDay(addDays(isoDay(new Date()), q.days))
  if (q.kind === 'change' && s.plan && s.state !== 'expired' && !s.plan.is_trial) {
    return `Тариф начнётся сразу после оплаты и будет действовать по ${until}. Остаток текущего тарифа не переносится.`
  }
  return `Тариф начнётся сразу после оплаты и будет действовать по ${until} включительно.`
})

async function choose(offer) {
  chosen.value = offer
  months.value = 1
  quote.value = null
  quoteError.value = ''
  await nextTick()
  document.getElementById('pay')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  if (!data.value?.payments_enabled) return
  await loadQuote()
}

function closeChosen() {
  chosen.value = null
  quote.value = null
  if (route.query.choose) router.replace({ query: { ...route.query, choose: undefined } })
}

async function pay() {
  if (!chosen.value) return
  payBusy.value = true
  try {
    const res = await webApi.createPayment(chosen.value.id, months.value)
    if (!res.confirmation_url) throw new Error('confirmation')
    window.location.href = res.confirmation_url
  } catch (err) {
    quoteError.value = errorText(err)
    payBusy.value = false
  }
}

// --- Возврат с ЮKassa: ?payment=id — перепроверяем статус, пока платёж ждёт ---

const payNotice = ref(null)
const POLL_MS = 3000
const POLL_TRIES = 20
let pollTimer = null

function closeNotice() {
  payNotice.value = null
  clearTimeout(pollTimer)
}

async function watchPayment(id, tries = 0) {
  let payment
  try {
    payment = (await webApi.checkPayment(id)).payment
  } catch (err) {
    payNotice.value = { cls: 'notice--warn', text: errorText(err) }
    return
  }
  if (payment.status === 'succeeded') {
    payNotice.value = payment.applied
      ? {
          cls: 'pay-notice--ok',
          text: `Оплата прошла — тариф «${payment.plan_name}» действует по ${formatDay(payment.last_day)} включительно.`,
        }
      : { cls: 'notice--warn', text: 'Оплата получена, но тариф не подключился автоматически — напишите в поддержку, подключим вручную.' }
    await load()
    return
  }
  if (payment.status === 'canceled' || payment.status === 'error') {
    payNotice.value = {
      cls: 'notice--warn',
      text: `Оплата не прошла${payment.cancel_reason ? `: ${payment.cancel_reason.toLowerCase()}` : ''}. Деньги не списаны — можно попробовать ещё раз.`,
    }
    await load()
    return
  }
  if (tries >= POLL_TRIES) {
    payNotice.value = {
      cls: 'notice--warn',
      text: 'ЮKassa ещё не подтвердила оплату. Как только подтвердит, тариф продлится сам — обновите страницу через пару минут.',
      url: payment.confirmation_url,
    }
    return
  }
  payNotice.value = { cls: '', text: 'Проверяем оплату…', url: '' }
  pollTimer = setTimeout(() => watchPayment(id, tries + 1), POLL_MS)
}

// --- Ссылка для друзей ---

const referral = ref(null)
const refError = ref('')

const daysText = (n) => `${n} ${plural(n, 'день', 'дня', 'дней')}`

const bonusText = computed(() => {
  const r = referral.value
  const b = r?.bonus || {}
  const parts = []
  if (b.referrer_days) parts.push(`вам +${daysText(b.referrer_days)} к тарифу`)
  if (b.invitee_days) parts.push(`другу +${daysText(b.invitee_days)}`)
  const got = r?.earned_days ? ` Вы уже получили ${daysText(r.earned_days)}.` : ''
  if (!parts.length) return got.trim()
  return `Бонус приходит, когда приглашённый оплатит тариф: ${parts.join(', ')}.${got}`
})

async function loadReferral() {
  refError.value = ''
  try {
    referral.value = await webApi.referral()
  } catch (err) {
    refError.value = errorText(err)
  }
}

async function copyRef() {
  try {
    await navigator.clipboard.writeText(referral.value.url)
    pushToast('Ссылка скопирована')
  } catch {
    pushToast('Не удалось скопировать — выделите ссылку вручную', 'error')
  }
}

// --- Промокод ---

const promo = ref('')
const promoBusy = ref(false)
const promoError = ref('')
const promoDone = ref('')

async function redeem() {
  promoBusy.value = true
  promoError.value = ''
  promoDone.value = ''
  try {
    const res = await webApi.redeemPromo(promo.value)
    promoDone.value = res.message
    promo.value = ''
    await load()
  } catch (err) {
    promoError.value = errorText(err)
  } finally {
    promoBusy.value = false
  }
}

onMounted(async () => {
  loadConfig()
  loadWorkspaces().catch(() => null)
  loadReferral()
  await load()
  const paymentId = Number(route.query.payment)
  if (paymentId) {
    router.replace({ query: { ...route.query, payment: undefined } })
    watchPayment(paymentId)
  }
  const chooseId = Number(route.query.choose)
  const offer = chooseId && data.value?.offers?.find((o) => o.id === chooseId)
  if (offer) choose(offer)
})

onBeforeUnmount(() => clearTimeout(pollTimer))
</script>

<style scoped>
.plan-duo {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 18px;
}
/* Оплата — белая карточка «Диалога» с чернильной рамкой: это активный шаг, а не подсказка. */
.pay-box {
  padding: 18px 20px;
  border: 1.5px solid var(--ink);
  border-radius: var(--r-card);
  background: var(--surface);
}
.plan-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 18px 20px;
}
.plan-cap {
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.plan-big {
  margin: 0;
  font-size: 26px;
  font-weight: 700;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}
.plan-big span {
  color: var(--muted);
  font-size: 14px;
  font-weight: 500;
}
.plan-dl {
  margin: 0;
}
.plan-dl > div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 7px 0;
  border-top: 1px solid var(--line);
  font-size: 14px;
}
.plan-dl dt {
  color: var(--muted);
}
.plan-dl dd {
  margin: 0;
  font-weight: 600;
  text-align: right;
}
.plan-acts {
  margin-top: auto;
}
.plan-meter {
  height: 10px;
}
.plan-muted {
  margin: 0;
  color: var(--muted);
  font-size: 14px;
  line-height: 1.5;
}
.plan-muted--bad {
  color: var(--danger);
}
.plan-sec {
  margin: 26px 0 0;
}
.plan-h {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
  margin: 0 0 12px;
  font-size: 17px;
}
.plan-h small {
  color: var(--muted);
  font-size: 13px;
  font-weight: 500;
}
.plan-duo--low {
  margin: 22px 0 0;
}
.plan-meta {
  font-size: 13px;
}
.pay-term {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin: 4px 0 12px;
}
.pay-term__label {
  color: var(--muted);
  font-size: 14px;
}
.pay-term__off {
  margin-left: 4px;
  font-size: 12px;
  font-weight: 600;
  opacity: 0.75;
}
.pay-sum {
  max-width: 480px;
  margin-bottom: 10px;
}
.pay-sum__total dd {
  font-size: 17px;
  font-variant-numeric: tabular-nums;
}
.plan-irow {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 10px;
}
/* Узкая карточка (телефон): кнопка уходит под поле, а не за край. */
.plan-irow .field {
  flex: 1 1 180px;
  min-width: 0;
  margin: 0;
}
.plan-irow .field input {
  width: 100%;
}
.plan-note {
  margin-top: 8px;
  font-size: 13px;
}
.table-scroll.ccard .table {
  margin: 0;
}
@media (max-width: 760px) {
  .plan-duo {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
