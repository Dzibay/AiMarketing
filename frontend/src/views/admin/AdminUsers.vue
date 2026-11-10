<template>
  <section class="panel">
    <div class="panel__head">
      <h2>Пользователи</h2>
      <span class="chip">{{ num(total) }}</span>
    </div>
    <p class="hint">
      Расход — в единицах за текущий месяц. Общий лимит:
      <b>{{ defaultLimit === null ? 'без лимита' : `${num(defaultLimit, 2)} ед. в месяц` }}</b> — меняется во вкладке
      «ИИ и сервисы». Свой лимит пользователя заменяет общий; 0 — платные вызовы закрыты.
    </p>
    <p v-if="loading" class="empty">Загружаем…</p>
    <p v-else-if="!users.length" class="empty">Пользователей пока нет.</p>
    <div v-else class="table-scroll">
      <table class="table admin-users">
        <thead>
          <tr>
            <th>Пользователь</th>
            <th>Регистрация</th>
            <th>Расход месяца</th>
            <th>Лимит</th>
            <th class="num">Вызовов</th>
            <th class="num">Себестоимость</th>
            <th>Статус</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id" :class="{ 'is-muted': u.blocked }">
            <td>
              <strong>{{ u.email || '—' }}</strong>
              <span v-if="u.is_admin" class="chip chip--info">админ</span>
              <small class="cell-note">
                #{{ u.id }}<template v-if="u.name"> · {{ u.name }}</template><template v-if="u.has_yandex"> · вход через Яндекс</template>
                <template v-if="u.telegram_linked"> · Telegram</template>
              </small>
            </td>
            <td>{{ formatDate(u.created_at) }}</td>
            <td class="admin-users__usage">
              <span>{{ num(u.usage.spent, 2) }}<template v-if="u.usage.limit !== null"> из {{ num(u.usage.limit, 2) }}</template> ед.</span>
              <div
                v-if="u.usage.limit !== null"
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
            <td>
              <button class="btn btn--ghost btn--xs" type="button" :disabled="busyId === u.id" @click="editLimit(u)">
                {{ u.usage.own_limit === null ? 'общий' : `свой: ${num(u.usage.own_limit, 2)}` }}
              </button>
            </td>
            <td class="num">{{ num(u.usage.calls) }}</td>
            <td class="num">{{ rubAi(u.usage.cost_rub) }}</td>
            <td>
              <span v-if="u.blocked" class="tone-bad" :title="u.blocked_reason">
                заблокирован<template v-if="u.blocked_reason">: {{ u.blocked_reason }}</template>
              </span>
              <span v-else class="tone-ok">активен</span>
              <button
                v-if="!u.blocked"
                class="btn btn--ghost btn--xs btn--danger"
                type="button"
                :disabled="busyId === u.id || u.is_admin"
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
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { askText } from '../../composables/useConfirm'
import { pushToast } from '../../composables/useToasts'
import { formatDate, num, rubAi } from '../../utils/format'

const loading = ref(true)
const users = ref([])
const total = ref(0)
const defaultLimit = ref(null)
const busyId = ref(null)

function usedPct(u) {
  const { spent, limit } = u.usage
  if (limit === null) return 0
  if (limit <= 0) return 100
  return Math.min(100, Math.round((spent / limit) * 100))
}

function tone(u) {
  const p = usedPct(u)
  return p >= 100 ? 'is-over' : p >= 80 ? 'is-near' : ''
}

async function load() {
  try {
    const data = await adminApi.users()
    users.value = data.items || []
    total.value = data.total || 0
    defaultLimit.value = data.default_units_limit ?? null
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

async function editLimit(u) {
  const value = await askText('Единиц в месяц', {
    title: `Лимит для ${u.email || `#${u.id}`}`,
    message: 'Пусто — общий лимит сервиса. 0 — закрыть ИИ, Вордстат и картинки.',
    value: u.usage.own_limit ?? '',
    placeholder: defaultLimit.value === null ? 'общий: без лимита' : `общий: ${num(defaultLimit.value, 2)}`,
    type: 'number',
  })
  if (value === null) return
  busyId.value = u.id
  try {
    const res = await adminApi.setLimit(u.id, value.trim() === '' ? null : Number(value))
    const own = res.units_limit ?? null
    replace(u.id, { usage: { ...u.usage, own_limit: own, limit: own ?? defaultLimit.value } })
    pushToast('Лимит сохранён')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busyId.value = null
  }
}

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
