<template>
  <div
    ref="root"
    class="menu-pop mention-pop"
    :class="{ 'mention-pop--down': place.down }"
    :style="{ maxHeight: `${place.max}px` }"
    @mousedown.prevent
    @focusout="onFocusOut"
  >
    <div v-if="!custom.open" class="seg mention-pop__tabs" role="tablist" aria-label="Что упомянуть">
      <button
        v-for="t in tabs"
        :key="t.id"
        type="button"
        role="tab"
        :class="{ active: t.id === tab }"
        :aria-selected="t.id === tab"
        tabindex="-1"
        @click="setTab(t.id)"
      >
        {{ t.label }}<small v-if="query && t.count">{{ t.count }}</small>
      </button>
    </div>

    <div v-if="custom.open" class="mention-pop__cal">
      <UiCalendar ref="cal" range :start="custom.start" :end="custom.end" :max="today" @pick="pickDay" />
      <div class="mention-pop__cal-foot">
        <p aria-live="polite">{{ customHint }}</p>
        <button class="btn btn--ghost btn--sm" type="button" @click="custom.open = false">Назад</button>
        <button class="btn btn--sm" type="button" :disabled="!custom.start" @click="applyCustom">Готово</button>
      </div>
    </div>
    <template v-else>
      <p v-if="loading && !items.length" class="mention-pop__note">Загружаю…</p>
      <p v-else-if="!items.length" class="mention-pop__note">{{ emptyText }}</p>
      <div v-else id="mention-list" class="mention-pop__list" role="listbox" :aria-label="tabLabel">
        <button
          v-for="(item, i) in items"
          :id="`mention-${item.key}`"
          :key="item.key"
          type="button"
          class="menu-item mention-pop__item"
          :class="{ 'is-active': i === index }"
          role="option"
          tabindex="-1"
          :aria-selected="i === index"
          @click="choose(item)"
          @mousemove="index = i"
        >
          <strong>{{ niceQuotes(item.title) }}</strong>
          <small v-if="item.sub">{{ item.sub }}</small>
        </button>
      </div>
    </template>
  </div>
</template>

<script>
// Кампании и группы кэшируем на пару минут на весь модуль: окно открывается заново на каждое «@».
const CACHE_MS = 120_000
const cache = new Map()
</script>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { aiApi } from '../../api/web'
import { formatRange, lastDays, niceQuotes } from '../../utils/format'
import UiCalendar from '../ui/UiCalendar.vue'
import { addDays, previousMonth, todayIso } from '../ui/calendar'

/**
 * Окно «@» над полем ввода: вкладки Кампании · Группы · Периоды · Проекты. query — набранное после «@»,
 * ищет по всем вкладкам сразу. Выбор — событие pick с упоминанием: { type, id, name, … } или
 * период { type: 'period', from, to, name }. Клавиши ловит поле ввода и отдаёт сюда через onKey.
 */
const props = defineProps({
  query: { type: String, default: '' },
  // null — общий чат: кампании всех проектов с названием проекта.
  workspaceId: { type: Number, default: null },
  projects: { type: Array, default: () => [] },
  general: { type: Boolean, default: false },
})
const emit = defineEmits(['pick', 'close'])

const STATES = { SUSPENDED: 'остановлена', OFF: 'выключена', ENDED: 'завершена', ARCHIVED: 'в архиве' }

const root = ref(null)
const cal = ref(null)
const loading = ref(true)
const options = ref({ campaigns: [], groups: [] })
const tab = ref('')
const index = ref(0)
const today = todayIso()
const custom = reactive({ open: false, start: '', end: '' })

async function load() {
  const key = props.workspaceId ?? 'all'
  const hit = cache.get(key)
  if (!hit || Date.now() - hit.at > CACHE_MS) cache.set(key, { at: Date.now(), data: aiApi.mentions(props.workspaceId) })
  try {
    options.value = await cache.get(key).data
  } catch {
    cache.delete(key)
  } finally {
    loading.value = false
  }
}
load()

function periods() {
  const day = (n) => addDays(today, -n)
  const list = [
    { title: 'Сегодня', from: today, to: today },
    { title: 'Вчера', from: day(1), to: day(1) },
    { title: '7 дней', ...range(lastDays(7)) },
    { title: '30 дней', ...range(lastDays(30)) },
    { title: 'Прошлый месяц', ...range(previousMonth()) },
  ]
  return [
    ...list.map((p) => ({ ...p, key: `period-${p.title}`, sub: formatRange(p.from, p.to), mention: { type: 'period', from: p.from, to: p.to, name: p.title } })),
    { key: 'period-custom', title: 'Свой период…', sub: 'выбрать в календаре', custom: true },
  ]
}
const range = (r) => ({ from: r.date_from, to: r.date_to })

const all = computed(() => {
  const project = (c) => (props.general && c.project ? c.project : '')
  return {
    campaigns: options.value.campaigns.map((c) => ({
      key: `campaign-${c.id}`,
      title: c.name,
      sub: [project(c), STATES[c.state] || ''].filter(Boolean).join(' · '),
      mention: { type: 'campaign', id: c.id, name: c.name, project_id: c.project_id },
    })),
    groups: options.value.groups.map((g) => ({
      key: `group-${g.id}`,
      title: g.name,
      sub: g.campaign,
      mention: { type: 'group', id: g.id, name: g.name, campaign_row_id: g.campaign_row_id, project_id: g.project_id },
    })),
    periods: periods(),
    projects: props.projects.map((w) => ({
      key: `project-${w.id}`,
      title: w.name,
      sub: w.site || '',
      mention: { type: 'project', id: w.id, name: w.name },
    })),
  }
})

