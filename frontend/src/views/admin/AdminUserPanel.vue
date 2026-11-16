<template>
  <Teleport to="body">
    <div v-if="userId" class="aup-layer">
      <div class="aup-backdrop" @click="$emit('close')"></div>
      <aside ref="panel" class="aup" role="dialog" aria-modal="true" :aria-label="u ? u.email || `Пользователь #${u.id}` : 'Пользователь'" tabindex="-1">
        <button class="aup__close" type="button" aria-label="Закрыть" @click="$emit('close')">×</button>
        <p v-if="!card" class="empty">{{ failed ? 'Не удалось загрузить пользователя.' : 'Загружаем…' }}</p>
        <template v-else>
          <header class="aup__head">
            <div class="aup__who">
              <span class="act-mark" :class="`act-${activityLevel(u.last_seen_at)}`" v-tip="activityText(u.last_seen_at)"></span>
              <div>
                <h2>{{ u.email || `#${u.id}` }}</h2>
                <p class="cell-note">
                  #{{ u.id }}<template v-if="u.name"> · {{ u.name }}</template> · с {{ formatDay(u.created_at) }}
                  · {{ activityText(u.last_seen_at) }}
                </p>
                <p class="aup__badges">
                  <span v-if="src" class="aup__src">
                    <i v-if="src.color" :style="{ background: src.color }"></i>{{ src.text }}<small v-if="src.note"> · {{ src.note }}</small>
                  </span>
                  <span v-else class="cell-note">источник неизвестен — до учёта визитов</span>
                  <span v-if="u.has_yandex" class="chip">Яндекс ID</span>
                  <span v-if="u.telegram_linked" class="chip">Telegram</span>
                  <span v-if="!u.email_verified" class="chip chip--warning">почта не подтверждена</span>
                  <span v-if="u.blocked" class="chip chip--bad" v-tip="u.blocked_reason">заблокирован</span>
                </p>
              </div>
            </div>
            <div class="aup__ribbon" aria-label="Заходы за 30 дней">
              <span
                v-for="d in card.days"
                :key="d.day"
                :class="d.actions ? 'is-act' : d.visits ? 'is-visit' : ''"
                v-tip="ribbonTip(d)"
              ></span>
              <small class="cell-note">30 дней: {{ visitedDays }} с заходами, {{ actedDays }} с действиями</small>
            </div>
            <div class="aup__actions">
              <button class="btn btn--accent btn--sm" type="button" :disabled="busy" @click="openEdit">Изменить подписку</button>
              <button v-if="!u.email_verified" class="btn btn--ghost btn--sm" type="button" :disabled="busy" @click="verifyEmail">
                Подтвердить почту
              </button>
              <button v-if="!u.blocked" class="btn btn--ghost btn--sm" type="button" :disabled="busy" @click="blockUser">Заблокировать</button>
              <button v-else class="btn btn--ghost btn--sm" type="button" :disabled="busy" @click="unblockUser">Разблокировать</button>
            </div>
          </header>

          <nav class="seg aup__tabs" aria-label="Разделы пользователя">
            <button v-for="t in TABS" :key="t.id" type="button" :class="{ active: tab === t.id }" @click="setTab(t.id)">
              {{ t.label }}<span v-if="t.id === 'support' && u.support_unread" class="aup__dot">{{ u.support_unread }}</span>
            </button>
          </nav>

          <!-- Обзор -->
          <div v-if="tab === 'overview'" class="aup__body">
            <section class="aup-box">
              <h3>Подписка</h3>
              <p>
                <strong>{{ tariff.title }}</strong>
                <span v-if="tariff.note" class="cell-note" :class="tariff.tone"> · {{ tariff.note }}</span>
              </p>
              <ul class="aup-facts">
                <li v-if="hasOverrides(u.subscription)">
                  <span>Свои условия</span><b>{{ overridesText(u.subscription.overrides) }}</b>
                </li>
                <li v-if="u.subscription.bonus_units">
                  <span>Бонус промокода</span><b>+{{ num(u.subscription.bonus_units, 2) }} ед. в этом периоде</b>
                </li>
                <li>
                  <span>Период лимита</span>
                  <b>{{ formatRange(u.subscription.period_start?.slice(0, 10), periodLast) }}</b>
                </li>
                <li>
                  <span>Израсходовано</span>
                  <b>
                    {{ num(u.usage.spent, 2) }}<template v-if="u.usage.limit !== null"> из {{ num(u.usage.limit, 2) }}</template> ед.
                    <template v-if="pct !== null"> · {{ pct }}%</template>
                  </b>
                </li>
                <li>
                  <span>Проектов по тарифу</span><b>{{ u.subscription.projects_limit ?? 'без лимита' }}</b>
                </li>
              </ul>
              <div v-if="pct !== null" class="meter" :class="pct >= 100 ? 'is-over' : pct >= 80 ? 'is-near' : ''">
                <span :style="{ width: `${Math.min(100, pct)}%` }"></span>
              </div>
            </section>
            <section class="aup-box">
              <h3>Итоги</h3>
              <div class="aup-totals">
                <div><small>Оплачено</small><strong>{{ rubles(u.paid.rub) }}</strong></div>
                <div><small>Срок</small><strong>{{ u.paid.days ? termText(u.paid.days) : '—' }}</strong></div>
                <div><small>Платежей</small><strong>{{ num(u.paid.count) }}</strong></div>
                <div><small>Расход ИИ</small><strong>{{ rubAi(u.ai_cost_rub) }}</strong></div>
                <div>
                  <small>Маржа</small><strong :class="{ 'tone-bad': u.margin_rub < 0 }">{{ rubles(u.margin_rub) }}</strong>
                </div>
              </div>
            </section>
            <section class="aup-box">
              <h3>Проекты и Директ</h3>
              <p v-if="!card.project_list.length" class="cell-note">Проектов нет.</p>
              <ul v-else class="aup-facts">
                <li v-for="p in card.project_list" :key="p.id" :class="{ 'is-muted': p.archived }">
                  <span>{{ p.name }}<template v-if="p.archived"> · в архиве</template></span>
                  <b>
                    <template v-if="p.direct">Директ: {{ p.direct }} · {{ p.campaigns }} {{ plural(p.campaigns, 'кампания', 'кампании', 'кампаний') }}</template>
                    <template v-else>Директ не подключён</template>
                  </b>
                </li>
              </ul>
            </section>
          </div>

          <!-- Платежи -->
          <div v-else-if="tab === 'payments'" class="aup__body">
            <p v-if="!payments" class="empty">Загружаем…</p>
            <p v-else-if="!payments.length" class="empty">Платежей нет.</p>
            <div v-else v-scroll-x class="table-scroll">
              <table class="table">
                <thead>
                  <tr>
                    <th>Когда</th>
                    <th>Тариф</th>
                    <th>Срок</th>
                    <th class="num">Сумма</th>
                    <th>Статус</th>
                    <th>Чек</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="p in payments" :key="p.id">
                    <td>{{ formatDateTime(p.paid_at || p.created_at) }}<small class="cell-note">№ {{ p.id }}</small></td>
                    <td>{{ p.plan_name || '—' }}<small class="cell-note">{{ p.kind === 'renew' ? 'продление' : 'переход' }}</small></td>
                    <td>{{ termText(p.days) }}<small v-if="p.last_day" class="cell-note">по {{ formatDay(p.last_day) }}</small></td>
                    <td class="num">
                      {{ rubles(p.amount_rub, 2) }}
                      <small v-if="p.refunded_rub" class="cell-note tone-bad">возврат {{ rubles(p.refunded_rub, 2) }}</small>
                    </td>
                    <td>
                      <span class="chip" :class="paymentChip(p)">{{ p.status_label }}</span>
                      <span v-if="p.test" class="chip">тест</span>
                    </td>
                    <td>
                      <template v-if="p.status === 'succeeded' && !p.test">{{ p.receipt_done ? 'пробит' : 'нет' }}</template>
                      <span v-else class="cell-note">—</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <!-- ИИ -->
          <div v-else-if="tab === 'ai'" class="aup__body">
            <section class="aup-box">
              <h3>Расход за 30 дней: {{ rubAi(ai30.cost) }} · {{ num(ai30.units, 2) }} ед.</h3>
              <div class="aup-bars" role="img" :aria-label="`Расход ИИ по дням, всего ${rubAi(ai30.cost)}`">
                <span
                  v-for="d in card.ai_days"
                  :key="d.day"
                  :style="{ height: `${barHeight(d.cost_rub)}%` }"
                  v-tip="`${formatDate(d.day)}: ${rubAi(d.cost_rub)} · ${num(d.units, 2)} ед.`"
                ></span>
              </div>
            </section>
            <section class="aup-box">
              <h3>По задачам за 30 дней</h3>
              <p v-if="!card.ai_tasks.length" class="cell-note">Расхода нет.</p>
              <ul v-else class="aup-facts">
                <li v-for="t in card.ai_tasks" :key="t.task">
                  <span>{{ t.label }}</span>
                  <b>{{ rubAi(t.cost_rub) }} · {{ num(t.units, 2) }} ед. · {{ num(t.calls) }} {{ plural(t.calls, 'вызов', 'вызова', 'вызовов') }}</b>
                </li>
              </ul>
            </section>
            <section class="aup-box">
              <h3>Чаты</h3>
              <p v-if="!card.chats.length" class="cell-note">Чатов нет.</p>
              <div v-else v-scroll-x class="table-scroll">
                <table class="table">
                  <thead>
                    <tr>
                      <th>Чат</th>
                      <th class="num">Сообщений</th>
                      <th class="num">Стоимость</th>
                      <th>Последнее</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="c in card.chats" :key="c.id">
                      <td>
                        {{ c.title || 'Без названия' }}
                        <small class="cell-note">{{ c.general ? 'общий' : c.project }}<template v-if="c.archived"> · в архиве</template></small>
                      </td>
                      <td class="num">{{ num(c.messages) }}</td>
                      <td class="num">{{ rubAi(c.cost_rub) }}</td>
                      <td>{{ formatDateTime(c.last_at || c.created_at) }}</td>
                      <td>
                        <button class="btn btn--ghost btn--xs" type="button" :disabled="!c.messages" @click="openChat(c)">Читать</button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>
          </div>

          <!-- События -->
          <div v-else-if="tab === 'events'" class="aup__body">
            <p v-if="!events" class="empty">Загружаем…</p>
            <p v-else-if="!events.length" class="empty">Событий пока нет.</p>
            <div v-else v-scroll-x class="table-scroll">
              <table class="table">
                <thead>
                  <tr>
                    <th>Когда</th>
                    <th>Событие</th>
                    <th>Подробности</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(e, i) in events" :key="i">
                    <td>{{ formatDateTime(e.created_at) }}</td>
                    <td>{{ e.label }}</td>
                    <td class="cell-note">{{ metaText(e.meta) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <!-- Обращения -->
          <div v-else-if="tab === 'support'" class="aup__body">
            <p v-if="!ticket" class="empty">Загружаем…</p>
            <template v-else>
              <p v-if="!ticket.messages.length" class="empty">Обращений нет.</p>
              <div v-else class="admin-sup">
                <div v-for="m in ticket.messages" :key="m.id" class="sup-msg" :class="`is-${m.author}`">
                  <small class="sup-msg__who">{{ m.author === 'admin' ? 'Поддержка' : 'Пользователь' }}</small>
                  <a v-if="m.has_image && images[m.id]" :href="images[m.id]" target="_blank" rel="noopener" class="sup-msg__img">
                    <img :src="images[m.id]" alt="Скриншот" />
                  </a>
                  <span v-else-if="m.has_image" class="sup-msg__img is-loading">Скриншот…</span>
                  <p v-if="m.body">{{ m.body }}</p>
                  <small class="sup-msg__time">
                    {{ formatDateTime(m.created_at) }}<template v-if="contextText(m.context)"> · {{ contextText(m.context) }}</template>
                  </small>
                </div>
              </div>
              <p class="hint">
                Ответ придёт пользователю в виджет и колокольчик, в Telegram — если привязан, иначе письмом.<template
                  v-if="ticket.topic"
                > Копия уйдёт в тему группы поддержки.</template>
              </p>
              <textarea v-model="reply" class="input" rows="3" maxlength="4000" placeholder="Ответ пользователю"></textarea>
              <div class="aup__send">
                <button class="btn btn--accent" type="button" :disabled="sending || !reply.trim()" @click="sendReply">Ответить</button>
              </div>
            </template>
          </div>
        </template>
      </aside>
    </div>
  </Teleport>

  <Modal :open="!!edit" :title="edit ? `Подписка ${u?.email || `#${userId}`}` : ''" :locked="saving" wide @close="edit = null">
    <form v-if="edit" id="sub-form" class="form-grid" @submit.prevent="saveSubscription">
      <p v-if="editError" class="form-error">{{ editError }}</p>
      <p class="hint">
        Сейчас: <b>{{ u.subscription?.plan?.name || 'без тарифа' }}</b>, {{ subTermText(u.subscription) }}. Новый тариф
        начинает период заново с сегодняшнего дня.
      </p>
      <div v-field class="field">
        <label>Тариф</label>
        <UiSelect v-model="edit.plan_id" :options="planOptions" />
      </div>
      <div v-field class="field">
        <label>Последний день подписки</label>
        <UiDatePicker v-model="edit.until" clearable clear-label="Без срока" />
        <small class="field-hint">При смене тарифа без срока — срок тарифа с сегодняшнего дня. Прошедшая дата закрывает доступ.</small>
      </div>

      <h3>Свои условия поверх тарифа</h3>
      <div v-field class="field">
        <label>Единиц за период</label>
        <div class="admin-override">
          <UiSelect v-model="edit.units_mode" :options="limitModes('units_limit')" aria-label="Лимит единиц" />
          <input
            v-if="edit.units_mode === 'custom'"
            v-model="edit.units_limit"
            type="number"
            min="0"
            step="any"
            inputmode="decimal"
            aria-label="Единиц за период"
            required
          />
        </div>
      </div>
      <div v-field class="field">
        <label>Проектов</label>
        <div class="admin-override">
          <UiSelect v-model="edit.projects_mode" :options="limitModes('projects_limit')" aria-label="Лимит проектов" />
          <input
            v-if="edit.projects_mode === 'custom'"
            v-model="edit.projects_limit"
            type="number"
            min="0"
            step="1"
            inputmode="numeric"
            aria-label="Проектов"
            required
          />
        </div>
      </div>
      <fieldset>
        <legend>Функции</legend>
        <div v-for="(label, key) in features" :key="key" v-field class="field">
          <label>{{ label }}</label>
          <UiSelect v-model="edit.features[key]" :options="featureModes(key)" />
        </div>
      </fieldset>
    </form>
    <template #footer>
      <button
        class="btn btn--ghost"
        type="button"
        :disabled="saving || !u?.subscription?.plan"
        v-tip="u?.subscription?.plan ? '' : 'Продлить можно только подписку с тарифом'"
        @click="renew"
      >
        Продлить на {{ u?.subscription?.plan?.days || '' }} дн.
      </button>
      <button class="btn btn--ghost" type="button" :disabled="saving" @click="edit = null">Отмена</button>
      <button class="btn btn--accent" type="submit" form="sub-form" :disabled="saving">{{ saving ? 'Сохраняем…' : 'Сохранить' }}</button>
    </template>
  </Modal>

  <AdminChatModal :chat="reading" @close="reading = null" />
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Modal from '../../components/ui/Modal.vue'
import UiDatePicker from '../../components/ui/UiDatePicker.vue'
import UiSelect from '../../components/ui/UiSelect.vue'
import AdminChatModal from './AdminChatModal.vue'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { askText } from '../../composables/useConfirm'
import { pushToast } from '../../composables/useToasts'
import { formatDate, formatDateTime, formatDay, formatRange, num, plural, rubAi, rubles } from '../../utils/format'
import { activityLevel, activityText, hasOverrides, sourceView, tariffView, termText, usedPct } from './adminUsers'

/**
 * Пользователь в боковой панели поверх списка (?user=ID, раздел — ?tab=…): шапка с меткой и лентой заходов
 * за 30 дней, обзор, платежи, расход ИИ с чатами, журнал событий, обращения. Окно подписки — отсюда же.
 */
const props = defineProps({
  userId: { type: Number, default: null },
  plans: { type: Array, default: () => [] },
  features: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['close', 'changed'])

const TABS = [
  { id: 'overview', label: 'Обзор' },
  { id: 'payments', label: 'Платежи' },
  { id: 'ai', label: 'ИИ' },
  { id: 'events', label: 'События' },
  { id: 'support', label: 'Обращения' },
]

const route = useRoute()
const router = useRouter()
const panel = ref(null)
const card = ref(null)
const failed = ref(false)
const busy = ref(false)
const payments = ref(null)
const events = ref(null)
const ticket = ref(null)
const images = ref({})
const reply = ref('')
const sending = ref(false)
const reading = ref(null)
const edit = ref(null)
const editError = ref('')
const saving = ref(false)

const u = computed(() => card.value?.user || null)
const src = computed(() => sourceView(u.value?.source))
const tariff = computed(() => tariffView(u.value))
const pct = computed(() => usedPct(u.value?.usage))
const tab = computed(() => (TABS.some((t) => t.id === route.query.tab) ? route.query.tab : 'overview'))
const visitedDays = computed(() => card.value?.days.filter((d) => d.visits || d.actions).length || 0)
const actedDays = computed(() => card.value?.days.filter((d) => d.actions).length || 0)
const ai30 = computed(() =>
  (card.value?.ai_days || []).reduce((s, d) => ({ cost: s.cost + d.cost_rub, units: s.units + d.units }), { cost: 0, units: 0 }),
)
const maxCost = computed(() => Math.max(0, ...(card.value?.ai_days || []).map((d) => d.cost_rub)))
// Конец периода — исключающий; показываем последний день.
const periodLast = computed(() => {
  const end = u.value?.subscription?.period_end
  if (!end) return null
  return new Date(new Date(end).getTime() - 1).toLocaleDateString('sv-SE', { timeZone: 'Europe/Moscow' })
})
const planOptions = computed(() => [
  { value: null, label: 'Без тарифа — без ограничений' },
  ...props.plans.map((p) => ({
    value: p.id,
    label: `${p.name}${p.is_trial ? ' (пробный)' : ''}${p.active ? '' : ' — выключен'} · ${p.days} дн.`,
  })),
])

function barHeight(cost) {
  if (!maxCost.value || !cost) return 0
  return Math.max(4, Math.round((cost / maxCost.value) * 100))
}

function ribbonTip(d) {
  const day = formatDate(d.day)
  if (!d.visits && !d.actions) return `${day}: не заходил`
  return `${day}: ${d.visits} ${plural(d.visits, 'заход', 'захода', 'заходов')}, ${d.actions} ${plural(d.actions, 'действие', 'действия', 'действий')}`
}

function paymentChip(p) {
  if (p.status === 'succeeded') return p.refunded_rub ? 'chip--warning' : 'chip--ok'
  if (p.status === 'canceled' || p.status === 'error') return 'chip--bad'
  return 'chip--info'
}

function dayText(day) {
  if (!day) return ''
  const [y, m, d] = day.split('-')
  return `${d}.${m}.${y}`
}

function subTermText(sub) {
  if (!sub?.last_day) return 'бессрочно'
  return `${sub.expired ? 'закончилась' : 'по'} ${dayText(sub.last_day)}`
}

function overridesText(o) {
  const parts = []
  if ('units_limit' in o) parts.push(o.units_limit === null ? 'единицы без лимита' : `${num(o.units_limit, 2)} ед.`)
  if ('projects_limit' in o) parts.push(o.projects_limit === null ? 'проекты без лимита' : `проектов: ${o.projects_limit}`)
  for (const [k, v] of Object.entries(o.features || {})) parts.push(`${props.features[k] || k}: ${v ? 'открыта' : 'закрыта'}`)
  return parts.join(' · ')
}

const META_LABELS = {
  via: (v) => (v === 'yandex' ? 'через Яндекс' : v === 'email' ? 'по почте' : v),
  by: (v) => (v === 'admin' ? 'админом' : v),
  auto: (v) => (v ? 'автоматически' : 'выбран пользователем'),
  renew: (v) => (v ? 'продление' : ''),
  last_day: (v) => (v ? `по ${dayText(v)}` : 'бессрочно'),
  all: (v) => (v ? 'все предложения' : ''),
}

function metaText(meta) {
  return Object.entries(meta || {})
    .map(([k, v]) => (META_LABELS[k] ? META_LABELS[k](v) : `${k}: ${v}`))
    .filter(Boolean)
    .join(' · ')
}

function contextText(ctx) {
  if (!ctx) return ''
  return [ctx.page, ctx.workspace ? `проект «${ctx.workspace.name}» #${ctx.workspace.id}` : '', ctx.plan ? `тариф ${ctx.plan}` : '', ctx.browser]
    .filter(Boolean)
    .join(' · ')
}

// --- Загрузка ---

function dropImages() {
  for (const url of Object.values(images.value)) if (url) URL.revokeObjectURL(url)
  images.value = {}
}

async function loadCard() {
  const id = props.userId
  try {
    const data = await adminApi.userCard(id)
    if (props.userId === id) card.value = data
  } catch (err) {
    if (props.userId !== id) return
    failed.value = true
    pushToast(errorText(err), 'error')
  }
}

async function loadTab(name) {
  const id = props.userId
  if (!id) return
  try {
    if (name === 'payments' && !payments.value) {
      const data = await adminApi.payments({ userId: id, limit: 300 })
      if (props.userId === id) payments.value = data.items || []
    } else if (name === 'events' && !events.value) {
      const data = await adminApi.events(id, 200)
      if (props.userId === id) events.value = data.items || []
    } else if (name === 'support' && !ticket.value) {
      showTicket(await adminApi.support(id), id)
      // Открыли — новые прочитаны.
      if (card.value?.user && props.userId === id && card.value.user.support_unread) {
        card.value.user.support_unread = 0
        emit('changed')
      }
    }
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

function showTicket(data, id) {
  if (props.userId !== id) return
  ticket.value = { messages: data.messages || [], topic: data.topic }
  for (const m of ticket.value.messages) {
    if (!m.has_image || images.value[m.id] !== undefined) continue
    images.value = { ...images.value, [m.id]: '' }
    adminApi
      .supportImage(m.id)
      .then((url) => (images.value = { ...images.value, [m.id]: url }))
      .catch(() => {})
  }
}

function reset() {
  card.value = null
  failed.value = false
  payments.value = null
  events.value = null
  ticket.value = null
  reply.value = ''
  edit.value = null
  reading.value = null
  dropImages()
}

watch(
  () => props.userId,
  async (id) => {
    reset()
    if (!id) return
    await loadCard()
    loadTab(tab.value)
    await nextTick()
    panel.value?.focus({ preventScroll: true })
  },
  { immediate: true },
)
watch(tab, (name) => loadTab(name))

function setTab(id) {
  router.replace({ query: { ...route.query, tab: id === 'overview' ? undefined : id } })
}

// Изменилось что-то, что видно в списке: перечитываем карточку и просим список обновиться.
async function refresh() {
  await loadCard()
  emit('changed')
}

// --- Действия ---

async function verifyEmail() {
  busy.value = true
  try {
    await adminApi.verifyEmail(props.userId)
    pushToast('Почта отмечена подтверждённой')
    await refresh()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function blockUser() {
  const reason = await askText('Причина (необязательно)', {
    title: `Заблокировать ${u.value.email || 'пользователя'}?`,
    confirmText: 'Заблокировать',
    danger: true,
  })
  if (reason == null) return
  busy.value = true
  try {
    await adminApi.blockUser(props.userId, reason)
    await refresh()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function unblockUser() {
  busy.value = true
  try {
    await adminApi.unblockUser(props.userId)
    await refresh()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function sendReply() {
  const id = props.userId
  if (!reply.value.trim()) return
  sending.value = true
  try {
    const data = await adminApi.supportReply(id, reply.value.trim())
    reply.value = ''
    showTicket(data, id)
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    sending.value = false
  }
}

async function openChat(c) {
  try {
    reading.value = await adminApi.chat(c.id)
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

// --- Подписка ---

function modeOf(overrides, key) {
  if (!(key in overrides)) return 'plan'
  return overrides[key] === null ? 'none' : 'custom'
}

function openEdit() {
  const sub = u.value.subscription || {}
  const o = sub.overrides || {}
  const featureOverrides = o.features || {}
  editError.value = ''
  edit.value = {
    plan_id: sub.plan?.id ?? null,
    until: sub.last_day || '',
    units_mode: modeOf(o, 'units_limit'),
    units_limit: o.units_limit ?? '',
    projects_mode: modeOf(o, 'projects_limit'),
    projects_limit: o.projects_limit ?? '',
    features: Object.fromEntries(
      Object.keys(props.features).map((k) => [k, k in featureOverrides ? (featureOverrides[k] ? 'on' : 'off') : 'plan']),
    ),
  }
}

function selectedPlan() {
  return props.plans.find((p) => p.id === edit.value?.plan_id) || null
}

function limitModes(key) {
  const p = selectedPlan()
  const text = !p || p[key] === null ? 'без лимита' : num(p[key], 2)
  return [
    { value: 'plan', label: `как в тарифе (${text})` },
    { value: 'none', label: 'без лимита' },
    { value: 'custom', label: 'своё значение' },
  ]
}

function featureModes(key) {
  const p = selectedPlan()
  const on = !p || p.features?.[key] !== false
  return [
    { value: 'plan', label: `как в тарифе (${on ? 'есть' : 'нет'})` },
    { value: 'on', label: 'открыта' },
    { value: 'off', label: 'закрыта' },
  ]
}

function overridesOf(e) {
  const out = {}
  if (e.units_mode === 'none') out.units_limit = null
  if (e.units_mode === 'custom') out.units_limit = Number(e.units_limit)
  if (e.projects_mode === 'none') out.projects_limit = null
  if (e.projects_mode === 'custom') out.projects_limit = Number(e.projects_limit)
  const f = Object.fromEntries(
    Object.entries(e.features)
      .filter(([, v]) => v !== 'plan')
      .map(([k, v]) => [k, v === 'on']),
  )
  if (Object.keys(f).length) out.features = f
  return out
}

// Отправляем только изменённое: тариф без новой даты получает срок тарифа с сегодняшнего дня.
function changedPayload(e) {
  const sub = u.value.subscription || {}
  const payload = { overrides: overridesOf(e) }
  if (e.plan_id !== (sub.plan?.id ?? null)) payload.plan_id = e.plan_id
  if ((e.until || '') !== (sub.last_day || '')) payload.until = e.until || null
  return payload
}

async function submitSubscription(payload, done) {
  saving.value = true
  editError.value = ''
  try {
    await adminApi.setSubscription(props.userId, payload)
    edit.value = null
    pushToast(done)
    await refresh()
  } catch (err) {
    editError.value = errorText(err)
  } finally {
    saving.value = false
  }
}

function saveSubscription() {
  return submitSubscription(changedPayload(edit.value), 'Подписка сохранена')
}

function renew() {
  const days = u.value.subscription?.plan?.days
  return submitSubscription({ renew: true }, `Подписка продлена на ${days} дн.`)
}

// Esc закрывает панель, если поверх неё нет окна.
function onKey(event) {
  if (event.key !== 'Escape' || !props.userId) return
  if (document.querySelector('.modal, .ui-pop, .ui-scrim')) return
  emit('close')
}
onMounted(() => document.addEventListener('keydown', onKey))
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKey)
  dropImages()
})
</script>

<style scoped>
.aup-layer {
  position: fixed;
  inset: 0;
  z-index: 55;
}
.aup-backdrop {
  position: absolute;
  inset: 0;
  background: var(--overlay);
  animation: aup-fade 0.2s ease;
}
.aup {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
  width: min(860px, 94vw);
  overflow-y: auto;
  padding: 24px 28px 32px;
  background: var(--bg);
  box-shadow: var(--shadow-pop);
  animation: aup-in 0.22s var(--ease-out);
  outline: none;
}
.aup__close {
  position: absolute;
  top: 14px;
  right: 16px;
  width: 36px;
  height: 36px;
  border: 0;
  border-radius: var(--pill);
  background: transparent;
  color: var(--muted);
  font-size: 24px;
  line-height: 1;
  cursor: pointer;
}
.aup__close:hover {
  background: var(--accent-soft);
  color: var(--ink);
}
.aup__head {
  display: grid;
  gap: 14px;
}
.aup__who {
  display: flex;
  gap: 14px;
  padding-right: 40px;
}
.aup__who .act-mark {
  align-self: stretch;
  width: 6px;
}
.aup__who h2 {
  margin: 0;
  font-size: 20px;
  overflow-wrap: anywhere;
}
.aup__who p {
  margin: 4px 0 0;
}
.aup__badges {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.aup__src {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
}
.aup__src i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.aup__src small {
  color: var(--muted);
  font-weight: 400;
}
.aup__ribbon {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 3px;
}
.aup__ribbon span {
  width: 14px;
  height: 18px;
  border-radius: 3px;
  background: color-mix(in srgb, var(--ink) 7%, transparent);
}
.aup__ribbon span.is-visit {
  background: color-mix(in srgb, var(--lime) 55%, var(--surface));
}
.aup__ribbon span.is-act {
  background: var(--lime);
  box-shadow: inset 0 0 0 1.5px color-mix(in srgb, var(--ink) 45%, transparent);
}
.aup__ribbon small {
  margin-left: 10px;
}
.aup__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.aup__tabs {
  align-self: flex-start;
}
.aup__dot {
  display: inline-grid;
  place-items: center;
  min-width: 18px;
  height: 18px;
  margin-left: 6px;
  padding: 0 5px;
  border-radius: var(--pill);
  background: var(--lime);
  color: var(--on-lime);
  font-size: 11px;
  font-weight: 700;
}
.aup__body {
  display: grid;
  gap: 14px;
}
.aup__send {
  display: flex;
  justify-content: flex-end;
}
.aup-box {
  padding: 16px 18px;
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
}
.aup-box h3 {
  margin: 0 0 10px;
  font-size: 15px;
}
.aup-box > p {
  margin: 0 0 10px;
}
.aup-facts {
  display: grid;
  gap: 6px;
  margin: 0 0 10px;
  padding: 0;
  list-style: none;
  font-size: 14px;
}
.aup-facts li {
  display: grid;
  grid-template-columns: minmax(140px, 220px) 1fr;
  gap: 12px;
}
.aup-facts li > span {
  color: var(--muted);
}
.aup-facts b {
  font-weight: 600;
}
.aup-totals {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
}
.aup-totals small {
  display: block;
  color: var(--muted);
  font-size: 12px;
}
.aup-totals strong {
  font-size: 18px;
}
.aup-bars {
  display: flex;
  align-items: flex-end;
  gap: 3px;
  height: 90px;
  border-bottom: 1px solid var(--line);
}
.aup-bars span {
  flex: 1;
  min-height: 1px;
  border-radius: 3px 3px 0 0;
  background: var(--ink);
}
.is-muted {
  opacity: 0.6;
}
@keyframes aup-in {
  from {
    transform: translateX(24px);
    opacity: 0;
  }
}
@keyframes aup-fade {
  from {
    opacity: 0;
  }
}
@media (prefers-reduced-motion: reduce) {
  .aup,
  .aup-backdrop {
    animation: none;
  }
}
</style>
