<template>
  <aside class="chat__side" :class="{ 'is-open': open }" :aria-label="general ? 'Общие чаты' : 'Чаты проекта'">
    <div class="chat__side-head">
      <strong>{{ general ? 'Общие чаты' : 'Чаты проекта' }}</strong>
      <button ref="closeButton" class="chat__side-x" type="button" aria-label="Закрыть список чатов" @click="emit('close')">
        <X aria-hidden="true" />
      </button>
    </div>
    <button class="btn btn--accent chat__new" type="button" :disabled="sending" @click="emit('new')">
      <Plus aria-hidden="true" />
      Новый чат
    </button>
    <label v-if="chats.length || query" class="chat-search">
      <Search aria-hidden="true" />
      <input
        v-model="query"
        type="search"
        placeholder="Найти в чатах"
        aria-label="Найти в чатах"
        maxlength="100"
        @keydown.esc.stop="query = ''"
      />
    </label>
    <template v-if="searching">
      <p v-if="found === null" class="hint" role="status">Ищем…</p>
      <p v-else-if="searchError" class="hint">{{ searchError }}</p>
      <p v-else-if="!found.length" class="hint" role="status">Ничего не нашлось.</p>
      <nav v-else class="chat__list" aria-label="Найденные чаты">
        <RouterLink
          v-for="c in found"
          :key="c.id"
          :to="c.match.message_id ? `${basePath}/${c.id}?m=${c.match.message_id}` : `${basePath}/${c.id}`"
          class="chat__item chat__item--found"
          :class="{ active: c.id === chatId }"
          @click="emit('close')"
        >
          <span class="chat__item-title"><template v-for="(p, i) in marked(c.title)" :key="i"><mark v-if="p.hit">{{ p.text }}</mark><template v-else>{{ p.text }}</template></template></span>
          <span v-if="c.match.snippet" class="chat__item-snippet">
            <span class="chat__item-who">{{ c.match.role === 'user' ? 'Вы' : 'ИИ' }}:</span>
            <template v-for="(p, i) in marked(c.match.snippet)" :key="i"><mark v-if="p.hit">{{ p.text }}</mark><template v-else>{{ p.text }}</template></template>
          </span>
          <small>{{ formatDateTime(c.updated_at) }}</small>
        </RouterLink>
      </nav>
    </template>
    <p v-else-if="chatsError" class="hint">{{ chatsError }}</p>
    <p v-else-if="!chats.length" class="hint">Чатов пока нет.</p>
    <nav v-else class="chat__list" aria-label="Чаты">
      <RouterLink
        v-for="c in chats"
        :key="c.id"
        :to="`${basePath}/${c.id}`"
        class="chat__item"
        :class="{ active: c.id === chatId }"
      >
        <span class="chat__item-title">{{ c.title }}</span>
        <small>
          <span v-if="c.status === 'running'" class="tone-warn">отвечает…</span>
          <span v-else-if="c.pending_actions" class="chat__item-wait">ждёт решения: {{ c.pending_actions }}</span>
          <span v-else-if="c.status === 'error'" class="tone-bad">ошибка</span>
          {{ formatDateTime(c.updated_at) }}
        </small>
      </RouterLink>
    </nav>
  </aside>
  <div v-if="open" class="chat-backdrop" @click="emit('close')" />
</template>

<script setup>
import { Plus, Search, X } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { aiApi, errorText } from '../../api/web'
import { useChat } from '../../composables/useChat'
import { formatDateTime } from '../../utils/format'

/**
 * Список чатов раздела: панель слева с затемнением. Открывает и закрывает её страница.
 * Поле поиска ищет по названиям и тексту сообщений; найденное открывает чат на нужном сообщении (?m=).
 */
const props = defineProps({
  open: { type: Boolean, default: false },
})
// close — закрыть панель (крестик, затемнение, Esc); new — «Новый чат».
const emit = defineEmits(['close', 'new'])

const { general, ws, basePath, chats, chatsError, chatId, sending } = useChat()
const closeButton = ref(null)

// --- Поиск ---
const query = ref('')
const found = ref(null) // null — запрос ещё идёт
const searchError = ref('')
const term = computed(() => query.value.replace(/\s+/g, ' ').trim())
const searching = computed(() => term.value.length >= 2)
let searchTimer = 0
let searchSeq = 0

watch(term, (q) => {
  clearTimeout(searchTimer)
  const seq = ++searchSeq
  found.value = null
  searchError.value = ''
  if (q.length < 2) return
  searchTimer = setTimeout(async () => {
    try {
      const res = await aiApi.searchChats(general ? null : ws.value.id, q)
      if (seq === searchSeq) found.value = res.items
    } catch (err) {
      if (seq !== searchSeq) return
      searchError.value = errorText(err)
      found.value = []
    }
  }, 250)
})

/** Текст на куски с отметкой найденного слова — без v-html, регистр не важен. */
function marked(text) {
  const q = term.value.toLowerCase()
  const low = text.toLowerCase()
  const out = []
  let from = 0
  for (let at = low.indexOf(q); q && at >= 0; at = low.indexOf(q, at + q.length)) {
    if (at > from) out.push({ text: text.slice(from, at) })
    out.push({ text: text.slice(at, at + q.length), hit: true })
    from = at + q.length
  }
  if (from < text.length) out.push({ text: text.slice(from) })
  return out
}

function onWindowKey(event) {
  if (event.key === 'Escape' && props.open) emit('close')
}

watch(
  () => props.open,
  (open) => {
    if (open) nextTick(() => closeButton.value?.focus())
  },
)

onMounted(() => window.addEventListener('keydown', onWindowKey))
onBeforeUnmount(() => {
  clearTimeout(searchTimer)
  window.removeEventListener('keydown', onWindowKey)
})
</script>
