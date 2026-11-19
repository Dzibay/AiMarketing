<template>
  <div class="board-extras">
    <div class="board-lines">
      <div class="board-lines__head">
        <span>Быстрые ссылки</span>
        <small :class="{ 'tone-bad': linksOn > MAX.sitelink }">{{ linksOn }} из {{ MAX.sitelink }}</small>
      </div>
      <ul v-if="sitelinks.length" class="board-links">
        <li
          v-for="l in sitelinks"
          :key="l.text"
          :class="{ 'is-pending': l.pending, 'is-off': !board.isOn(l), 'is-own': l.own }"
        >
          <template v-if="editing !== l.text">
            <UiCheckbox
              v-if="l.pending"
              :checked="board.isOn(l)"
              :disabled="board.disabled.value"
              :aria-label="l.text"
              @change="board.toggle(l)"
            />
            <button
              class="board-extras__title"
              type="button"
              v-tip="'Изменить'"
              :disabled="board.disabled.value"
              @click="startLink(l)"
            >
              <strong>{{ l.text }}</strong>
            </button>
            <BoardRule :item="l" />
            <button
              class="board-x board-extras__x"
              type="button"
              v-tip="removeTip(l)"
              :aria-label="`Убрать: ${l.text}`"
              :disabled="board.disabled.value"
              @click="board.remove('sitelink', '', [l])"
            >
              ×
            </button>
            <small v-if="l.description">{{ l.description }}</small>
            <small v-if="l.href" class="board-links__href">{{ l.href }}</small>
            <small v-else-if="board.isOn(l)" class="tone-bad">нет адреса страницы — нажмите на ссылку и впишите</small>
          </template>
          <form v-else class="board-extras__form" @submit.prevent="saveLink(l)">
            <label v-for="f in LINK_FIELDS" :key="f.key" class="board-extras__field">
              <span>{{ f.label }}</span>
              <input
                :ref="(el) => f.key === 'text' && setLinkField(el)"
                v-model="form[f.key]"
                class="ai-input"
                :maxlength="f.limit ? f.limit + 20 : 1024"
                :placeholder="f.placeholder"
                v-bind="f.attrs || {}"
                @keydown.esc.prevent="cancel"
              />
              <small v-if="f.limit" class="board-lines__len" :class="{ 'tone-bad': form[f.key].trim().length > f.limit }">
                {{ form[f.key].trim().length }}/{{ f.limit }}
              </small>
            </label>
            <div class="board-extras__buttons">
              <button class="btn btn--accent btn--xs" type="submit" :disabled="!canSaveLink(l)">Сохранить</button>
              <button class="btn btn--ghost btn--xs" type="button" @click="cancel">Отмена</button>
            </div>
          </form>
        </li>
      </ul>
      <form v-if="editing === ADD_LINK" class="board-extras__form board-extras__form--add" @submit.prevent="addLink">
        <label v-for="f in LINK_FIELDS" :key="f.key" class="board-extras__field">
          <span>{{ f.label }}</span>
          <input
            :ref="(el) => f.key === 'text' && setLinkField(el)"
            v-model="form[f.key]"
            class="ai-input"
            :maxlength="f.limit ? f.limit + 20 : 1024"
            :placeholder="f.placeholder"
            v-bind="f.attrs || {}"
            @keydown.esc.prevent="cancel"
          />
          <small v-if="f.limit" class="board-lines__len" :class="{ 'tone-bad': form[f.key].trim().length > f.limit }">
            {{ form[f.key].trim().length }}/{{ f.limit }}
          </small>
        </label>
        <div class="board-extras__buttons">
          <button class="btn btn--accent btn--xs" type="submit" :disabled="!canAddLink">Добавить</button>
          <button class="btn btn--ghost btn--xs" type="button" @click="cancel">Отмена</button>
        </div>
      </form>
      <button
        v-else-if="linksOn < MAX.sitelink"
        class="ai-check__link board-extras__more"
        type="button"
        :disabled="board.disabled.value"
        @click="startAdd"
      >
        + быстрая ссылка
      </button>
    </div>

    <div class="board-lines">
      <div class="board-lines__head">
        <span>Уточнения</span>
        <small :class="{ 'tone-bad': calloutsOn > MAX.callout }">{{ calloutsOn }} из {{ MAX.callout }}</small>
      </div>
      <ul v-if="callouts.length" class="board-chips">
        <li
          v-for="c in callouts"
          :key="c.text"
          :class="{ 'is-pending': c.pending, 'is-off': !board.isOn(c), 'is-own': c.own, 'is-selected': editing === `~${c.text}` }"
        >
          <UiCheckbox
            v-if="c.pending"
            :checked="board.isOn(c)"
            :disabled="board.disabled.value"
            :aria-label="c.text"
            @change="board.toggle(c)"
          />
          <button
            class="board-chips__text"
            type="button"
            v-tip="'Изменить'"
            :disabled="board.disabled.value"
            @click="startCallout(c)"
          >
            {{ c.text }}
          </button>
          <BoardRule :item="c" />
          <button
            class="board-x"
            type="button"
            v-tip="removeTip(c)"
            :aria-label="`Убрать: ${c.text}`"
            :disabled="board.disabled.value"
            @click="board.remove('callout', '', [c])"
          >
            ×
          </button>
        </li>
      </ul>
      <form v-if="editedCallout" class="board-lines__add" @submit.prevent="saveCallout(editedCallout)">
        <input
          ref="calloutField"
          v-model="callout"
          class="ai-input"
          :maxlength="LIMIT.callout + 20"
          aria-label="Уточнение: новый текст"
          @keydown.esc.prevent="cancel"
        />
        <small class="board-lines__len" :class="{ 'tone-bad': callout.trim().length > LIMIT.callout }">
          {{ callout.trim().length }}/{{ LIMIT.callout }}
        </small>
        <button class="btn btn--accent btn--xs" type="submit" :disabled="!canSaveCallout(editedCallout)">Сохранить</button>
        <button class="btn btn--ghost btn--xs" type="button" @click="cancel">Отмена</button>
      </form>
      <form v-else-if="calloutsOn < MAX.callout" class="board-add" @submit.prevent="addCallout">
        <input
          v-model="fresh"
          class="ai-input"
          :maxlength="LIMIT.callout + 20"
          placeholder="+ уточнение, например «Гарантия 5 лет»"
          aria-label="Новое уточнение"
          :disabled="board.disabled.value"
        />
        <small v-if="fresh.trim()" class="board-lines__len" :class="{ 'tone-bad': fresh.trim().length > LIMIT.callout }">
          {{ fresh.trim().length }}/{{ LIMIT.callout }}
        </small>
        <button v-if="fresh.trim()" class="btn btn--xs" type="submit" :disabled="!canAddCallout">Добавить</button>
      </form>
    </div>
  </div>
