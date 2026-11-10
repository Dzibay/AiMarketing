<template>
  <section class="panel" id="ai-usage">
    <div class="panel__head">
      <div>
        <h2>Лимит периода</h2>
        <p v-if="usage" class="hint">с {{ formatDate(usage.period_start) }} · новый период — {{ formatDate(usage.period_end) }}</p>
      </div>
      <span v-if="usage" class="chip" :class="chip.cls">{{ chip.text }}</span>
    </div>
    <p v-if="!usage" class="empty">Не удалось загрузить расход.</p>
    <template v-else>
      <template v-if="usage.limited">
        <div
          class="meter"
          :class="tone"
          role="progressbar"
          :aria-valuenow="usage.used_percent"
          aria-valuemin="0"
          aria-valuemax="100"
        >
          <span :style="{ width: `${usage.used_percent}%` }"></span>
        </div>
        <p class="sync-status" :class="usage.exhausted ? 'is-error' : ''">
          <template v-if="usage.exhausted">
            Лимит исчерпан — ИИ-ассистент, Вордстат и картинки недоступны до {{ formatDate(usage.period_end) }}
            или до перехода на тариф выше
          </template>
          <template v-else>
            Использовано {{ pct(usage.used_percent) }} · осталось {{ pct(usage.left_percent) }} · обновится
            {{ formatDate(usage.period_end) }}
          </template>
        </p>
        <p class="hint">Остаток не переносится: в новом периоде лимит снова полный.</p>
      </template>
      <p v-else class="hint">Лимита нет. Ниже — на что уходит расход в этом периоде.</p>
      <p v-if="!usage.by_kind.length" class="empty">В этом периоде ИИ, Вордстат и картинки ещё не использовались.</p>
      <div v-else class="usage-split">
        <div v-for="group in groups" :key="group.key">
          <h3 class="subhead">{{ group.title }}</h3>
          <ul class="usage-shares">
            <li v-for="row in group.rows" :key="row.id">
              <span>
                {{ row.label }}
                <small>{{ num(row.calls) }} {{ plural(row.calls, 'запрос', 'запроса', 'запросов') }}</small>
              </span>
              <strong>{{ pct(row.percent) }}</strong>
            </li>
          </ul>
        </div>
      </div>
      <template v-if="usage.by_day?.length > 1 && usage.by_kind.length">
        <h3 class="subhead">По дням</h3>
        <TrendChart
          :points="usage.by_day"
          value-key="percent"
          :label="usage.limited ? 'Доля лимита' : 'Доля расхода'"
          :format-value="pct"
          :height="160"
        />
      </template>
    </template>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import TrendChart from './TrendChart.vue'
import { formatDate, num, pct, plural } from '../utils/format'

/** Шкала лимита за период из GET /api/web/plan (usage) или /api/web/ai/usage. */
const props = defineProps({ usage: { type: Object, default: null } })

const tone = computed(() => {
  const p = props.usage?.used_percent ?? 0
  return p >= 100 ? 'is-over' : p >= 80 ? 'is-near' : ''
})

const chip = computed(() => {
  const u = props.usage
  if (!u?.limited) return { text: 'без лимита', cls: '' }
  if (u.exhausted) return { text: 'исчерпан', cls: 'chip--warning' }
  return { text: `осталось ${pct(u.left_percent)}`, cls: u.used_percent >= 80 ? 'chip--warning' : 'chip--ok' }
})

// С лимитом — доли лимита, без лимита — доли всего расхода за период.
const groups = computed(() => {
  const u = props.usage
  if (!u) return []
  return [
    { key: 'kind', title: u.limited ? 'Куда ушёл лимит' : 'Куда ушёл расход', rows: u.by_kind },
    { key: 'project', title: 'По проектам', rows: u.by_project },
  ]
})
</script>
