<template>
  <div ref="root" class="ai-gallery" :class="{ 'is-readonly': readonly }">
    <div v-for="g in groups" :key="g.name" class="ai-gallery__group" role="group" :aria-label="g.name || 'Картинки'">
      <div class="ai-gallery__head">
        <strong class="ai-gallery__title">{{ g.name || 'Картинки' }}</strong>
        <span class="ai-gallery__count">выбрано {{ g.on }} из {{ g.rows.length }}</span>
        <span v-if="!readonly" class="ai-gallery__all">
          <button class="ai-check__link" type="button" :disabled="disabled || g.on === g.rows.length" @click="setGroup(g, true)">
            Выбрать все
          </button>
          <button class="ai-check__link" type="button" :disabled="disabled || !g.on" @click="setGroup(g, false)">
            Снять все
          </button>
        </span>
      </div>

      <ul class="ai-gallery__grid">
        <li v-for="r in g.rows" :key="r.id" class="ai-gallery__card" :class="{ 'is-off': isOff(r) }">
          <!-- Вся карточка — подпись флажка: щелчок по картинке тоже снимает и возвращает галочку. -->
          <UiCheckbox
            class="ai-gallery__pick"
            :model-value="!isOff(r)"
            :disabled="readonly || disabled"
            @update:model-value="flip(r)"
          >
            <span class="ai-gallery__pic">
              <img v-if="r.image?.preview_url" :src="r.image.preview_url" alt="" loading="lazy" />
              <span v-else class="ai-gallery__none">нет превью</span>
            </span>
            <span class="ai-gallery__name">{{ nameOf(r) }}</span>
            <small v-if="metaOf(r)" class="ai-gallery__meta">{{ metaOf(r) }}</small>
          </UiCheckbox>
          <button
            v-if="r.image?.preview_url"
            class="ai-gallery__zoom"
            type="button"
            :data-zoom="r.id"
            :aria-label="`Открыть крупно: ${nameOf(r)}`"
            v-tip="'Открыть крупно'"
            @click="show(r)"
          >
            <ZoomIn aria-hidden="true" />
          </button>
        </li>
      </ul>
    </div>

    <Modal :open="current != null" :title="current ? nameOf(current) : ''" wide @close="hide">
      <div v-if="current" ref="viewer" class="ai-viewer" tabindex="-1">
        <div class="ai-viewer__stage">
          <img :src="current.image.preview_url" :alt="nameOf(current)" />
          <template v-if="viewable.length > 1">
            <button class="ai-viewer__nav ai-viewer__nav--prev" type="button" aria-label="Предыдущая картинка" v-tip="'Предыдущая · ←'" @click="step(-1)">
              <ChevronLeft aria-hidden="true" />
            </button>
            <button class="ai-viewer__nav ai-viewer__nav--next" type="button" aria-label="Следующая картинка" v-tip="'Следующая · →'" @click="step(1)">
              <ChevronRight aria-hidden="true" />
            </button>
          </template>
        </div>
        <div class="ai-viewer__info">
          <p class="ai-viewer__meta">
            <span>{{ position + 1 }} из {{ viewable.length }}</span>
            <span v-if="current.section">{{ current.section }}</span>
            <span v-if="current.cells[1]">{{ current.cells[1] }}</span>
          </p>
          <p v-if="current.cells[2]" class="ai-viewer__why">{{ current.cells[2] }}</p>
          <UiCheckbox
            v-if="!readonly"
            class="check-row"
            :model-value="!isOff(current)"
            :disabled="disabled"
            @update:model-value="flip(current)"
          >
            Взять в работу
          </UiCheckbox>
          <span v-else class="chip" :class="{ 'chip--ok': !isOff(current) }">{{ isOff(current) ? 'снята' : 'взята' }}</span>
        </div>
      </div>
    </Modal>
  </div>
</template>

<script setup>
/**
 * Картинки этапа «Картинки» на согласование: группы с галочками, «выбрано 3 из 4», крупный просмотр со стрелками.
 * v-model:off — id снятых строк, тот же список, что у таблицы карточки (в нём бывают и строки без картинок).
 * rows — [{ id, section, cells: [название, откуда, почему], image: { preview_url } }].
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ChevronLeft, ChevronRight, ZoomIn } from '@lucide/vue'
import Modal from '../../components/ui/Modal.vue'
import UiCheckbox from '../../components/ui/UiCheckbox.vue'

const props = defineProps({
  rows: { type: Array, required: true },
  off: { type: Array, default: () => [] },
  readonly: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:off'])

const root = ref(null)
const viewer = ref(null)
const viewing = ref(null)

const offSet = computed(() => new Set(props.off))
const isOff = (row) => offSet.value.has(row.id)
const nameOf = (row) => row.cells[0] || 'Картинка'
const metaOf = (row) => [row.cells[1], row.cells[2]].filter(Boolean).join(' · ')

const groups = computed(() => {
  const byName = new Map()
  for (const row of props.rows) {
    const name = row.section || ''
    if (!byName.has(name)) byName.set(name, { name, rows: [] })
    byName.get(name).rows.push(row)
  }
  return [...byName.values()].map((g) => ({ ...g, on: g.rows.filter((r) => !isOff(r)).length }))
})

function update(ids, on) {
  const next = new Set(offSet.value)
  for (const id of ids) {
    if (on) next.delete(id)
    else next.add(id)
  }
  emit('update:off', [...next])
}

function flip(row) {
  update([row.id], isOff(row))
}

function setGroup(group, on) {
  update(group.rows.map((r) => r.id), on)
}

// ---------- Крупный просмотр: по всем картинкам карточки, ←/→ листают ----------

const viewable = computed(() => groups.value.flatMap((g) => g.rows).filter((r) => r.image?.preview_url))
const position = computed(() => viewable.value.findIndex((r) => r.id === viewing.value))
const current = computed(() => viewable.value[position.value] ?? null)

function show(row) {
  viewing.value = row.id
}

function hide() {
  const id = viewing.value
  viewing.value = null
  // Фокус — на лупу картинки, которую смотрели последней: с неё и продолжат по Tab.
  nextTick(() => root.value?.querySelector(`[data-zoom="${id}"]`)?.focus())
}

function step(delta) {
  const list = viewable.value
  if (!list.length) return
  viewing.value = list[(position.value + delta + list.length) % list.length].id
}

function onKey(event) {
  if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
  if (event.target.closest?.('input:not([type="checkbox"]), textarea, select')) return
  event.preventDefault()
  step(event.key === 'ArrowLeft' ? -1 : 1)
}

watch(
  () => current.value != null,
  (open) => {
    if (open) {
      window.addEventListener('keydown', onKey)
      nextTick(() => viewer.value?.focus())
    } else {
      window.removeEventListener('keydown', onKey)
    }
  },
)

onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>
