<template>
  <article class="board-group">
    <header class="board-group__head">
      <form v-if="renaming" class="board-group__rename" @submit.prevent="rename">
        <input
          ref="nameField"
          v-model="name"
          class="ai-input"
          maxlength="120"
          aria-label="Название группы"
          @keydown.esc.prevent="renaming = false"
        />
        <button class="btn btn--accent btn--xs" type="submit" :disabled="!canRename">Сохранить</button>
        <button class="btn btn--ghost btn--xs" type="button" @click="renaming = false">Отмена</button>
      </form>
      <template v-else>
        <strong>{{ group.name }}</strong>
        <small v-if="onKeywords || !onAudiences">{{ onKeywords }} {{ plural(onKeywords, 'фраза', 'фразы', 'фраз') }}</small>
        <small v-else>по аудитории, без фраз</small>
        <span class="board-group__tools">
          <button class="ai-check__link" type="button" :disabled="board.disabled.value" @click="startRename">
            Переименовать
          </button>
          <select
            v-if="others.length"
            class="board-select"
            aria-label="Объединить с группой"
            :disabled="board.disabled.value"
            @change="merge($event)"
          >
            <option value="">Объединить с…</option>
            <option v-for="n in others" :key="n" :value="n">{{ n }}</option>
          </select>
        </span>
      </template>
    </header>

    <ul v-if="group.keywords.length" class="board-chips">
      <li
        v-for="k in shown"
        :key="k.text"
        :class="{ 'is-pending': k.pending, 'is-off': !board.isOn(k), 'is-own': k.own, 'is-selected': selected.includes(k.text) }"
      >
        <input
          v-if="k.pending"
          type="checkbox"
          :checked="board.isOn(k)"
          :disabled="board.disabled.value"
          :aria-label="k.text"
          @change="board.toggle(k)"
        />
        <button
          type="button"
          class="board-chips__text"
          :title="k.note || 'Выбрать: перенести, изменить или убрать'"
          :aria-pressed="selected.includes(k.text)"
          :disabled="board.disabled.value"
          @click="select(k)"
        >
          {{ k.text }}<small v-if="k.value">{{ k.value }}</small>
        </button>
      </li>
      <li v-if="group.keywords.length > KEYWORDS_SHOWN" class="board-chips__more">
        <button class="ai-check__link" type="button" @click="expanded = !expanded">
          {{ expanded ? 'свернуть' : `ещё ${group.keywords.length - KEYWORDS_SHOWN}` }}
        </button>
      </li>
    </ul>

    <form v-if="editing" class="board-group__bar" @submit.prevent="saveKeyword">
      <input
        ref="keywordField"
        v-model="keyword"
        class="ai-input"
        maxlength="4096"
        aria-label="Фраза"
        @keydown.esc.prevent="editing = false"
      />
      <button class="btn btn--accent btn--xs" type="submit" :disabled="!keyword.trim() || keyword.trim() === selected[0]">
        Сохранить
      </button>
      <button class="btn btn--ghost btn--xs" type="button" @click="editing = false">Отмена</button>
    </form>
    <div v-else-if="selected.length" class="board-group__bar">
      <span>Выбрано: {{ selected.length }}</span>
      <select v-if="others.length" v-model="moveTo" class="board-select" aria-label="Перенести в группу">
        <option value="">Перенести в…</option>
        <option v-for="n in others" :key="n" :value="n">{{ n }}</option>
      </select>
      <button v-if="moveTo" class="btn btn--accent btn--xs" type="button" :disabled="board.disabled.value" @click="move">
        Перенести
      </button>
      <button v-if="selected.length === 1" class="btn btn--xs" type="button" :disabled="board.disabled.value" @click="startEdit">
        Изменить
      </button>
      <button class="btn btn--xs" type="button" :disabled="board.disabled.value" @click="removeSelected">Убрать</button>
      <button class="ai-check__link" type="button" @click="clear">Отмена</button>
    </div>

    <form class="board-add" @submit.prevent="addKeyword">
      <input
        v-model="fresh"
        class="ai-input"
        maxlength="4096"
        placeholder="+ фраза"
        aria-label="Новая фраза в группу"
        :disabled="board.disabled.value"
      />
      <button v-if="fresh.trim()" class="btn btn--xs" type="submit" :disabled="board.disabled.value">Добавить</button>
    </form>

    <BoardAudiences v-if="audiences.length" :items="audiences" :group="group.name" :keywords="onKeywords" />

    <div v-if="hasAd" class="board-group__ad" :class="{ 'is-both': preview === 'both' }">
      <div class="board-group__lines">
        <BoardLines :items="group.titles" kind="title" :group="group.name" />
        <BoardLines :items="group.texts" kind="text" :group="group.name" />
        <BoardImages :items="images" :group="group.name" />
      </div>
      <div class="board-group__previews">
        <figure v-if="preview !== 'mobile'" class="board-group__preview">
          <figcaption v-if="preview === 'both'">На компьютере</figcaption>
          <AdPreview
            :titles="titles"
            :texts="texts"
            :href="group.href"
            :site="site"
            :pending="adPending"
            :sitelinks="links"
            :callouts="notes"
            :images="imageUrls"
          />
        </figure>
        <figure v-if="preview !== 'desktop'" class="board-group__preview board-group__preview--mobile">
          <figcaption v-if="preview === 'both'">На телефоне</figcaption>
          <AdPreview
            mobile
            :titles="titles"
            :texts="texts"
            :href="group.href"
            :site="site"
            :pending="adPending"
            :sitelinks="links"
            :callouts="notes"
            :images="imageUrls"
          />
        </figure>
      </div>
    </div>
    <p v-else class="hint">Заголовки и тексты появятся на этапе «Объявления».</p>
    <p v-if="group.href" class="hint board-group__href">
      Ссылка: <a :href="group.href" target="_blank" rel="noopener">{{ group.href }}</a>
    </p>
  </article>
