<template>
  <div class="adm-sup">
    <section class="panel adm-sup__inbox">
      <div class="adm-sup__head">
        <nav class="seg seg--sm" aria-label="Какие обращения">
          <button type="button" :class="{ active: filter === 'open' }" @click="setKey('filter', '')">
            Ждут ответа<b v-if="inbox?.counts.open"> {{ inbox.counts.open }}</b>
          </button>
          <button type="button" :class="{ active: filter === 'all' }" @click="setKey('filter', 'all')">
            Все<b v-if="inbox"> {{ inbox.counts.all }}</b>
          </button>
        </nav>
        <input
          v-model.trim="search"
          class="search-input"
          type="search"
          placeholder="Почта, имя, id"
          aria-label="Поиск обращений"
          @keydown.enter="setKey('q', search)"
          @search="setKey('q', search)"
        />
      </div>
      <p v-if="inboxError && !inbox" class="empty">{{ inboxError }}</p>
      <p v-else-if="!inbox" class="empty">Загружаем…</p>
      <p v-else-if="!inbox.items.length" class="empty">{{ filter === 'open' && !q ? 'Все ответили — никто не ждёт.' : 'Ничего не нашлось.' }}</p>
      <ul v-else class="adm-sup__list">
        <li v-for="t in inbox.items" :key="t.user_id">
          <button type="button" class="adm-sup__item" :class="{ 'is-active': t.user_id === userId, 'is-waiting': t.waiting }" @click="setKey('user', t.user_id)">
            <span class="adm-sup__who">
              <b>{{ t.email || t.name || `#${t.user_id}` }}</b>
              <span v-if="t.unread" class="adm-sup__badge">{{ t.unread }}</span>
            </span>
            <span class="adm-sup__meta">
              {{ t.plan || 'без тарифа' }}<template v-if="t.trial"> (пробный)</template><template v-if="t.blocked"> · заблокирован</template>
            </span>
            <span class="adm-sup__text">
              <template v-if="t.last.author === 'admin'">Вы: </template>{{ t.last.text }}
            </span>
            <span class="adm-sup__time" :class="{ 'tone-warn': t.waiting }">
              {{ t.waiting ? `ждёт ${ago(t.waiting_since)}` : formatDateTime(t.last.at) }}
            </span>
          </button>
        </li>
      </ul>
      <div v-if="inbox && inbox.pages > 1" class="adm-costs__pages">
        <button class="btn btn--ghost btn--xs" type="button" :disabled="inbox.page <= 1" @click="setKey('page', inbox.page - 1)">Назад</button>
        <span>{{ inbox.page }} из {{ inbox.pages }}</span>
        <button class="btn btn--ghost btn--xs" type="button" :disabled="inbox.page >= inbox.pages" @click="setKey('page', inbox.page + 1)">Дальше</button>
      </div>
    </section>

    <section class="panel adm-sup__thread">
      <p v-if="!userId" class="empty">Выберите переписку слева.</p>
      <p v-else-if="!thread" class="empty">Загружаем…</p>
      <template v-else>
        <header class="adm-sup__user">
          <div>
            <RouterLink :to="{ path: `${ADMIN_BASE}/users`, query: { user: userId } }">
              <b>{{ thread.user.email || thread.user.name || `#${userId}` }}</b>
            </RouterLink>
            <span v-if="thread.user.blocked" class="chip chip--bad adm-costs__chip">заблокирован</span>
          </div>
          <small class="cell-note">
            {{ thread.user.plan || 'без тарифа' }}<template v-if="thread.user.last_day">
              {{ thread.user.expired ? ' · закончился' : ' · до' }} {{ formatDay(thread.user.last_day) }}</template>
            · заходил {{ thread.user.last_seen_at ? formatDateTime(thread.user.last_seen_at) : 'никогда' }}
            · {{ thread.user.telegram ? 'Telegram привязан' : 'без Telegram — ответ уйдёт письмом' }}
          </small>
        </header>
        <div ref="scroller" class="adm-sup__messages">
          <p v-if="!thread.messages.length" class="empty">Сообщений нет.</p>
          <div v-for="m in thread.messages" :key="m.id" class="sup-msg" :class="`is-${m.author}`">
            <small class="sup-msg__who">{{ m.author === 'admin' ? 'Поддержка' : 'Пользователь' }}</small>
            <a v-if="m.has_image && images[m.id]" :href="images[m.id]" target="_blank" rel="noopener" class="sup-msg__img">
              <img :src="images[m.id]" alt="Скриншот" />
            </a>
            <span v-else-if="m.has_image" class="sup-msg__img is-loading">Скриншот…</span>
            <p v-if="m.body">{{ m.body }}</p>
            <small class="sup-msg__time">
              {{ formatDateTime(m.created_at) }}<template v-if="contextText(m.context)"> · {{ contextText(m.context) }}</template>
            </small>
          </div>
        </div>
        <form class="adm-sup__reply" @submit.prevent="sendReply">
          <textarea
            v-model="reply"
            class="input"
            rows="3"
            maxlength="4000"
            placeholder="Ответ пользователю · Ctrl+Enter — отправить"
            @keydown.ctrl.enter.prevent="sendReply"
            @keydown.meta.enter.prevent="sendReply"
          ></textarea>
          <div class="adm-sup__send">
            <small class="cell-note">Придёт в виджет и колокольчик; в Telegram — если привязан, иначе письмом.</small>
            <button class="btn btn--accent" type="submit" :disabled="sending || !reply.trim()">Ответить</button>
          </div>
        </form>
      </template>
    </section>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { ADMIN_BASE } from './adminNav'
