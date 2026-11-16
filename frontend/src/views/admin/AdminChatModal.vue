<template>
  <Modal :open="!!chat" :title="chat?.chat.title || 'Чат'" wide @close="$emit('close')">
    <template v-if="chat">
      <p class="cell-note">
        {{ chat.chat.email }} · {{ chat.chat.general ? 'общий чат' : 'чат проекта' }} · начат
        {{ formatDateTime(chat.chat.created_at) }}
      </p>
      <p v-if="!chat.messages.length" class="empty">Сообщений нет.</p>
      <ol class="fb-chat">
        <li
          v-for="m in chat.messages"
          :key="m.id"
          class="fb-chat__msg"
          :class="[`is-${m.role}`, { 'is-rated': m.id === highlight }]"
        >
          <div class="fb-chat__who">
            {{ m.role === 'user' ? 'Пользователь' : `ИИ${m.model ? ` · ${m.model}` : ''}` }}
            <span v-if="m.feedback" :class="m.feedback.rating < 0 ? 'tone-bad' : 'tone-ok'">
              · {{ m.feedback.rating < 0 ? 'плохо' : 'хорошо' }}
            </span>
          </div>
          <p>{{ m.content }}</p>
        </li>
      </ol>
    </template>
  </Modal>
</template>

<script setup>
import { nextTick, watch } from 'vue'
import Modal from '../../components/ui/Modal.vue'
import { formatDateTime } from '../../utils/format'

/** Чат на чтение в админке: вопросы и ответы с оценками. highlight — сообщение, к которому прокрутить. */
const props = defineProps({
  chat: { type: Object, default: null },
  highlight: { type: Number, default: null },
})
defineEmits(['close'])

watch(
  () => [props.chat, props.highlight],
  async ([chat, id]) => {
    if (!chat || !id) return
    await nextTick()
    document.querySelector('.fb-chat__msg.is-rated')?.scrollIntoView({ block: 'center' })
  },
)
</script>

<style scoped>
.fb-chat {
  display: grid;
  gap: 12px;
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
}
.fb-chat__msg {
  padding: 10px 12px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--ink) 4%, transparent);
}
.fb-chat__msg.is-user {
  margin-left: 15%;
}
.fb-chat__msg.is-rated {
  outline: 2px solid var(--ink);
}
.fb-chat__msg p {
  margin: 4px 0 0;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
  font-size: 14px;
}
.fb-chat__who {
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
}
@media (max-width: 640px) {
  .fb-chat__msg.is-user {
    margin-left: 0;
  }
}
</style>