</template>

<script setup>
/**
 * Группа на доске: фразы (галочки ждущей таблицы, выбор → перенести, изменить, убрать; новая фраза),
 * аудитории, заголовки и тексты с правкой, картинки и предпросмотр объявления на компьютере и телефоне.
 */
import { computed, inject, nextTick, ref, watch } from 'vue'
import { askConfirm } from '../../composables/useConfirm'
import { plural } from '../../utils/format'
import AdPreview from './AdPreview.vue'
import BoardAudiences from './BoardAudiences.vue'
import BoardImages from './BoardImages.vue'
import BoardLines from './BoardLines.vue'

const props = defineProps({
  group: { type: Object, required: true },
  // Названия всех групп доски — куда переносить и с чем объединять.
  names: { type: Array, default: () => [] },
  site: { type: String, default: '' },
  // desktop | mobile | both
  preview: { type: String, default: 'desktop' },
  // Быстрые ссылки и уточнения кампании — если у группы своих нет.
  sitelinks: { type: Array, default: () => [] },
  callouts: { type: Array, default: () => [] },
  // Этап объявлений начался — заголовки можно вписывать, даже если все убрали.
  adsStarted: { type: Boolean, default: false },
})
const board = inject('board')

const KEYWORDS_SHOWN = 14

const expanded = ref(false)
const selected = ref([])
const moveTo = ref('')
const editing = ref(false)
const keyword = ref('')
const keywordField = ref(null)
const fresh = ref('')
const renaming = ref(false)
const name = ref('')
const nameField = ref(null)

const others = computed(() => props.names.filter((n) => n !== props.group.name))
const shown = computed(() => (expanded.value ? props.group.keywords : props.group.keywords.slice(0, KEYWORDS_SHOWN)))
const onKeywords = computed(() => props.group.keywords.filter(board.isOn).length)
const audiences = computed(() => props.group.audiences || [])
const onAudiences = computed(() => audiences.value.filter(board.isOn).length)
const canRename = computed(() => {
  const value = name.value.trim()
  return !!value && value !== props.group.name && !others.value.includes(value) && !board.disabled.value
})

const images = computed(() => props.group.images || [])
const hasAd = computed(
  () => props.adsStarted || props.group.titles.length > 0 || props.group.texts.length > 0 || images.value.length > 0,
)
const titles = computed(() => props.group.titles.filter(board.isOn).map((t) => t.text))
const texts = computed(() => props.group.texts.filter(board.isOn).map((t) => t.text))
const imageUrls = computed(() => images.value.filter(board.isOn).map((i) => i.preview_url).filter(Boolean))
const adPending = computed(() => [...props.group.titles, ...props.group.texts, ...images.value].some((t) => t.pending))
const links = computed(() => (props.group.sitelinks ?? props.sitelinks).filter(board.isOn))
const notes = computed(() => (props.group.callouts ?? props.callouts).filter(board.isOn).map((c) => c.text))

// Фразу перенесли или убрали — из выбора её тоже долой.
watch(
  () => props.group.keywords.map((k) => k.text),
  (texts) => {
    selected.value = selected.value.filter((t) => texts.includes(t))
    if (!selected.value.length) editing.value = false
  },
)

function select(k) {
  editing.value = false
  selected.value = selected.value.includes(k.text) ? selected.value.filter((t) => t !== k.text) : [...selected.value, k.text]
}

function clear() {
  selected.value = []
  moveTo.value = ''
  editing.value = false
}

function chosen() {
  return props.group.keywords.filter((k) => selected.value.includes(k.text))
}

async function move() {
  const ops = chosen().map((k) => ({ op: 'move', kind: 'keyword', group: props.group.name, text: k.text, to_group: moveTo.value }))
  if (ops.length && (await board.edit(ops))) clear()
}

async function removeSelected() {
  if (await board.remove('keyword', props.group.name, chosen())) clear()
}

async function startEdit() {
  keyword.value = selected.value[0]
  editing.value = true
  await nextTick()
  keywordField.value?.focus()
}

async function saveKeyword() {
  const to = keyword.value.trim()
  if (!to || to === selected.value[0]) return
  const ok = await board.edit([{ op: 'edit', kind: 'keyword', group: props.group.name, text: selected.value[0], to }])
  if (ok) clear()
}

async function addKeyword() {
  const text = fresh.value.trim()
  if (!text) return
  if (await board.edit([{ op: 'add', kind: 'keyword', group: props.group.name, text }])) fresh.value = ''
}

async function startRename() {
  name.value = props.group.name
  renaming.value = true
  await nextTick()
  nameField.value?.focus()
}

async function rename() {
  if (!canRename.value) return
  if (await board.edit([{ op: 'rename_group', group: props.group.name, to: name.value.trim() }])) renaming.value = false
}

async function merge(event) {
  const target = event.target.value
  event.target.value = ''
  if (!target) return
  const ok = await askConfirm(
    `Объединить «${props.group.name}» с «${target}»? Фразы перейдут в «${target}»; объявление останется от «${target}», а если его там нет — возьмётся из «${props.group.name}».`,
  )
  if (ok) await board.edit([{ op: 'rename_group', group: props.group.name, to: target }])
}
</script>