</template>

<script setup>
/**
 * Быстрые ссылки и уточнения на доске: правка по щелчку со счётчиками знаков, «×» и новые строки.
 * Они общие для объявлений кампании — правка без группы; уходит в то же одно согласование доски.
 */
import { computed, inject, nextTick, reactive, ref } from 'vue'
import UiCheckbox from '../../components/ui/UiCheckbox.vue'
import BoardRule from './BoardRule.vue'

const props = defineProps({
  // Быстрые ссылки и уточнения доски: из таблиц этапа, а нет их там — из карточки создания.
  sitelinks: { type: Array, default: () => [] },
  callouts: { type: Array, default: () => [] },
})
const board = inject('board')

// Пределы Директа: заголовок ссылки, описание, уточнение; сколько всего.
const LIMIT = { sitelink: 30, description: 60, callout: 25 }
const MAX = { sitelink: 8, callout: 50 }
const ADD_LINK = '+'
const LINK_FIELDS = [
  { key: 'text', label: 'Название', limit: LIMIT.sitelink, placeholder: 'Например, Доставка' },
  { key: 'description', label: 'Описание', limit: LIMIT.description, placeholder: 'Необязательно' },
  {
    key: 'href',
    label: 'Адрес страницы',
    limit: 0,
    placeholder: 'https://site.ru/dostavka',
    // Клавиатура телефона не ставит заглавную в начале адреса. Без type="url": он не пустил бы «site.ru/x» без схемы.
    attrs: { inputmode: 'url', autocapitalize: 'off', autocorrect: 'off', autocomplete: 'off', spellcheck: 'false' },
  },
]

