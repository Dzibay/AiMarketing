<template>
  <div class="adm-dash" :class="{ 'is-loading': loading && data }">
    <p v-if="error && !data" class="panel empty">{{ error }}</p>
    <div v-else-if="!data" class="panel"><p class="empty">Загружаем…</p></div>

    <template v-else>
      <section class="panel">
        <div class="panel__head">
          <h2>Какие модели отвечали</h2>
          <span class="adm-charts__total">{{ rubAi(data.total_rub) }}</span>
        </div>
        <p class="hint">Класс — назначенный задаче; если ответила запасная модель, вызов в строке запасной и в «подменой».</p>
        <p v-if="!data.items.length" class="empty">За период вызовов моделей нет.</p>
        <div v-else v-scroll-x class="table-scroll">
          <table class="table adm-costs__table">
            <thead>
              <tr>
                <th>Модель</th>
                <th>Класс</th>
                <th class="num">Вызовов</th>
                <th class="num" v-tip="'Сколько из этих вызовов модель сделала вместо назначенной'">Подменой</th>
                <th class="num">Вход · кэш</th>
                <th class="num">Выход</th>
                <th class="num">₽</th>
                <th class="num">За вызов</th>
                <th class="num">Доля</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in data.items" :key="`${row.model}:${row.model_class}`">
                <td>{{ row.model || '—' }}</td>
                <td>{{ row.class_name }}</td>
                <td class="num">{{ num(row.calls) }}</td>
                <td class="num">{{ row.substitutes ? num(row.substitutes) : '—' }}</td>
                <td class="num">{{ tokens(row.tokens_in) }}<small v-if="row.cache_share" class="cell-note"> · {{ pct(row.cache_share, 0) }}</small></td>
                <td class="num">{{ tokens(row.tokens_out) }}</td>
                <td class="num">{{ rubAi(row.cost_rub) }}</td>
                <td class="num">{{ rubAi(row.per_call_rub) }}</td>
                <td class="num">{{ pct(row.share) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section v-if="data.lines.length" class="panel adm-charts__item">
        <div class="panel__head">
          <h2>Расход по моделям</h2>
        </div>
        <AdminChart
          title="Расход по моделям"
          :points="data.series"
          :series="series"
          :step="data.period.step"
          :last-day="data.period.to"
          :format-value="(v) => rubles(v, v && v < 10 ? 2 : 0)"
          :height="240"
          stacked
        />
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import AdminChart from './AdminChart.vue'
import { partColor, tokens, useCostsTab } from './aiCosts'
import { num, pct, rubAi, rubles } from '../../utils/format'

/** Модели: кто отвечал, подмены, токены и рубли; расход по дням — шесть самых дорогих и «остальные». */
const props = defineProps({ params: { type: Object, required: true } })
const emit = defineEmits(['period'])

const { data, loading, error } = useCostsTab('models', computed(() => props.params), null, (p) => emit('period', p))
const series = computed(() => data.value.lines.map((l, i) => ({ key: l.key, label: l.label, color: partColor(i), type: 'bar' })))
</script>
