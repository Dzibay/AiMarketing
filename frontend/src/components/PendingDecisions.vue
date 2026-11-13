<template>
  <template v-if="items.length">
    <h2 class="section-h">
      Ждут вашего решения <span class="cgroup__n">{{ total }}</span>
      <small v-if="total > items.length" class="home-link">
        <RouterLink to="/cabinet/pending">Все предложения</RouterLink>
      </small>
    </h2>
    <div class="ccard">
      <div v-for="a in items" :key="a.id" class="prow">
        <div class="prow__main">
          <small>{{ niceQuotes(a.project?.name) || 'Все проекты' }}</small>
          <b>{{ a.label }}</b>
          <p v-if="a.summary">{{ a.summary }}</p>
          <p v-else-if="a.kind === 'ask'">ИИ ждёт вашего ответа в чате, чтобы продолжить.</p>
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
          <RouterLink class="btn" :class="a.kind === 'write' ? 'btn--ghost' : 'btn--accent'" :to="chatLink(a)">
            {{ a.kind === 'write' ? 'Открыть чат' : 'Ответить в чате' }}
          </RouterLink>
        </div>
      </div>
    </div>
  </template>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { aiApi, errorText } from '../api/web'
import { refreshPending } from '../composables/usePending'
import { pushToast } from '../composables/useToasts'
import { niceQuotes } from '../utils/format'

/** Главная простого режима: предложения ИИ без решения. Нет предложений — блока нет. */
const props = defineProps({
  wsId: { type: Number, default: null },
  limit: { type: Number, default: 4 },
})

const items = ref([])
const total = ref(0)
const busy = reactive({})

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
    if (!approve) pushToast('Отклонено')
    else if (res.pending) pushToast(`Одобрено. В чате «${a.chat.title}» ещё без решения: ${res.pending}`)
    else pushToast(`Выполняем — ход ИИ продолжится в чате «${a.chat.title}»`)
    await load()
    refreshPending()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    delete busy[a.id]
  }
}

onMounted(load)
</script>

