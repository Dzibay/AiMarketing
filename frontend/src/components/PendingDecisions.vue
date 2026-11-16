<template>
  <template v-if="items.length">
    <h2 class="section-h">
      <span class="hico" aria-hidden="true"><ListChecks /></span>Ждут вашего решения <span class="cgroup__n">{{ total }}</span>
      <small v-if="total > items.length" class="home-link">
        <RouterLink to="/cabinet/pending">Все предложения</RouterLink>
      </small>
    </h2>
    <div class="ccard">
      <template v-for="a in items" :key="a.id">
      <!-- Только что выполнено: строка с галочкой на несколько секунд, потом уходит. -->
      <div v-if="doneIds[a.id]" class="prow pdec pdec--done is-fresh" :data-action="a.id" role="status">
        <span class="done-check"><Check aria-hidden="true" /></span>
        <div class="prow__main">
          <b>{{ a.label }}</b>
          <p>{{ doneIds[a.id] }} <RouterLink :to="chatLink(a)">«{{ a.chat.title }}»</RouterLink></p>
        </div>
      </div>
      <div v-else class="prow pdec" :data-action="a.id">
        <div class="prow__main">
          <small>{{ niceQuotes(a.project?.name) || 'Все проекты' }}</small>
          <b>{{ a.label }}</b>
          <p v-if="a.summary">{{ a.summary }}</p>
          <p v-else-if="a.kind === 'ask'">ИИ ждёт вашего ответа в чате, чтобы продолжить.</p>
        </div>
        <div class="prow__acts prow__acts--decide">
          <template v-if="a.kind === 'write'">
            <button class="btn btn--accent" type="button" :disabled="!!busy[a.id]" @click="decide(a, true)">
              Выполнить
            </button>
            <button class="btn btn--ghost" type="button" :disabled="!!busy[a.id]" @click="decide(a, false)">
              Отклонить
            </button>
          </template>
          <!-- На телефоне три кнопки в ряд не помещаются: чат — в «⋯». -->
          <UiMenu v-if="phone && a.kind === 'write'">
            <UiMenuItem :icon="MessageSquare" :to="chatLink(a)">Открыть чат</UiMenuItem>
          </UiMenu>
          <RouterLink v-else class="btn" :class="a.kind === 'write' ? 'btn--ghost' : 'btn--accent'" :to="chatLink(a)">
            {{ a.kind === 'write' ? 'Открыть чат' : 'Ответить в чате' }}
          </RouterLink>
        </div>
      </div>
      </template>
    </div>
  </template>
</template>

<script setup>
import { nextTick, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { Check, ListChecks, MessageSquare } from '@lucide/vue'
import { aiApi, errorText } from '../api/web'
import { refreshPending } from '../composables/usePending'
import { pushToast } from '../composables/useToasts'
import { useViewport } from '../composables/useViewport'
import { foldFrom, heightOf } from '../utils/foldAnim'
import { niceQuotes } from '../utils/format'
import UiMenu from './ui/UiMenu.vue'
import UiMenuItem from './ui/UiMenuItem.vue'

/** Главная простого режима: предложения ИИ без решения. Нет предложений — блока нет. */
const props = defineProps({
  wsId: { type: Number, default: null },
  limit: { type: Number, default: 4 },
})

const { phone } = useViewport()

const items = ref([])
const total = ref(0)
const busy = reactive({})
// Выполненные: id → подпись строки с галочкой; строка держится DONE_MS, потом список перечитывается.
const doneIds = reactive({})
const DONE_MS = 4000

function chatLink(a) {
  const base = a.chat.general || !a.project ? '/cabinet/chat' : `/w/${a.project.id}/chat`
  return `${base}/${a.chat.id}`
}

async function load() {
  try {
    const res = await aiApi.pending(props.wsId)
    const all = res.items || []
    items.value = all.slice(0, props.limit)
    total.value = res.total || all.length
  } catch {
    // Блок необязательный: если не загрузился, главная работает без него.
    items.value = []
  }
}

async function decide(a, approve) {
  busy[a.id] = true
  try {
    const res = await aiApi.decide(a.id, approve, false, [])
    refreshPending()
    if (!approve) {
      pushToast('Отклонено')
      await load()
      return
    }
    // «Выполнить» — карточка сворачивается в строку с галочкой на месте, а не пропадает молча.
    const selector = `.pdec[data-action="${a.id}"]`
    const from = heightOf(selector)
    doneIds[a.id] = res.pending ? `Одобрено. ИИ продолжит, когда решите остальное (${res.pending}) в чате` : 'Выполняем — ход ИИ продолжится в чате'
    total.value = Math.max(0, total.value - 1)
    nextTick(() => foldFrom(selector, from))
    setTimeout(async () => {
      await load()
      delete doneIds[a.id]
    }, DONE_MS)
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    delete busy[a.id]
  }
}

onMounted(load)
</script>

<style scoped>
.pdec.pdec--done {
  justify-content: flex-start;
  gap: 12px;
  animation: pdec-done-flash 1.2s var(--ease-out);
}
.pdec--done .prow__main p {
  margin: 2px 0 0;
  color: var(--muted);
  font-size: 13px;
}
@keyframes pdec-done-flash {
  from {
    background: var(--lime-soft);
  }
}
</style>
