<template>
  <section class="cask" aria-label="Вопрос ИИ про кампании">
    <div class="cask__ctx">
      <Sparkles :size="15" aria-hidden="true" />
      <template v-if="picked.length">
        <b>Выбрано {{ picked.length }}:</b>
        <span v-for="c in picked" :key="c.campaign_row_id" class="cask__chip">
          {{ c.name }}
          <button type="button" :aria-label="`Убрать «${c.name}»`" @click="$emit('unselect', c.campaign_row_id)">
            <X :size="12" aria-hidden="true" />
          </button>
        </span>
      </template>
      <span v-else>
        Вопрос про <b>{{ allText }}</b> — или отметьте нужные галочкой
      </span>
    </div>
    <form class="hask__box cask__box" @submit.prevent="ask()">
      <textarea
        v-model="text"
        rows="1"
        maxlength="4000"
        placeholder="Спросите про эти кампании…"
        aria-label="Вопрос про кампании"
        @keydown.enter.exact.prevent="ask()"
      />
      <button class="hask__send" type="submit" :disabled="!text.trim()" aria-label="Спросить">
        <ArrowUp aria-hidden="true" />
      </button>
    </form>
    <div class="hask__chips">
      <button v-for="s in starters" :key="s" class="hask__chip" type="button" @click="ask(s)">{{ s }}</button>
    </div>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowUp, Sparkles, X } from '@lucide/vue'
import { putHomeQuestion } from '../composables/useHomeAsk'
import { plural } from '../utils/format'

/**
 * Вопрос ИИ про кампании списка (макет «Диалог»): про отмеченные галочкой или про все.
 * wsId — список одного проекта; без него кампании разных проектов (у строк есть project).
 */
const props = defineProps({
  rows: { type: Array, required: true },
  selected: { type: Set, required: true },
  wsId: { type: Number, default: null },
})
defineEmits(['unselect'])

const router = useRouter()
const text = ref('')

const picked = computed(() => props.rows.filter((c) => props.selected.has(c.campaign_row_id)))
const allText = computed(() => {
  const n = props.rows.length
  return `${n === 1 ? 'эту' : 'все'} ${n} ${plural(n, 'кампанию', 'кампании', 'кампаний')}`
})
const starters = computed(() =>
  picked.value.length
    ? ['Почему у них дорогая заявка?', 'Что из этого отключить?', 'Сравни их между собой']
    : ['Какие тратят впустую?', 'Что отключить?', 'Куда добавить бюджет?'],
)

// Один проект у всех выбранных — его чат: там ИИ видит кампании целиком. Иначе — общий чат.
const projectId = computed(() => {
  if (props.wsId) return props.wsId
  const ids = new Set((picked.value.length ? picked.value : props.rows).map((c) => c.project?.id).filter(Boolean))
  return ids.size === 1 ? [...ids][0] : null
})

function campaignLine(c) {
  const project = !props.wsId && !projectId.value && c.project?.name ? `проект «${c.project.name}», ` : ''
  return `«${c.name}» (${project}campaign_row_id ${c.campaign_row_id})`
}

function ask(preset) {
  const q = (preset || text.value).trim()
  if (!q) return
  const scope = picked.value.length
    ? `Речь о кампаниях: ${picked.value.map(campaignLine).join(', ')}.`
    : props.wsId || projectId.value
      ? 'Речь о всех кампаниях проекта, кроме архивных.'
      : 'Речь о всех кампаниях во всех проектах, кроме архивных.'
  putHomeQuestion(`${q}\n\n${scope}`)
  router.push({ path: projectId.value ? `/w/${projectId.value}/chat` : '/cabinet/chat', query: { ask: 'text' } })
}
</script>

<style scoped>
.cask {
  position: sticky;
  bottom: 0;
  z-index: 5;
  margin-top: 24px;
  padding: 14px 0 16px;
  background: linear-gradient(to bottom, transparent, var(--bg) 18px);
}
.cask__ctx {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 8px;
  margin-bottom: 10px;
  color: var(--muted);
  font-size: 13px;
}
.cask__ctx b {
  color: var(--ink);
}
.cask__chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 4px 2px 10px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--surface);
  color: var(--ink);
}
.cask__chip button {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border: 0;
  border-radius: 50%;
  background: none;
  color: var(--muted);
  cursor: pointer;
}
.cask__chip button:hover {
  background: color-mix(in srgb, var(--ink) 7%, transparent);
  color: var(--ink);
}
.cask__box textarea {
  min-height: 28px;
}
</style>
