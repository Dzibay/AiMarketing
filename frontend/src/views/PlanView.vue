<template>
  <p class="page-lead">Условия, расход лимита за период, оплата и переход на другой тариф.</p>

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

    <section class="panel">
      <div class="panel__head">
        <div>
          <h2>{{ sub.plan ? sub.plan.name : 'Без тарифа' }}</h2>
          <p class="hint">{{ termText }}</p>
        </div>
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

    <UsagePanel v-if="data.usage" :usage="data.usage" />

    <section class="panel" id="offers">
      <div class="panel__head">
        <h2>{{ sub.state === 'expired' ? 'Продлить или сменить тариф' : 'Тарифы' }}</h2>
      </div>
      <p class="hint">Цена — за срок тарифа; скидка за переход с вашего тарифа уже учтена.</p>

      <div v-if="chosen" id="pay" class="pay-box" role="region" aria-label="Оплата тарифа">
        <div class="pay-box__head">
          <h3>
            {{ chosen.current ? 'Продление' : 'Переход на' }} «{{ chosen.name }}» на {{ chosen.days }}
            {{ plural(chosen.days, 'день', 'дня', 'дней') }}
          </h3>
          <button class="icon-btn" type="button" aria-label="Закрыть" @click="closeChosen">
            <X aria-hidden="true" />
          </button>
        </div>

        <p v-if="!data.payments_enabled" class="pay-box__text">
          Онлайн-оплата пока не подключена. Чтобы {{ chosen.current ? 'продлить' : 'перейти на' }} «{{ chosen.name }}»
          сейчас, напишите в поддержку — переключим тариф вручную.
        </p>
        <p v-else-if="quoteError" class="form-error">{{ quoteError }}</p>
        <p v-else-if="!quote" class="empty">Считаем стоимость…</p>
        <template v-else>
          <p class="pay-box__price">
            <strong>{{ rubles(quote.amount_rub, 2) }}</strong>
            <small>за {{ quote.days }} {{ plural(quote.days, 'день', 'дня', 'дней') }}</small>
          </p>
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

    <section class="panel">
      <div class="panel__head">
        <h2>Промокод</h2>
      </div>
      <form class="filters" @submit.prevent="redeem">
        <div v-field class="field field--wide">
          <label>Код</label>
          <input v-model.trim="promo" type="text" maxlength="32" autocomplete="off" placeholder="Например, START" />
        </div>
        <button class="btn btn--accent" type="submit" :disabled="promoBusy || !promo">Активировать</button>
      </form>
      <p v-if="promoError" class="form-error">{{ promoError }}</p>
      <p v-else-if="promoDone" class="form-ok">{{ promoDone }}</p>
      <p class="hint">Промокод может продлить подписку, подключить тариф или добавить лимит ИИ на текущий период.</p>
    </section>

    <section v-if="data.payments?.length" class="panel">
      <div class="panel__head">
        <h2>Платежи</h2>
      </div>
      <div class="table-scroll">
        <table class="table">
          <thead>
            <tr>
              <th>Когда</th>
              <th>Тариф</th>
              <th>Статус</th>
              <th>Срок</th>
              <th class="num">Сумма</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in data.payments" :key="p.id">
              <td>{{ formatDateTime(p.created_at) }}</td>
              <td>
                {{ p.plan_name || '—' }}
                <small class="cell-note">{{ p.kind === 'renew' ? 'продление' : 'переход' }}</small>
              </td>
              <td>
                <span class="chip" :class="statusChip(p)">{{ p.status_label }}</span>
                <span v-if="p.test" class="chip">тест</span>
                <small v-if="p.cancel_reason" class="cell-note">{{ p.cancel_reason }}</small>
                <a v-if="p.confirmation_url" class="cell-note" :href="p.confirmation_url">Оплатить</a>
              </td>
              <td>{{ p.last_day ? `по ${formatDay(p.last_day)}` : '—' }}</td>
              <td class="num">
                {{ rubles(p.amount_rub, 2) }}
                <small v-if="p.refunded_rub" class="cell-note">возврат {{ rubles(p.refunded_rub, 2) }}</small>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="hint">Возврат — по запросу в поддержку, деньги вернутся тем же способом оплаты.</p>
    </section>

    <section v-if="history.length" class="panel">
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
import { Check, Lock, X } from '@lucide/vue'
import PlanOffers from '../components/PlanOffers.vue'
import UsagePanel from '../components/UsagePanel.vue'
import { errorText, webApi } from '../api/web'
import { loadPlan, usePlan } from '../composables/usePlan'
import { useSiteConfig } from '../composables/useSiteConfig'
import { loadWorkspaces } from '../composables/useWorkspaces'
import { useHead } from '../utils/useHead'
import { formatDateTime, formatDay, isoDay, plural, rubles } from '../utils/format'

useHead('Тариф — AiMarketing', true)

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

const termText = computed(() => {
  const s = sub.value
  if (s.state === 'expired') return `Закончилась ${formatDay(s.last_day)}`
  if (s.last_day) return `Действует по ${formatDay(s.last_day)} включительно`
  return 'Бессрочно'
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
  quote.value = null
  quoteError.value = ''
  await nextTick()
  document.getElementById('pay')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  if (!data.value?.payments_enabled) return
  try {
    quote.value = await webApi.paymentQuote(offer.id)
  } catch (err) {
    quoteError.value = errorText(err)
  }
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
    const res = await webApi.createPayment(chosen.value.id)
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
