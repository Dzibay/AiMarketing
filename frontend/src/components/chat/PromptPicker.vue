<template>
  <div ref="root" class="menu-pop prompt-pop" :class="{ 'is-below': below }" :style="{ maxHeight }" role="dialog" aria-label="Готовые задачи" @keydown="onKeydown">
    <div class="prompt-pop__search">
      <Search aria-hidden="true" />
      <input
        ref="search"
        v-model="query"
        type="search"
        placeholder="Найти задачу"
        aria-label="Найти задачу"
        aria-controls="prompt-list"
        :aria-activedescendant="flat[index] ? `prompt-${flat[index].id}` : null"
      />
    </div>
    <div id="prompt-list" ref="list" class="prompt-pop__list" role="listbox" aria-label="Задачи">
      <p v-if="!ready" class="prompt-pop__note">Загружаем…</p>
      <p v-else-if="!flat.length" class="prompt-pop__note">
        {{ query ? 'Ничего не нашлось.' : 'Задач пока нет.' }}
      </p>
      <template v-for="g in shown" :key="g.key">
        <p class="prompt-pop__h">{{ g.label }}</p>
        <div
          v-for="p in g.items"
          :id="`prompt-${p.id}`"
          :key="p.id"
          class="menu-item prompt-pop__item"
          :class="{ 'is-active': flat[index]?.id === p.id }"
          role="option"
          :aria-selected="flat[index]?.id === p.id"
          @mousedown.prevent
          @click="pick(p)"
          @mousemove="index = flat.indexOf(p)"
        >
          <span class="prompt-pop__text">
            <strong>{{ p.title }}</strong>
            <small v-if="p.text !== p.title">{{ p.text }}</small>
          </span>
          <button
            v-if="p.own"
            class="prompt-pop__del"
            type="button"
            :aria-label="`Удалить задачу «${p.title}»`"
            v-tip="'Удалить задачу'"
            @click.stop="remove(p)"
          >
            <Trash2 aria-hidden="true" />
          </button>
        </div>
      </template>
    </div>
    <p class="prompt-pop__foot">Задача встанет в поле ввода — её можно поправить перед отправкой.</p>
  </div>
</template>

<script setup>
import { Search, Trash2 } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { errorText } from '../../api/web'
import { askConfirm } from '../../composables/useConfirm'
import { deleteOwnPrompt, usePrompts } from '../../composables/usePrompts'
import { pushToast } from '../../composables/useToasts'

/**
 * Список готовых задач над полем ввода: свои — первыми, дальше встроенные места по категориям.
 * Поиск по названию и тексту, стрелки и Enter, Esc — закрыть. Выбор отдаёт текст задачи (pick).
 */
const props = defineProps({
  // Место чата: general | project | first (usePrompts).
  place: { type: String, required: true },
  // Кнопка, которой открыли список: нажатие на неё не считается «мимо».
  anchor: { type: Object, default: null },
})
const emit = defineEmits(['pick', 'close'])

const { groups, own, ready } = usePrompts(computed(() => props.place))
const query = ref('')
const index = ref(0)
const root = ref(null)
const search = ref(null)
const list = ref(null)

const matches = (p, q) => !q || `${p.title} ${p.text}`.toLowerCase().includes(q)

const shown = computed(() => {
  const q = query.value.trim().toLowerCase()
  const out = []
  const mine = own.value.filter((p) => matches(p, q)).map((p) => ({ ...p, id: `own-${p.id}`, ownId: p.id, own: true }))
  if (mine.length) out.push({ key: 'own', label: 'Мои задачи', items: mine })
  for (const g of groups.value) {
    const items = g.items.filter((p) => matches(p, q)).map((p) => ({ ...p, id: p.key }))
    if (items.length) out.push({ key: g.key, label: g.label, items })
  }
  return out
})
const flat = computed(() => shown.value.flatMap((g) => g.items))

watch(query, () => {
  index.value = 0
})
watch(index, () => nextTick(() => list.value?.querySelector('.prompt-pop__item.is-active')?.scrollIntoView({ block: 'nearest' })))

function pick(p) {
  emit('pick', p.text)
}

