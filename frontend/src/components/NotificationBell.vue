<template>
  <div ref="root" class="nbell" :class="{ 'is-open': open }">
    <button
      class="dicon nbell__toggle"
      type="button"
      :aria-label="buttonLabel"
      v-tip="buttonLabel"
      :aria-expanded="open"
      @click="toggle"
    >
      <Bell aria-hidden="true" />
      <span v-if="notifCount > 0" class="nbell__count" aria-hidden="true">{{ notifCount > 99 ? '99+' : notifCount }}</span>
    </button>

    <div v-if="open && !sheet" class="nbell__menu" role="dialog" aria-label="Уведомления">
      <div class="nbell__head">
        <strong>Уведомления</strong>
        <small>{{ summary }}</small>
      </div>
      <div class="nbell__list">
        <NotificationItems :items="items" :loading="loading" :error="error" @pick="pick" />
      </div>
      <div class="nbell__footer">
        <RouterLink to="/cabinet/settings/notifications" @click="open = false">Что присылать и куда</RouterLink>
      </div>
    </div>

    <UiSheet :open="open && sheet" title="Уведомления" @close="open = false">
      <NotificationItems :items="items" :loading="loading" :error="error" @pick="pick" />
      <template #footer>
        <RouterLink class="nbell__sheet-link" to="/cabinet/settings/notifications" @click="open = false">
          Что присылать и куда
        </RouterLink>
      </template>
    </UiSheet>
  </div>
</template>

<script setup>
import { computed, defineComponent, h, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { Bell, CloudOff, CreditCard, Gauge, MessageSquare, TrendingDown, Zap } from '@lucide/vue'
import { errorText, notifyApi } from '../api/web'
import { setNotifCount, usePending } from '../composables/usePending'
import { useViewport } from '../composables/useViewport'
import { plural, timeAgo } from '../utils/format'
import UiSheet from './ui/UiSheet.vue'

/**
 * Колокольчик в верхней строке: что случилось, пока вас не было, — ответ ИИ в закрытом чате, сигнал по рекламе,
 * правило автопилота, сбой загрузки, лимит, тариф. Открыли список — счётчик гаснет; непрочитанные записи
 * остаются выделенными, пока список открыт. Запись ведёт на нужный экран.
 * Предложения ИИ, ждущие решения, сюда не входят: это решения, у них свой чип «Ждёт решения».
 * На телефоне список открывается шторкой снизу.
 */
const { phone: sheet } = useViewport()

const router = useRouter()
const { notifCount } = usePending()

const root = ref(null)
const open = ref(false)
const items = ref([])
const loading = ref(false)
const error = ref('')

const buttonLabel = computed(() =>
  notifCount.value
    ? `Уведомления: ${notifCount.value} ${plural(notifCount.value, 'новое', 'новых', 'новых')}`
    : 'Уведомления',
)

const fresh = computed(() => items.value.filter((i) => !i.read).length)
const summary = computed(() => {
  if (loading.value && !items.value.length) return 'Загружаем…'
  if (!items.value.length) return 'Пока ничего не случилось'
  return fresh.value ? `${fresh.value} ${plural(fresh.value, 'новое', 'новых', 'новых')}` : 'Новых нет'
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await notifyApi.list()
    items.value = data.items || []
    // Просмотрели список — всё прочитано; выделение новых остаётся до закрытия.
    if (data.unread) {
      const res = await notifyApi.read({ all: true })
      setNotifCount(res?.unread ?? 0)
    } else {
      setNotifCount(0)
    }
  } catch (err) {
    error.value = errorText(err)
  } finally {
    loading.value = false
  }
}

function toggle() {
  open.value = !open.value
  if (open.value) load()
}

function pick(item) {
  open.value = false
  if (item.link) router.push(item.link)
}

function onDocClick(event) {
  if (open.value && root.value && !root.value.contains(event.target) && !event.target.closest?.('.ui-sheet')) {
    open.value = false
  }
}

onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))

const ICONS = {
  ai_answer: MessageSquare,
  signal: TrendingDown,
  rule: Zap,
  load_error: CloudOff,
  limit: Gauge,
  plan: CreditCard,
}

/** Список записей — общий для выпадающего окна и шторки. */
const NotificationItems = defineComponent({
  props: { items: Array, loading: Boolean, error: String },
  emits: ['pick'],
  setup(p, { emit }) {
    return () => {
      if (p.error) return h('p', { class: 'nbell__empty is-error' }, p.error)
      if (!p.items.length) {
        if (p.loading) return h('p', { class: 'nbell__empty' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }))
        return h('p', { class: 'nbell__empty' }, 'Здесь появятся ответы ИИ из закрытых чатов, сигналы по рекламе и срабатывания правил.')
      }
      return h(
        'ul',
        { class: 'nbell__items' },
        p.items.map((item) =>
          h('li', { key: item.id }, [
            h(
              'button',
              {
                type: 'button',
                class: ['nbell__item', `is-${item.severity}`, { 'is-new': !item.read, 'is-static': !item.link }],
                onClick: () => emit('pick', item),
              },
              [
                h('span', { class: 'nbell__icon', 'aria-hidden': 'true' }, h(ICONS[item.kind] || Bell)),
                h('span', { class: 'nbell__text' }, [
                  h('strong', item.title),
                  item.body ? h('span', { class: 'nbell__body' }, item.body) : null,
                  h('small', timeAgo(item.created_at)),
                ]),
                !item.read ? h('span', { class: 'nbell__dot', 'aria-label': 'новое' }) : null,
              ],
            ),
          ]),
        ),
      )
    }
  },
})
</script>
