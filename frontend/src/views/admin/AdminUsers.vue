<template>
  <section class="panel">
    <div class="panel__head">
      <h2>Пользователи</h2>
      <span class="chip">{{ num(total) }}</span>
    </div>
    <p class="hint">
      Расход — в единицах за текущий период пользователя (срок его тарифа; без тарифа — календарный месяц).
      Лимиты, проекты и функции берутся из тарифа; «свои условия» — правки поверх тарифа для одного пользователя.
    </p>
    <p v-if="loading" class="empty">Загружаем…</p>
    <p v-else-if="!users.length" class="empty">Пользователей пока нет.</p>
    <div v-else class="table-scroll">
      <table class="table admin-users">
        <thead>
          <tr>
            <th>Пользователь</th>
            <th>Регистрация</th>
            <th>Тариф</th>
            <th>Расход периода</th>
            <th class="num">Вызовов</th>
            <th class="num">Себестоимость</th>
            <th>Статус</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id" :class="{ 'is-muted': u.blocked }">
            <td>
              <strong>{{ u.email || '—' }}</strong>
              <small class="cell-note">
                #{{ u.id }}<template v-if="u.name"> · {{ u.name }}</template><template v-if="u.has_yandex"> · вход через Яндекс</template>
                <template v-if="u.telegram_linked"> · Telegram</template>
              </small>
              <template v-if="!u.email_verified">
                <span class="chip chip--warning">почта не подтверждена</span>
                <button class="btn btn--ghost btn--xs" type="button" :disabled="busyId === u.id" @click="verifyEmail(u)">
                  Подтвердить
                </button>
              </template>
            </td>
            <td>{{ formatDate(u.created_at) }}</td>
            <td class="admin-users__plan">
              <strong>{{ u.subscription?.plan?.name || 'Без тарифа' }}</strong>
              <small class="cell-note">{{ termText(u.subscription) }}</small>
              <span v-if="u.subscription?.expired" class="chip chip--bad">закончилась</span>
              <span v-if="hasOverrides(u.subscription)" class="chip chip--info">свои условия</span>
              <button class="btn btn--ghost btn--xs" type="button" :disabled="busyId === u.id" @click="openEdit(u)">
                Изменить
              </button>
            </td>
            <td class="admin-users__usage">
              <span>{{ num(u.usage?.spent, 2) }}<template v-if="limitOf(u) !== null"> из {{ num(limitOf(u), 2) }}</template> ед.</span>
              <div
                v-if="limitOf(u) !== null"
                class="meter"
                :class="tone(u)"
                role="progressbar"
                :aria-valuenow="usedPct(u)"
                aria-valuemin="0"
                aria-valuemax="100"
              >
                <span :style="{ width: `${usedPct(u)}%` }"></span>
              </div>
            </td>
            <td class="num">{{ num(u.usage?.calls) }}</td>
            <td class="num">{{ rubAi(u.usage?.cost_rub) }}</td>
            <td>
              <span v-if="u.blocked" class="tone-bad" v-tip="u.blocked_reason">
                заблокирован<template v-if="u.blocked_reason">: {{ u.blocked_reason }}</template>
              </span>
              <span v-else class="tone-ok">активен</span>
              <button class="btn btn--ghost btn--xs" type="button" @click="openEvents(u)">События</button>
              <button
                v-if="!u.blocked"
                class="btn btn--ghost btn--xs"
                type="button"
                :disabled="busyId === u.id"
                @click="blockUser(u)"
              >
                Заблокировать
              </button>
              <button v-else class="btn btn--ghost btn--xs" type="button" :disabled="busyId === u.id" @click="unblockUser(u)">
                Разблокировать
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>

  <Modal :open="!!edit" :title="edit ? `Подписка ${edit.user.email || `#${edit.user.id}`}` : ''" :locked="saving" wide @close="edit = null">
    <form v-if="edit" id="sub-form" class="form-grid" @submit.prevent="saveSubscription">
      <p v-if="editError" class="form-error">{{ editError }}</p>
      <p class="hint">
        Сейчас: <b>{{ edit.user.subscription?.plan?.name || 'без тарифа' }}</b>, {{ termText(edit.user.subscription) }}.
        Новый тариф начинает период заново с сегодняшнего дня.
      </p>
      <div v-field class="field">
        <label>Тариф</label>
        <UiSelect v-model="edit.plan_id" :options="planOptions" />
      </div>
      <div v-field class="field">
        <label>Последний день подписки</label>
        <UiDatePicker v-model="edit.until" clearable clear-label="Без срока" />
        <small class="field-hint">
          При смене тарифа без срока — срок тарифа с сегодняшнего дня. Прошедшая дата закрывает доступ.
        </small>
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
        :disabled="saving || !edit?.user.subscription?.plan"
        v-tip="edit?.user.subscription?.plan ? '' : 'Продлить можно только подписку с тарифом'"
        @click="renew"
      >
        Продлить на {{ edit?.user.subscription?.plan?.days || '' }} дн.
      </button>
      <button class="btn btn--ghost" type="button" :disabled="saving" @click="edit = null">Отмена</button>
      <button class="btn btn--accent" type="submit" form="sub-form" :disabled="saving">
        {{ saving ? 'Сохраняем…' : 'Сохранить' }}
      </button>
    </template>
  </Modal>

  <Modal :open="!!log" :title="log ? `События ${log.user.email || `#${log.user.id}`}` : ''" wide @close="log = null">
    <p v-if="log?.loading" class="empty">Загружаем…</p>
    <p v-else-if="!log?.items.length" class="empty">Событий пока нет.</p>
    <div v-else class="table-scroll">
      <table class="table">
        <thead>
          <tr>
            <th>Когда</th>
            <th>Событие</th>
            <th>Подробности</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(e, i) in log.items" :key="i">
            <td>{{ formatDateTime(e.created_at) }}</td>
            <td>{{ e.label }}</td>
            <td class="cell-note">{{ metaText(e.meta) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </Modal>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import Modal from '../../components/ui/Modal.vue'
import UiDatePicker from '../../components/ui/UiDatePicker.vue'
import UiSelect from '../../components/ui/UiSelect.vue'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { askText } from '../../composables/useConfirm'
import { pushToast } from '../../composables/useToasts'
import { formatDate, formatDateTime, num, rubAi } from '../../utils/format'

const loading = ref(true)
const users = ref([])
const total = ref(0)
const plans = ref([])
const planOptions = computed(() => [
  { value: null, label: 'Без тарифа — без ограничений' },
  ...plans.value.map((p) => ({
    value: p.id,
    label: `${p.name}${p.is_trial ? ' (пробный)' : ''}${p.active ? '' : ' — выключен'} · ${p.days} дн.`,
  })),
])
const features = ref({})
const busyId = ref(null)
const edit = ref(null)
const editError = ref('')
const saving = ref(false)
const log = ref(null)

const limitOf = (u) => u.usage?.limit ?? null

function usedPct(u) {
  const limit = limitOf(u)
  if (limit === null) return 0
  if (limit <= 0) return 100
  return Math.min(100, Math.round(((u.usage?.spent || 0) / limit) * 100))
}

function tone(u) {
  const p = usedPct(u)
  return p >= 100 ? 'is-over' : p >= 80 ? 'is-near' : ''
}

function dayText(day) {
  if (!day) return ''
  const [y, m, d] = day.split('-')
  return `${d}.${m}.${y}`
}

function termText(sub) {
  if (!sub?.last_day) return 'бессрочно'
  return `${sub.expired ? 'закончилась' : 'по'} ${dayText(sub.last_day)}`
}

function hasOverrides(sub) {
  const o = sub?.overrides || {}
  return 'units_limit' in o || 'projects_limit' in o || Object.keys(o.features || {}).length > 0
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

async function load() {
  try {
    const [data, planData] = await Promise.all([adminApi.users(), adminApi.plans()])
    users.value = data.items || []
    total.value = data.total || 0
    plans.value = planData.items || []
    features.value = planData.features || {}
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    loading.value = false
  }
}

function replace(id, patch) {
  users.value = users.value.map((u) => (u.id === id ? { ...u, ...patch } : u))
}

// Ответ блокировки — неполный пользователь: берём только статус, остальное в строке не трогаем.
function blockState(res) {
  return { blocked: res.blocked, blocked_at: res.blocked_at, blocked_reason: res.blocked_reason }
}

// --- Подписка ---

function modeOf(overrides, key) {
  if (!(key in overrides)) return 'plan'
  return overrides[key] === null ? 'none' : 'custom'
}

function openEdit(u) {
  const sub = u.subscription || {}
  const o = sub.overrides || {}
  const featureOverrides = o.features || {}
  editError.value = ''
  edit.value = {
    user: u,
    plan_id: sub.plan?.id ?? null,
    until: sub.last_day || '',
    units_mode: modeOf(o, 'units_limit'),
    units_limit: o.units_limit ?? '',
    projects_mode: modeOf(o, 'projects_limit'),
    projects_limit: o.projects_limit ?? '',
    features: Object.fromEntries(
      Object.keys(features.value).map((k) => [k, k in featureOverrides ? (featureOverrides[k] ? 'on' : 'off') : 'plan']),
    ),
  }
}

function selectedPlan() {
  return plans.value.find((p) => p.id === edit.value?.plan_id) || null
}

function planLimitText(key) {
  const p = selectedPlan()
  if (!p || p[key] === null) return 'без лимита'
  return num(p[key], 2)
}

function planFeature(key) {
  const p = selectedPlan()
  return !p || p.features?.[key] !== false
}

function limitModes(key) {
  return [
    { value: 'plan', label: `как в тарифе (${planLimitText(key)})` },
    { value: 'none', label: 'без лимита' },
    { value: 'custom', label: 'своё значение' },
  ]
}

function featureModes(key) {
  return [
    { value: 'plan', label: `как в тарифе (${planFeature(key) ? 'есть' : 'нет'})` },
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
  const sub = e.user.subscription || {}
  const payload = { overrides: overridesOf(e) }
  if (e.plan_id !== (sub.plan?.id ?? null)) payload.plan_id = e.plan_id
  if ((e.until || '') !== (sub.last_day || '')) payload.until = e.until || null
  return payload
}

async function submitSubscription(payload, done) {
  const u = edit.value.user
  saving.value = true
  editError.value = ''
  try {
    const sub = await adminApi.setSubscription(u.id, payload)
    edit.value = null
    pushToast(done)
    // Период и лимит могли смениться — расход пересчитываем целиком.
    const fresh = await adminApi.users()
    users.value = fresh.items || users.value
    if (!fresh.items) replace(u.id, { subscription: sub })
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
  const days = edit.value.user.subscription?.plan?.days
  return submitSubscription({ renew: true }, `Подписка продлена на ${days} дн.`)
}

async function verifyEmail(u) {
  busyId.value = u.id
  try {
    await adminApi.verifyEmail(u.id)
    replace(u.id, { email_verified: true })
    pushToast('Почта отмечена подтверждённой')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busyId.value = null
  }
}

async function openEvents(u) {
  log.value = { user: u, items: [], loading: true }
  try {
    const data = await adminApi.events(u.id)
    if (log.value?.user.id === u.id) log.value = { user: u, items: data.items || [], loading: false }
  } catch (err) {
    log.value = null
    pushToast(errorText(err), 'error')
  }
}

// --- Блокировка ---

async function blockUser(u) {
  const reason = await askText('Причина (необязательно)', {
    title: `Заблокировать ${u.email || 'пользователя'}?`,
    confirmText: 'Заблокировать',
    danger: true,
  })
  if (reason == null) return
  busyId.value = u.id
  try {
    replace(u.id, blockState(await adminApi.blockUser(u.id, reason)))
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busyId.value = null
  }
}

async function unblockUser(u) {
  busyId.value = u.id
  try {
    replace(u.id, blockState(await adminApi.unblockUser(u.id)))
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busyId.value = null
  }
}

onMounted(load)
</script>
