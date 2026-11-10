<template>
  <section class="panel">
    <div class="panel__head">
      <div>
        <h2>Ждёт решения</h2>
        <p class="hint">
          Предложения ИИ без вашего решения {{ project ? `в проекте «${project.name}»` : 'во всех проектах' }}. В Директе
          ничего не меняется, пока вы не нажмёте «Выполнить».
        </p>
      </div>
      <button class="btn btn--ghost" type="button" :disabled="loading" @click="load">Обновить</button>
    </div>

    <p v-if="loading && !items.length" class="empty">Загружаем…</p>
    <p v-else-if="loadError" class="empty">{{ loadError }}</p>
    <div v-else-if="!items.length" class="pending-empty">
      <p class="empty">Решений ничего не ждёт. Новые предложения ИИ появятся здесь и в чатах.</p>
      <RouterLink class="btn btn--accent" :to="chatHome">Открыть ИИ-помощника</RouterLink>
    </div>
    <template v-else>
      <p v-if="total > items.length" class="hint">Показаны первые {{ items.length }} из {{ total }}.</p>
      <section v-for="g in groups" :key="g.key" class="pending-group">
        <h3 class="pending-group__title">
          <RouterLink v-if="g.project" :to="`/w/${g.project.id}/summary`">{{ g.project.name }}</RouterLink>
          <span v-else>Без проекта</span>
          <span class="chip">{{ g.items.length }}</span>
        </h3>
        <div class="pending-group__list">
          <article v-for="a in g.items" :key="a.id" class="action-card" :data-action="a.id">
            <div class="action-card__head">
              <strong>{{ a.label }}</strong>
              <span class="hint">{{ formatDateTime(a.created_at) }}</span>
            </div>
            <p v-if="a.summary" class="action-card__summary">{{ a.summary }}</p>
            <p v-else-if="a.kind === 'ask'" class="action-card__summary">{{ ASK_TEXT[a.tool] || ASK_TEXT.ask_questions }}</p>
            <AiChanges v-if="a.changes?.length" :changes="a.changes" />
            <p v-if="a.reason" class="hint">Зачем: {{ a.reason }}</p>
            <p v-if="a.categories?.length" class="action-card__cats">
              <span v-for="c in a.categories" :key="c.id" class="chip">{{ c.label }}</span>
            </p>
            <p v-if="a.kind === 'write' && a.items?.length" class="hint">
              Строки можно снять только в чате — здесь действие выполнится целиком.
            </p>
            <div class="action-card__buttons">
              <template v-if="a.kind === 'write'">
                <button class="btn btn--accent" type="button" :disabled="!!busy[a.id]" @click="decide(a, true)">
                  Выполнить
                </button>
                <button class="btn btn--ghost" type="button" :disabled="!!busy[a.id]" @click="decide(a, false)">
                  Отклонить
                </button>
              </template>
              <RouterLink class="btn" :class="a.kind === 'ask' ? 'btn--accent' : 'btn--ghost'" :to="chatLink(a)">
                {{ a.kind === 'ask' ? 'Ответить в чате' : 'Открыть в чате' }}
              </RouterLink>
              <span class="hint">{{ a.chat.general ? 'Общий чат' : 'Чат' }} «{{ a.chat.title }}»</span>
            </div>
          </article>
        </div>
      </section>
    </template>
  </section>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { aiApi, errorText } from '../api/web'
import { refreshPending } from '../composables/usePending'
import { useProjectFilter } from '../composables/useProjectFilter'
import { pushToast } from '../composables/useToasts'
import { formatDateTime } from '../utils/format'
import { useHead } from '../utils/useHead'
import AiChanges from './workspace/AiChanges.vue'

useHead('Ждёт решения — AiMarketing', true)

// Карточки вопросов, плана и таблицы решаются только в чате: там весь контекст ответа.
const ASK_TEXT = {
  ask_questions: 'ИИ задал вопросы — ответьте в чате, чтобы он продолжил.',
  propose_plan: 'ИИ предложил план — согласуйте шаги в чате.',
  propose_items: 'ИИ предложил таблицу — отметьте нужные строки в чате.',
}

const { projectId, project } = useProjectFilter()

const items = ref([])
const total = ref(0)
const loading = ref(false)
const loadError = ref('')
const busy = reactive({})

const chatHome = computed(() => (projectId.value ? `/w/${projectId.value}/chat` : '/cabinet/chat'))

/** По проектам — в порядке первой карточки; общие вопросы без проекта — в конце. */
const groups = computed(() => {
  const map = new Map()
  for (const a of items.value) {
    const key = a.project ? `p${a.project.id}` : 'none'
    if (!map.has(key)) map.set(key, { key, project: a.project, items: [] })
    map.get(key).items.push(a)
  }
  return [...map.values()].sort((x, y) => Number(!x.project) - Number(!y.project))
})

function chatLink(a) {
  const base = a.chat.general || !a.project ? '/cabinet/chat' : `/w/${a.project.id}/chat`
  return `${base}/${a.chat.id}`
}

let seq = 0
async function load() {
  const n = ++seq
  loading.value = true
  loadError.value = ''
  try {
    const res = await aiApi.pending(projectId.value)
    if (n !== seq) return
    items.value = res.items || []
    total.value = res.total || items.value.length
  } catch (err) {
    if (n === seq) loadError.value = errorText(err)
  } finally {
    if (n === seq) loading.value = false
  }
  refreshPending()
}

async function decide(a, approve) {
  busy[a.id] = true
  try {
    const res = await aiApi.decide(a.id, approve, false, [])
    items.value = items.value.filter((x) => x.id !== a.id)
    total.value = Math.max(0, total.value - 1)
    if (!approve) pushToast('Отклонено')
    else if (res.pending) {
      pushToast(`Одобрено. В чате «${a.chat.title}» ещё без решения: ${res.pending} — ИИ продолжит, когда решите все`)
    } else pushToast(`Выполняем — ход ИИ продолжится в чате «${a.chat.title}»`)
  } catch (err) {
    pushToast(errorText(err), 'error')
    load()
  } finally {
    delete busy[a.id]
    refreshPending()
  }
}

watch(projectId, load, { immediate: true })
</script>

<style scoped>
.pending-empty {
  display: grid;
  justify-items: center;
  gap: 0.75rem;
  padding-bottom: 1rem;
}

.pending-group + .pending-group {
  margin-top: 1.4rem;
}

.pending-group__title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin: 0 0 0.6rem;
  font-size: 1rem;
}

.pending-group__title a {
  color: inherit;
}

.pending-group__list {
  display: grid;
  gap: 0.75rem;
}
</style>