const linksOn = computed(() => props.sitelinks.filter(board.isOn).length)
const calloutsOn = computed(() => props.callouts.filter(board.isOn).length)

// Что правят сейчас: название ссылки, ADD_LINK — новая ссылка, «~текст» — уточнение.
const editing = ref(null)
const form = reactive({ text: '', description: '', href: '' })
const linkField = ref(null)
const callout = ref('')
const calloutField = ref(null)
const fresh = ref('')

const editedCallout = computed(() =>
  editing.value?.startsWith('~') ? props.callouts.find((c) => `~${c.text}` === editing.value) || null : null,
)

function removeTip(item) {
  return item.pending && !item.added && !item.shared ? 'Снять галочку' : 'Убрать'
}

function cancel() {
  editing.value = null
}

function fits(value, limit) {
  const text = value.trim()
  return !!text && text.length <= limit
}

function linkOk() {
  return (
    fits(form.text, LIMIT.sitelink) &&
    form.description.trim().length <= LIMIT.description &&
    !!form.href.trim() &&
    !board.disabled.value
  )
}

function canSaveLink(l) {
  const changed =
    form.text.trim() !== l.text ||
    form.description.trim() !== (l.description || '') ||
    form.href.trim() !== (l.href || '')
  return linkOk() && changed
}

const canAddLink = computed(() => linkOk())
const canAddCallout = computed(() => fits(fresh.value, LIMIT.callout) && !board.disabled.value)

function canSaveCallout(c) {
  return fits(callout.value, LIMIT.callout) && callout.value.trim() !== c.text && !board.disabled.value
}

function setLinkField(el) {
  if (el) linkField.value = el
}

async function focus(field) {
  await nextTick()
  field.value?.focus()
}

function startLink(l) {
  Object.assign(form, { text: l.text, description: l.description || '', href: l.href || '' })
  editing.value = l.text
  focus(linkField)
}

function startAdd() {
  Object.assign(form, { text: '', description: '', href: '' })
  editing.value = ADD_LINK
  focus(linkField)
}

function startCallout(c) {
  callout.value = c.text
  editing.value = `~${c.text}`
  focus(calloutField)
}

function linkOp(op, text) {
  return { op, kind: 'sitelink', group: '', text, href: form.href.trim(), description: form.description.trim() }
}

async function saveLink(l) {
  if (!canSaveLink(l)) return
  if (await board.edit([{ ...linkOp('edit', l.text), to: form.text.trim() }])) cancel()
}

async function addLink() {
  if (!canAddLink.value) return
  if (await board.edit([linkOp('add', form.text.trim())])) cancel()
}

async function saveCallout(c) {
  if (!canSaveCallout(c)) return
  if (await board.edit([{ op: 'edit', kind: 'callout', group: '', text: c.text, to: callout.value.trim() }])) cancel()
}

async function addCallout() {
  if (!canAddCallout.value) return
  if (await board.edit([{ op: 'add', kind: 'callout', group: '', text: fresh.value.trim() }])) fresh.value = ''
}
</script>

<style scoped>
.board-extras {
  display: grid;
  gap: 0.8rem;
}

.board-extras__title {
  appearance: none;
  padding: 0;
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  text-align: left;
  overflow-wrap: anywhere;
  cursor: pointer;
}

.board-extras__title:disabled {
  cursor: default;
}

.board-extras__x {
  margin-left: auto;
}

.board-extras__form {
  display: grid;
  flex-basis: 100%;
  gap: 0.4rem;
}

.board-extras__form--add {
  padding: 0.5rem 0.6rem;
  border: 1px dashed var(--line);
  border-radius: 10px;
}

.board-extras__field {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.15rem 0.4rem;
  font-size: 0.78rem;
}

.board-extras__field > span {
  grid-column: 1 / -1;
  color: var(--muted);
}

.board-extras__field .ai-input {
  min-width: 0;
  padding: 0.35rem 0.6rem;
  font-size: 0.84rem;
}

.board-extras__buttons {
  display: flex;
  gap: 0.4rem;
}

.board-extras .tone-bad {
  color: var(--danger);
}

.board-extras__more {
  justify-self: start;
  margin-top: 0.3rem;
}
</style>
