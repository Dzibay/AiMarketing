<template>
  <div class="aip__inner chat" :class="{ 'chat--hello': showIntro && !listOpen }">
    <header class="aip__head">
      <div class="aip__title">
        <strong>{{ listOpen ? (general ? 'Общие чаты' : 'Чаты проекта') : chat?.title || 'Новый чат' }}</strong>
        <small>{{ general ? 'Все проекты' : niceQuotes(ws.name) }}</small>
      </div>
      <button
        class="dicon"
        type="button"
        :class="{ active: listOpen }"
        :aria-pressed="listOpen"
        aria-label="Все чаты"
        v-tip="'Все чаты'"
        @click="listOpen = !listOpen"
      >
        <History aria-hidden="true" />
      </button>
      <button class="dicon" type="button" aria-label="Новый чат" v-tip="'Новый чат'" :disabled="sending" @click="startNew">
        <SquarePen aria-hidden="true" />
      </button>
      <RouterLink class="dicon" :to="fullTo" aria-label="На весь экран" v-tip="'На весь экран'">
        <Maximize2 aria-hidden="true" />
      </RouterLink>
      <button class="dicon" type="button" aria-label="Закрыть панель" v-tip="'Закрыть'" @click="emit('close')">
        <X aria-hidden="true" />
      </button>
    </header>

    <!-- Список чатов — вместо ленты: в узкой панели ему некуда выезжать сбоку. -->
    <nav v-if="listOpen" class="aip__list" aria-label="Чаты">
      <p v-if="chatsError" class="hint">{{ chatsError }}</p>
      <p v-else-if="!chats.length" class="hint">Чатов пока нет — напишите первое сообщение.</p>
      <button
        v-for="c in chats"
        :key="c.id"
        class="srow srow--chat"
        :class="{ active: c.id === chatId }"
        type="button"
        @click="pick(c.id)"
      >
        <span v-if="c.status === 'running'" class="sdot sdot--run" aria-label="ИИ отвечает"></span>
        <span v-else-if="c.pending_actions" class="sdot sdot--lime" aria-hidden="true"></span>
        <MessageSquare v-else aria-hidden="true" />
        <span class="srow__t">{{ c.title }}</span>
        <span v-if="c.pending_actions" class="srow__n">{{ c.pending_actions }}</span>
      </button>
    </nav>

    <template v-else>
      <UiNotice v-if="!aiConfigured" tone="warn" class="aip__notice">
        ИИ временно недоступен: администратор ещё не подключил модель.
      </UiNotice>

      <div v-if="showIntro" class="aip__hello">
        <p>{{ helloText }}</p>
        <div v-if="starters.length" class="aip__starters">
          <button
            v-for="s in starters"
            :key="s"
            class="chat-next__btn"
            type="button"
            :disabled="!canSendStarter"
            @click="send(s)"
          >
            <CornerDownRight aria-hidden="true" />
            {{ s }}
          </button>
        </div>
      </div>
      <ChatThread v-else />

      <div class="chat__foot aip__foot">
        <div v-if="activeWrites.length > 1" class="chat__pending">
          <span>Ждут решения: {{ activeWrites.length }}</span>
          <button class="btn btn--accent btn--sm" type="button" :disabled="deciding" @click="decideAll(true)">Выполнить все</button>
          <button class="btn btn--ghost btn--sm" type="button" :disabled="deciding" @click="decideAll(false)">Отклонить все</button>
        </div>
        <p v-if="pageContext" class="aip__ctx">
          <span v-tip="'ИИ получит это вместе с сообщением и не будет переспрашивать'">Про: {{ pageContext.label }}</span>
          <button type="button" aria-label="Не учитывать открытую страницу" v-tip="'Не учитывать'" @click="hidePageContext">
            <X aria-hidden="true" />
          </button>
        </p>
        <ChatComposer ref="composer" />
      </div>
    </template>
  </div>
</template>