import { queryOne, useAdminLoad } from './useAdminLoad'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { reloadAdminSummary } from '../../composables/useAdminSummary'
import { pushToast } from '../../composables/useToasts'
import { formatDateTime, formatDay, num, plural } from '../../utils/format'

/**
 * Обращения (docs/admin-plan.md, этап 7): слева входящие — сначала ждущие ответа, дольше всех ждущие наверху;
 * справа переписка и ответ. Выбранный человек — ?user=ID (ссылка из Telegram ведёт сюда).
 */
const route = useRoute()
const router = useRouter()

const filter = computed(() => (queryOne(route, 'filter') === 'all' ? 'all' : 'open'))
const q = computed(() => queryOne(route, 'q'))
const userId = computed(() => Number(queryOne(route, 'user')) || null)
const inboxParams = computed(() => ({ filter: filter.value, q: q.value, page: Number(queryOne(route, 'page')) || 1 }))
const { data: inbox, error: inboxError, reload: reloadInbox } = useAdminLoad(adminApi.supportInbox, inboxParams)

const search = ref(q.value)
watch(q, (v) => (search.value = v))

function setKey(key, value) {
  const next = { ...route.query, [key]: value === '' || value == null ? undefined : String(value) }
  if (key !== 'page' && key !== 'user') delete next.page
  router.push({ query: next })
}

function ago(iso) {
  if (!iso) return ''
  const min = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000))
  if (min < 60) return `${num(min)} ${plural(min, 'минуту', 'минуты', 'минут')}`
  const h = Math.round(min / 60)
  if (h < 48) return `${num(h)} ${plural(h, 'час', 'часа', 'часов')}`
  const d = Math.round(h / 24)
  return `${num(d)} ${plural(d, 'день', 'дня', 'дней')}`
}

function contextText(ctx) {
  if (!ctx) return ''
  return [ctx.page, ctx.workspace ? `проект «${ctx.workspace.name}» #${ctx.workspace.id}` : '', ctx.plan ? `тариф ${ctx.plan}` : '', ctx.browser]
    .filter(Boolean)
    .join(' · ')
}

// --- Переписка ---

