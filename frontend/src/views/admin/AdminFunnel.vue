<template>
  <div class="adm-dash" :class="{ 'is-loading': loading && data }">
    <div class="adm-dash__bar">
      <AdminPeriod :period="data?.period" :query="query" :steps="false" :disabled="loading && !data" @period="setPeriod" />
      <UiSelect
        v-if="data"
        :model-value="source"
        :options="data.options"
        size="sm"
        aria-label="Источник"
        @update:model-value="setSource"
      />
      <span v-if="data" class="adm-dash__versus">{{ versusNote }}</span>
    </div>

    <p v-if="error && !data" class="panel empty">{{ error }}</p>
    <div v-else-if="!data" class="panel"><p class="empty">Загружаем…</p></div>
    <section v-else class="panel">
      <div class="panel__head"><h2>Воронка</h2></div>
      <p class="hint">
        Люди, зарегистрированные за период, и сколько из них дошли до каждого шага (когда бы ни дошли). «Визит» —
        посетители лендинга за тот же период: это вход воронки, а не те же самые люди. Время шага — медиана от
        предыдущего шага у тех, кто прошёл оба.
      </p>
      <div v-scroll-x class="table-scroll">
        <table class="table adm-costs__table adm-fun">
          <thead>
            <tr>
              <th>Шаг</th>
              <th class="adm-fun__barcol"></th>
              <th class="num">Людей</th>
              <th class="num">От прошлого шага</th>
              <th class="num">От визитов</th>
              <th class="num">От регистраций</th>
              <th class="num">Время шага</th>
              <th class="num">Прошлый период</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in data.steps" :key="s.key">
              <td>{{ s.label }}</td>
              <td class="adm-fun__barcol">
                <span class="adm-fun__bar"><i :style="{ width: `${barWidth(s)}%` }"></i></span>
              </td>
              <td class="num"><b>{{ num(s.count) }}</b></td>
              <td class="num" :class="dropTone(s.of_prev)">{{ s.of_prev != null ? pct(s.of_prev) : '—' }}</td>
              <td class="num">{{ s.of_first != null ? pct(s.of_first) : '—' }}</td>
              <td class="num">{{ s.of_registered != null ? pct(s.of_registered) : '—' }}</td>
              <td class="num">{{ duration(s.median_hours) }}</td>
              <td class="num">
                <template v-if="s.prev_count != null">
                  {{ num(s.prev_count) }}
                  <DeltaChip v-if="s.change != null" :value="s.change" good="up" versus="к прошлому периоду" />
                </template>
                <span v-else class="cell-note">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import DeltaChip from '../../components/DeltaChip.vue'
import UiSelect from '../../components/ui/UiSelect.vue'
import AdminPeriod from './AdminPeriod.vue'
import { queryOne, useAdminLoad } from './useAdminLoad'
import { useAdminPeriod } from './useAdminPeriod'
import { adminApi } from '../../api/admin'
import { num, pct } from '../../utils/format'

/** Воронка (docs/admin-plan.md, этап 7): шаги, доли, типичное время, срез по источнику, сравнение с прошлым периодом. */
const route = useRoute()
const router = useRouter()
const { query, setPeriod } = useAdminPeriod()
const source = computed(() => queryOne(route, 'source'))
const params = computed(() => ({ ...query.value, source: source.value }))
const { data, loading, error } = useAdminLoad(adminApi.funnel, params)

const setSource = (v) => router.push({ query: { ...route.query, source: v || undefined } })
const versusNote = computed(() => {
  const prev = data.value.period.prev
  if (!prev) return 'За всё время — сравнивать не с чем'
  return `Сравнение — с ${prev.from.split('-').reverse().join('.')} по ${prev.to.split('-').reverse().join('.')}`
})

const top = computed(() => Math.max(1, ...data.value.steps.map((s) => s.count)))
const barWidth = (s) => Math.round((s.count / top.value) * 100)
const dropTone = (v) => (v == null ? '' : v < 20 ? 'tone-bad' : v < 50 ? 'tone-warn' : '')

function duration(hours) {
  if (hours == null) return '—'
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} мин`
  if (hours < 48) return `${num(hours, hours < 10 ? 1 : 0)} ч`
  return `${num(hours / 24, hours < 240 ? 1 : 0)} дн`
}
</script>

<style scoped>
.adm-fun__barcol {
  width: 30%;
  min-width: 140px;
}

.adm-fun__bar {
  display: block;
  height: 10px;
  border-radius: 5px;
  background: var(--line);
  overflow: hidden;
}

.adm-fun__bar i {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--c-green);
}
</style>