<script setup>
import { CornerDownRight, History, Maximize2, MessageSquare, SquarePen, X } from '@lucide/vue'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { aiApi } from '../../api/web'
import { aiPanelFocus } from '../../composables/useAiPanel'
import { provideChat } from '../../composables/useChat'
import { hidePageContext, pageContext } from '../../composables/usePageContext'
import { useViewport } from '../../composables/useViewport'
import { loadWorkspaces } from '../../composables/useWorkspaces'
import { niceQuotes } from '../../utils/format'
import UiNotice from '../ui/UiNotice.vue'
import ChatComposer from './ChatComposer.vue'
import ChatThread from './ChatThread.vue'

/**
 * Чат в боковой панели (AiPanel.vue): чат выбранного проекта или общий. Открытый чат — свой, не адрес страницы:
 * переходы по кабинету его не меняют. С сообщением уходит то, что открыто на странице (usePageContext).
 */
const GENERAL_WS = {
  id: null,
  name: 'Все проекты',
  site: '',
  ai_access: { full_access: false, categories: {}, max_change_pct: 30 },
  ai_categories: [],
  campaigns_count: 1,
}
// Последний открытый чат по каждому проекту — вернуться к нему, когда панель снова покажет этот проект.
const lastChats = new Map()

const props = defineProps({
  // Проект из списка (useWorkspaces); null — общий чат по всем проектам.
  project: { type: Object, default: null },
})
const emit = defineEmits(['close'])

const general = !props.project
const scope = general ? 'general' : props.project.id
const ws = computed(() => props.project || GENERAL_WS)
const basePath = computed(() => (general ? '/cabinet/chat' : `/w/${ws.value.id}/chat`))
const panelChat = ref(lastChats.get(scope) || null)
watch(panelChat, (id) => lastChats.set(scope, id))

const {
  chats,
  chatsError,
  chatId,
  chat,
  showIntro,
  activeWrites,
  deciding,
  decideAll,
  aiConfigured,
  canSendStarter,
  sending,
  input,
  fillModels,
  send,
  loadChats,
  loadChat,
  newChat,
  goChat,
} = provideChat({ general, ws, basePath, reloadWs: () => loadWorkspaces(), panel: true, panelChat, context: pageContext })

const listOpen = ref(false)
const composer = ref(null)
const fullTo = computed(() => (chatId.value ? `${basePath.value}/${chatId.value}` : basePath.value))

const helloText = computed(() =>
  general
    ? 'Спросите про любой проект — сравню, найду, где сливается бюджет, и предложу правки.'
    : `Спросите про «${niceQuotes(ws.value.name)}». Вижу статистику, кампании, фразы и запросы — отвечу про то, что у вас открыто.`,
)

// Подсказки — про открытую страницу: вопрос уйдёт вместе с её контекстом.
const starters = computed(() => {
  const c = pageContext.value
  switch (c?.view) {
    case 'campaign':
      return ['Почему подорожали заявки?', 'Что улучшить в этой кампании?']
    case 'campaigns':
      return c.selected?.length > 1
        ? ['Сравни выбранные кампании', 'Что улучшить в выбранных?']
        : c.selected?.length
          ? ['Почему подорожали заявки?', 'Что улучшить в этой кампании?']
          : ['Какие кампании тянут результат вниз?', 'Где сливается бюджет?']
    case 'queries':
      return ['Какие запросы отминусовать?', 'Какие запросы приносят заявки?']
    case 'analytics':
      return ['Что изменилось за период и почему?', 'Почему подорожали заявки?']
    case 'history':
      return ['Что из последних изменений сработало?']
    case 'pending':
      return ['Что из этого принять в первую очередь?']
    default:
      return general ? ['Где сливается бюджет?', 'Какой проект требует внимания?'] : ['Что горит сейчас?', 'Почему подорожали заявки?']
  }
})

function pick(id) {
  listOpen.value = false
  goChat(id)
}

function startNew() {
  listOpen.value = false
  newChat()
}

// Открыли панель — курсор в поле ввода. На телефоне — нет: клавиатура закрыла бы полэкрана.
const { narrow } = useViewport()
watch(aiPanelFocus, () => {
  if (!narrow.value) nextTick(() => input.value?.focus())
})

watch(listOpen, (open) => {
  if (open) loadChats()
})

onMounted(() => {
  loadChats()
  if (chatId.value) loadChat(chatId.value)
  else
    aiApi
      .models()
      .then(fillModels)
      .catch(() => {})
  nextTick(() => composer.value?.autosize())
})
</script>
