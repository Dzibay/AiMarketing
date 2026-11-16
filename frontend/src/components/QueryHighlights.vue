<template>
  <template v-if="costly.length">
    <h2 class="section-h">
      Самые дорогие запросы без заявок
      <small class="cgroup__n">ещё не убраны в минус</small>
    </h2>
    <div class="ccard qhl">
      <div v-for="q in costly" :key="`${q.project?.id || ''}:${q.query_norm}`" class="prow">
        <div class="prow__main">
          <b class="qhl__q">{{ q.query }}</b>
          <small>{{ where(q) }}</small>
        </div>
        <div class="prow__acts">
          <span class="qhl__cost">{{ rubles(q.cost_rub) }}</span>
          <RouterLink class="btn btn--ghost btn--sm" :to="chatLink(q)">
            <Sparkles :size="15" aria-hidden="true" />
            Разобрать с ИИ
          </RouterLink>
        </div>
      </div>
    </div>
  </template>

  <h2 class="section-h">Что уже убрано</h2>
  <div class="ccard qhl qhl--done">
    <div class="prow">
      <div class="prow__main">
        <span class="qhl__big">
          <span class="done-check" aria-hidden="true"><Check /></span>
          <template v-if="removed.phrases">
            <b><CountUp :value="num(removed.phrases)" /></b>
            {{ plural(removed.phrases, 'минус-фраза', 'минус-фразы', 'минус-фраз') }} за 30 дней
          </template>
          <template v-else>За 30 дней минус-фраз не добавляли</template>
        </span>
        <p v-if="removed.phrases">
          Отсекли {{ num(removed.queries) }} {{ plural(removed.queries, 'запрос', 'запроса', 'запросов') }} — по ним
          реклама больше не показывается.
        </p>
        <p v-else>Когда отминусуете мусорные запросы, здесь появится, сколько уже убрано.</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { RouterLink } from 'vue-router'
import { Check, Sparkles } from '@lucide/vue'
import CountUp from './ui/CountUp.vue'
import { niceQuotes, num, plural, rubles } from '../utils/format'

const props = defineProps({
  /** Дорогие запросы без заявок: query, query_norm, cost_rub, clicks, campaigns, project? */
  costly: { type: Array, default: () => [] },
  /** Минус-фразы за 30 дней: phrases, queries. */
  removed: { type: Object, default: () => ({ phrases: 0, queries: 0 }) },
  /** Проект строки — для ссылки в чат; без него — текущий. */
  workspaceId: { type: [Number, String], default: null },
})

function where(q) {
  const clicks = `${num(q.clicks)} ${plural(q.clicks, 'клик', 'клика', 'кликов')}, заявок нет`
  const camps = (q.campaigns || []).map((c) => c.name).filter(Boolean)
  const place = q.project
    ? niceQuotes(q.project.name)
    : camps.slice(0, 1).join('') + (camps.length > 1 ? ` и ещё ${camps.length - 1}` : '')
  return [clicks, place].filter(Boolean).join(' · ')
}

function chatLink(q) {
  const id = q.project?.id || props.workspaceId
  const cid = q.campaigns?.length === 1 ? String(q.campaigns[0].id) : undefined
  return { path: `/w/${id}/chat`, query: { ask: 'query', q: q.query, cid } }
}
</script>

<style scoped>
.qhl__q {
  overflow-wrap: anywhere;
}
.qhl__cost {
  color: var(--ink);
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.qhl__big {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 15px;
}
.qhl__big b {
  font-size: 20px;
}
@media (max-width: 720px) {
  .qhl .prow__acts {
    justify-content: space-between;
  }
}
</style>