const matches = (item, q) => `${item.title} ${item.sub}`.toLowerCase().includes(q)
const found = computed(() => {
  const q = props.query.trim().toLowerCase()
  const out = {}
  for (const [id, list] of Object.entries(all.value)) {
    // «Свой период» — всегда в конце вкладки, если ищут не что-то другое.
    out[id] = q ? list.filter((item) => !item.custom && matches(item, q)) : list
  }
  return out
})

const TAB_LABELS = { campaigns: 'Кампании', groups: 'Группы', periods: 'Периоды', projects: 'Проекты' }
const tabs = computed(() =>
  Object.keys(TAB_LABELS)
    .filter((id) => all.value[id].length || (id === 'campaigns' && loading.value))
    .map((id) => ({ id, label: TAB_LABELS[id], count: found.value[id].length })),
)
const tabLabel = computed(() => TAB_LABELS[tab.value] || '')
const items = computed(() => found.value[tab.value] || [])
const total = computed(() => tabs.value.reduce((n, t) => n + t.count, 0))
const emptyText = computed(() => {
  if (tab.value === 'campaigns') return 'В проекте пока нет кампаний'
  return 'Ничего не найдено'
})

// Вкладка по умолчанию — кампании; при поиске — первая, где что-то нашлось.
watch(
  [tabs, () => props.query],
  () => {
    const ids = tabs.value.map((t) => t.id)
    if (!ids.includes(tab.value)) tab.value = ids[0] || 'periods'
    if (props.query.trim() && !items.value.length) {
      const hit = tabs.value.find((t) => t.count)
      if (hit) tab.value = hit.id
    }
    index.value = Math.min(index.value, Math.max(items.value.length - 1, 0))
  },
  { immediate: true },
)
// Набранное после «@» ничего не нашло — это не упоминание: окно закрывается.
watch(
  [total, loading],
  ([n, busy]) => {
    if (!busy && props.query.trim() && !n) emit('close')
  },
  { immediate: true },
)

function setTab(id) {
  tab.value = id
  index.value = 0
  custom.open = false
}

function choose(item) {
  if (item.custom) {
    Object.assign(custom, { open: true, start: '', end: '' })
    nextTick(() => cal.value?.focus?.())
    return
  }
  emit('pick', item.mention)
}

const customHint = computed(() => {
  if (!custom.start) return 'Первый день'
  if (!custom.end) return `С ${formatRange(custom.start)} — теперь последний`
  return formatRange(custom.start, custom.end)
})

// Первый клик начинает период, второй — заканчивает и вставляет.
function pickDay(iso) {
  if (!custom.start || custom.end) {
    custom.start = iso
    custom.end = ''
    return
  }
  custom.end = iso
  applyCustom()
}

function applyCustom() {
  if (!custom.start) return
  const end = custom.end || custom.start
  const [from, to] = custom.start <= end ? [custom.start, end] : [end, custom.start]
  custom.open = false
  emit('pick', { type: 'period', from, to, name: formatRange(from, to) })
}

// Фокус ушёл и из окна, и из поля ввода (Tab из календаря) — окно закрывается.
function onFocusOut(event) {
  const to = event.relatedTarget
  if (to && !root.value?.contains(to) && !to.classList?.contains('composer__input')) emit('close')
}

function contains(node) {
  return !!node && !!root.value?.contains(node)
}

/** Клавиши из поля ввода: стрелки — по списку, Tab — по вкладкам, Enter — выбрать. true — клавиша наша. */
function onKey(e) {
  if (e.key === 'Escape') {
    if (custom.open) custom.open = false
    else emit('close')
    return true
  }
  if (custom.open) return false
  const last = items.value.length - 1
  if (e.key === 'ArrowDown') index.value = index.value >= last ? 0 : index.value + 1
  else if (e.key === 'ArrowUp') index.value = index.value <= 0 ? last : index.value - 1
  else if (e.key === 'Tab') {
    const ids = tabs.value.map((t) => t.id)
    const at = ids.indexOf(tab.value)
    setTab(ids[(at + (e.shiftKey ? ids.length - 1 : 1)) % ids.length])
  } else if (e.key === 'Enter') {
    const item = items.value[index.value]
    if (!item) return false
    choose(item)
  } else return false
  return true
}

const activeId = computed(() => {
  const item = !custom.open && items.value[index.value]
  return item ? `mention-${item.key}` : null
})

watch(index, () =>
  nextTick(() => root.value?.querySelector('.mention-pop__item.is-active')?.scrollIntoView({ block: 'nearest' })),
)

// Где открыться: над полем ввода, а если сверху мало места (новый чат на телефоне, поле посреди
// экрана) — под ним. Высота — сколько помещается, но не больше 24rem.
const TOP_BAR = 72
const place = reactive({ down: false, max: 384 })
function measure() {
  const form = root.value?.parentElement?.getBoundingClientRect()
  if (!form) return
  const above = form.top - TOP_BAR
  const below = window.innerHeight - form.bottom - 12
  place.down = above < 320 && below > above
  place.max = Math.max(160, Math.min(384, place.down ? below : above))
}
onMounted(() => {
  measure()
  window.addEventListener('resize', measure)
})
onBeforeUnmount(() => window.removeEventListener('resize', measure))

defineExpose({ onKey, activeId, contains })
</script>
