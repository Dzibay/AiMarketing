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
    <p v-if="chatsError" class="hint">{{ chatsError }}</p>
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
import { Plus, X } from '@lucide/vue'
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { useChat } from '../../composables/useChat'
import { formatDateTime } from '../../utils/format'

/** Список чатов раздела: панель слева с затемнением. Открывает и закрывает её страница. */
const props = defineProps({
  open: { type: Boolean, default: false },
})
// close — закрыть панель (крестик, затемнение, Esc); new — «Новый чат».
const emit = defineEmits(['close', 'new'])

const { general, basePath, chats, chatsError, chatId, sending } = useChat()
const closeButton = ref(null)

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
onBeforeUnmount(() => window.removeEventListener('keydown', onWindowKey))
</script>