async function remove(p) {
  if (!(await askConfirm(`Удалить задачу «${p.title}»?`))) return
  try {
    await deleteOwnPrompt(p.ownId)
    pushToast('Задача удалена')
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
  search.value?.focus()
}

function onKeydown(e) {
  const last = flat.value.length - 1
  if (e.key === 'ArrowDown') index.value = index.value >= last ? 0 : index.value + 1
  else if (e.key === 'ArrowUp') index.value = index.value <= 0 ? last : index.value - 1
  else if (e.key === 'Enter' && flat.value[index.value]) pick(flat.value[index.value])
  else if (e.key === 'Escape') emit('close')
  else return
  e.preventDefault()
}

// Нажатие мимо списка закрывает его; окно подтверждения удаления — не «мимо».
function onPointerDown(e) {
  const t = e.target
  if (root.value?.contains(t) || props.anchor?.contains?.(t) || t.closest?.('[role="dialog"], [role="alertdialog"]')) return
  emit('close')
}

// Сверху мало места (поле ввода посреди стартового экрана, низкое окно) — список раскрывается вниз.
const below = ref(false)
const maxHeight = ref(null)
function place() {
  const box = root.value?.parentElement?.getBoundingClientRect()
  if (!box) return
  // 64 — верхняя панель кабинета, 416 — полная высота списка (26rem), 8 — зазор до поля.
  const above = box.top - 64 - 8
  const under = window.innerHeight - box.bottom - 16
  below.value = above < Math.min(416, window.innerHeight * 0.6) && under > above
  maxHeight.value = `${Math.max(200, Math.min(416, window.innerHeight * 0.6, below.value ? under : above))}px`
}

onMounted(() => {
  place()
  document.addEventListener('pointerdown', onPointerDown, true)
  search.value?.focus()
})
onBeforeUnmount(() => document.removeEventListener('pointerdown', onPointerDown, true))
</script>

<style scoped>
.prompt-pop {
  top: auto;
  right: auto;
  bottom: calc(100% + 8px);
  left: 0;
  display: flex;
  flex-direction: column;
  width: min(480px, 100%);
  max-height: min(26rem, 60vh);
  padding: 0;
  overflow: hidden;
}
.prompt-pop.is-below {
  top: calc(100% + 8px);
  bottom: auto;
}
.prompt-pop__search {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line);
  color: var(--muted);
}
.prompt-pop__search svg {
  flex: none;
  width: 16px;
  height: 16px;
}
.prompt-pop__search input {
  flex: 1;
  min-width: 0;
  min-height: 28px;
  border: 0;
  outline: 0;
  background: none;
  color: var(--ink);
  font: inherit;
  font-size: 15px;
}
.prompt-pop__list {
  flex: 1;
  min-height: 0;
  padding: 4px 6px 6px;
  overflow-y: auto;
}
.prompt-pop__h {
  margin: 8px 8px 2px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
}
.prompt-pop__note {
  margin: 10px 8px;
  color: var(--muted);
  font-size: 14px;
}
.prompt-pop__item {
  align-items: flex-start;
  padding: 8px 10px;
}
.prompt-pop__item.is-active {
  background: var(--surface-2);
}
.prompt-pop__text {
  display: grid;
  flex: 1;
  min-width: 0;
  gap: 2px;
}
.prompt-pop__text strong {
  font-size: 14px;
  font-weight: 600;
}
.prompt-pop__text small {
  overflow: hidden;
  color: var(--muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.prompt-pop__del {
  appearance: none;
  display: grid;
  flex: none;
  place-items: center;
  width: 28px;
  height: 28px;
  margin: -4px -4px -4px 0;
  padding: 0;
  border: 0;
  border-radius: 8px;
  background: none;
  color: var(--muted);
  cursor: pointer;
}
.prompt-pop__del:hover {
  background: color-mix(in srgb, var(--danger) 12%, transparent);
  color: var(--danger);
}
.prompt-pop__del svg {
  width: 15px;
  height: 15px;
}
.prompt-pop__foot {
  margin: 0;
  padding: 8px 14px 10px;
  border-top: 1px solid var(--line);
  color: var(--faint);
  font-size: 12px;
}
@media (pointer: coarse) {
  .prompt-pop__del {
    width: 40px;
    height: 40px;
    margin: -10px -8px -10px 0;
  }
  .prompt-pop__item {
    min-height: 44px;
  }
}
</style>
