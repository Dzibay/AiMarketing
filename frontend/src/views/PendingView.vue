<template>
  <div class="ws-toolbar">
    <p class="sync-status">
      Предложения ИИ {{ project ? `по сайту «${project.name}»` : 'по всем сайтам' }}, которые ждут вашего решения. В
      Директе ничего не меняется, пока вы не нажмёте «Выполнить».
    </p>
  </div>

  <div v-if="loading && !items.length" class="ccard ccard--note">Загружаем…</div>
  <div v-else-if="loadError" class="ccard ccard--note tone-bad">{{ loadError }}</div>
  <EmptyState
    v-else-if="!items.length"
    :icon="Check"
    text="Решений ничего не ждёт. Новые предложения ИИ появятся здесь, на главной и в чатах."
  >
    <template #action><RouterLink class="btn btn--accent" :to="chatHome">Спросить ИИ</RouterLink></template>
  </EmptyState>
  <template v-else>
    <section v-for="g in groups" :key="g.key">
      <h2 class="section-h pend-h">
        <RouterLink v-if="g.project" :to="`/w/${g.project.id}/summary`">{{ g.project.name }}</RouterLink>
        <span v-else>Все сайты</span>
        <span class="cgroup__n">{{ g.items.length }}</span>
      </h2>
      <div class="ccard">
        <article v-for="a in g.items" :key="a.id" class="prow pend" :data-action="a.id">
          <div class="prow__main">
            <small>
              {{ formatDateTime(a.created_at) }} · {{ a.chat.general ? 'общий чат' : 'чат' }} «{{ a.chat.title }}»
            </small>
            <b>{{ a.label }}</b>
            <p v-if="a.summary">{{ a.summary }}</p>
            <p v-else-if="a.kind === 'ask'">{{ ASK_TEXT[a.tool] || ASK_TEXT.ask_questions }}</p>
            <AiChanges v-if="a.changes?.length" class="pend__changes" :changes="a.changes" />
            <p v-if="a.reason" class="pend__why">Зачем: {{ a.reason }}</p>
            <p v-if="a.categories?.length" class="pend__cats">
              <span v-for="c in a.categories" :key="c.id" class="chip">{{ c.label }}</span>
            </p>
            <p v-if="a.kind === 'write' && a.items?.length" class="pend__why">
              Отдельные строки снимаются только в чате — здесь действие выполнится целиком.
            </p>
          </div>
          <div class="prow__acts">
            <template v-if="a.kind === 'write'">
              <button class="btn btn--accent" type="button" :disabled="!!busy[a.id]" @click="decide(a, true)">
                Выполнить
              </button>
              <button class="btn btn--ghost" type="button" :disabled="!!busy[a.id]" @click="decide(a, false)">
                Отклонить
              </button>
            </template>
            <RouterLink class="btn" :class="a.kind === 'ask' ? 'btn--accent' : 'btn--ghost'" :to="chatLink(a)">
              {{ a.kind === 'ask' ? 'Ответить в чате' : 'Открыть чат' }}
            </RouterLink>
          </div>
        </article>
      </div>
    </section>
    <p v-if="total > items.length" class="meta-note">Показаны первые {{ items.length }} из {{ total }}.</p>
  </template>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { Check } from '@lucide/vue'
import EmptyState from '../components/ui/EmptyState.vue'
import { aiApi, errorText } from '../api/web'
import { refreshPending, usePending } from '../composables/usePending'
import { useProjectFilter } from '../composables/useProjectFilter'
import { pushToast } from '../composables/useToasts'
import { formatDateTime } from '../utils/format'
import { useHead } from '../utils/useHead'
import AiChanges from './workspace/AiChanges.vue'

useHead('Ждёт решения — Толк', true)

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
// Решённые здесь: запрос списка, ушедший до решения, не должен вернуть карточку обратно.
const decided = new Set()

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
    items.value = (res.items || []).filter((a) => !decided.has(a.id))
    total.value = Math.max(items.value.length, (res.total || 0) - (res.items || []).length + items.value.length)
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
    decided.add(a.id)
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

// Кнопки «Обновить» нет: шапка раз в минуту и при возврате на вкладку пересчитывает «Ждёт решения» —
// изменилось число (новое предложение или решение в чате) — подтягиваем список.
const { pendingCount } = usePending()
watch(pendingCount, () => {
  if (!loading.value) load()
})
</script>

<style scoped>
.pend-h {
  display: flex;
  align-items: center;
  gap: 8px;
}
.pend-h a {
  color: inherit;
  text-decoration: none;
}
.pend-h a:hover {
  text-decoration: underline;
  text-underline-offset: 3px;
}
.pend {
  align-items: flex-start;
}
.pend .prow__main {
  flex: 1;
}
.pend__changes {
  margin-top: 8px;
}
.prow__main .pend__why {
  font-size: 13px;
}
.prow__main .pend__cats {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
@media (max-width: 720px) {
  .pend .prow__acts {
    flex-wrap: wrap;
    gap: 8px;
  }
}
</style>