const thread = ref(null)
const images = ref({})
const reply = ref('')
const sending = ref(false)
const scroller = ref(null)

function dropImages() {
  for (const url of Object.values(images.value)) if (url) URL.revokeObjectURL(url)
  images.value = {}
}

async function scrollDown() {
  await nextTick()
  if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
}

function showMessages(messages, id) {
  if (userId.value !== id) return
  thread.value = { ...thread.value, messages: messages || [] }
  for (const m of thread.value.messages) {
    if (!m.has_image || images.value[m.id] !== undefined) continue
    images.value = { ...images.value, [m.id]: '' }
    adminApi
      .supportImage(m.id)
      .then((url) => (images.value = { ...images.value, [m.id]: url }))
      .catch(() => {})
  }
  scrollDown()
}

watch(
  userId,
  async (id) => {
    thread.value = null
    reply.value = ''
    dropImages()
    if (!id) return
    try {
      const out = await adminApi.supportUser(id)
      if (userId.value !== id) return
      thread.value = { user: out.user, messages: [] }
      showMessages(out.thread.messages, id)
      // Открыли — прочитано: счётчики слева и в меню.
      reloadInbox()
      reloadAdminSummary()
    } catch (err) {
      if (userId.value === id) pushToast(errorText(err), 'error')
    }
  },
  { immediate: true },
)

async function sendReply() {
  const id = userId.value
  const text = reply.value.trim()
  if (!id || !text || sending.value) return
  sending.value = true
  try {
    const out = await adminApi.supportReply(id, text)
    reply.value = ''
    showMessages(out.messages, id)
    reloadInbox()
    reloadAdminSummary()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    sending.value = false
  }
}

onBeforeUnmount(dropImages)
</script>

<style scoped>
.adm-sup {
  display: grid;
  grid-template-columns: minmax(280px, 380px) minmax(0, 1fr);
  gap: 14px;
  align-items: start;
}

.adm-sup__head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}

.adm-sup__head .search-input {
  flex: 1 1 140px;
  min-width: 0;
}

.adm-sup__list {
  display: grid;
  gap: 2px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.adm-sup__item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 2px 10px;
  width: 100%;
  padding: 10px;
  border: 0;
  border-radius: 10px;
  background: none;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.adm-sup__item:hover {
  background: color-mix(in srgb, var(--ink) 4%, transparent);
}

.adm-sup__item.is-active {
  background: color-mix(in srgb, var(--accent) 16%, transparent);
}

.adm-sup__who {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  overflow: hidden;
  font-size: 14px;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.adm-sup__badge {
  padding: 0 6px;
  border-radius: 999px;
  background: var(--ink);
  color: var(--bg);
  font-size: 11px;
  font-weight: 700;
}

.adm-sup__meta,
.adm-sup__time {
  color: var(--muted);
  font-size: 12px;
  text-align: right;
}

.adm-sup__meta {
  grid-column: 2;
  grid-row: 1;
}

.adm-sup__text {
  grid-column: 1 / -1;
  display: -webkit-box;
  overflow: hidden;
  color: var(--muted);
  font-size: 13px;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.adm-sup__time {
  grid-column: 1 / -1;
  text-align: left;
}

.adm-sup__thread {
  position: sticky;
  top: 12px;
  display: flex;
  flex-direction: column;
  max-height: calc(100vh - 120px);
}

.adm-sup__user {
  display: grid;
  gap: 4px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line);
}

.adm-sup__messages {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 10px;
  min-height: 200px;
  padding: 12px 0;
  overflow-y: auto;
}

.adm-sup__reply {
  display: grid;
  gap: 8px;
  padding-top: 10px;
  border-top: 1px solid var(--line);
}

.adm-sup__send {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

@media (max-width: 900px) {
  .adm-sup {
    grid-template-columns: minmax(0, 1fr);
  }

  .adm-sup__thread {
    position: static;
    max-height: none;
  }
}
</style>
