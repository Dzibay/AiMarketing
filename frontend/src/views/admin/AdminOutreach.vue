<template>
  <div class="adm-costs">
    <p class="hint">
      Готовые списки людей, которым стоит написать самому: в Telegram, если привязан, или на почту. Рассылки из админки
      нет — списки пересчитываются при каждом открытии. Заблокированных здесь нет.
    </p>
    <p v-if="error && !lists" class="panel empty">{{ error }}</p>
    <div v-else-if="!lists" class="panel"><p class="empty">Загружаем…</p></div>
    <div v-else class="adm-out__cards">
      <button
        v-for="l in lists.lists"
        :key="l.key"
        type="button"
        class="adm-kpi adm-out__card"
        :class="{ 'is-active': l.key === key }"
        :aria-pressed="l.key === key"
        @click="open(l.key)"
      >
        <span class="adm-kpi__label">{{ l.label }}</span>
        <strong class="adm-out__count" :class="{ 'cell-note': !l.count }">{{ num(l.count) }}</strong>
        <span class="adm-kpi__note">{{ l.hint }}</span>
      </button>
    </div>

    <section v-if="key" class="panel">
      <div class="panel__head">
        <h2>{{ people?.label || '…' }}</h2>
        <span v-if="people" class="adm-charts__total">{{ n(people.total, 'человек', 'человека', 'человек') }}</span>
      </div>
      <p v-if="people?.hint" class="hint">{{ people.hint }}</p>
      <p v-if="peopleError && !people" class="empty">{{ peopleError }}</p>
      <p v-else-if="!people || peopleLoading" class="empty">Загружаем…</p>
      <p v-else-if="!people.items.length" class="empty">Сейчас в этом списке никого.</p>
      <div v-else v-scroll-x class="table-scroll">
        <table class="table adm-costs__table">
          <thead>
            <tr>
              <th>Кто</th>
              <th>Тариф</th>
              <th>Почему в списке</th>
              <th>Заходил</th>
              <th>Регистрация</th>
              <th>Куда писать</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in people.items" :key="p.user_id">
              <td>
                <RouterLink :to="{ path: `${ADMIN_BASE}/users`, query: { user: p.user_id } }">{{ p.email || `#${p.user_id}` }}</RouterLink>
                <small v-if="p.name" class="cell-note"> · {{ p.name }}</small>
              </td>
              <td>{{ p.plan || '—' }}</td>
              <td class="adm-costs__wrap">{{ p.reason }}</td>
              <td>{{ p.last_seen_at ? formatDateTime(p.last_seen_at) : 'никогда' }}</td>
              <td>{{ formatDay(p.created_at) }}</td>
              <td>
                <span v-if="p.telegram" class="chip chip--ok">Telegram</span>
                <a v-else-if="p.email" :href="`mailto:${p.email}`">почта</a>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="people && people.items.length < people.total" class="hint">Показаны первые {{ num(people.items.length) }}.</p>
    </section>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { ADMIN_BASE } from './adminNav'
import { queryOne, useAdminLoad } from './useAdminLoad'
import { adminApi } from '../../api/admin'
import { formatDateTime, formatDay, num, plural } from '../../utils/format'

/** Кому написать (docs/admin-plan.md, этап 7): карточки списков со счётчиками, выбранный список — ?list=… */
const route = useRoute()
const router = useRouter()
const key = computed(() => queryOne(route, 'list'))

const { data: lists, error } = useAdminLoad(adminApi.outreach)
const listParams = computed(() => key.value)
const {
  data: people,
  loading: peopleLoading,
  error: peopleError,
} = useAdminLoad((k) => (k ? adminApi.outreachList(k) : Promise.resolve(null)), listParams)

const n = (v, one, few, many) => `${num(v)} ${plural(v, one, few, many)}`
const open = (k) => router.push({ query: { ...route.query, list: k === key.value ? undefined : k } })
</script>

<style scoped>
.adm-out__cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
}

.adm-out__card {
  display: grid;
  align-content: start;
  gap: 6px;
  border: 0;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.adm-out__card.is-active {
  box-shadow: 0 0 0 2px var(--ink);
}

.adm-out__count {
  font-size: 26px;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
}
</style>
