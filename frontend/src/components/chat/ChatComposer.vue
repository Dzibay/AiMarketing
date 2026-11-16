<template>
  <form
    class="composer"
    :class="{ 'composer--big': showIntro && !panel, 'is-off': inputOff, 'is-drop': dropping }"
    @submit.prevent="send()"
    @dragenter="onDragEnter"
    @dragover="onDragOver"
    @dragleave="onDragLeave"
    @drop="onDrop"
  >
    <div v-if="pendingAsk" class="composer__ask" role="group" aria-label="Ждущие предложения">
      <p class="composer__ask-text" role="status">{{ askText }}</p>
      <div class="composer__ask-btns">
        <button class="btn btn--accent btn--sm" type="button" :disabled="sending" @click="answerPending('keep')">
          Оставить и отправить
        </button>
        <button class="btn btn--ghost btn--sm" type="button" :disabled="sending" @click="answerPending('reject')">
          Отклонить и отправить
        </button>
        <button class="btn btn--ghost btn--sm" type="button" :disabled="sending" @click="answerPending('decide')">
          Сначала решить
        </button>
      </div>
    </div>
    <MentionPicker
      v-if="picker.open"
      ref="pickerRef"
      :query="picker.query"
      :workspace-id="general ? null : ws?.id ?? null"
      :projects="otherProjects"
      :general="general"
      @pick="pickMention"
      @close="closePicker"
    />
    <PromptPicker
      v-if="tasksOpen"
      :place="place"
      :anchor="tasksBtn"
      @pick="pickTask"
      @close="closeTasks(true)"
    />
    <ul v-if="attachments.length" class="composer__files" aria-label="Приложенные картинки">
      <li v-for="a in attachments" :key="a.key" class="composer__file" :class="{ 'is-error': a.error }">
        <span class="composer__thumb">
          <img :src="a.preview" alt="" />
          <span v-if="a.busy" class="spinner composer__thumb-spin" aria-hidden="true" />
        </span>
        <span class="composer__file-body">
          <span class="composer__file-name">{{ a.name || 'Картинка' }}</span>
          <span v-if="a.error" class="composer__file-note is-error" role="alert">{{ a.error }}</span>
          <span v-else class="composer__file-use" role="group" :aria-label="`Зачем картинка «${a.name || 'Картинка'}»`">
            <button
              v-if="modelsSeeImages"
              type="button"
              class="composer__use"
              :class="{ 'is-on': a.purpose === 'ai' }"
              :aria-pressed="a.purpose === 'ai'"
              :disabled="a.busy || inputOff"
              v-tip="'ИИ увидит картинку и учтёт её в ответе'"
              @click="setPurpose(a, 'ai')"
            >
              Показать ИИ
            </button>
            <button
              type="button"
              class="composer__use"
              :class="{ 'is-on': a.purpose === 'ads' }"
              :aria-pressed="a.purpose === 'ads'"
              :disabled="a.busy || inputOff || (!!a.direct_problem && a.purpose !== 'ads')"
              v-tip="a.direct_problem || 'ИИ сможет поставить её в объявления; в Директ загрузится после «Выполнить»'"
              @click="setPurpose(a, 'ads')"
            >
              В объявления
            </button>
          </span>
        </span>
        <button
          type="button"
          class="composer__file-del"
          :aria-label="`Убрать картинку «${a.name || 'Картинка'}»`"
          v-tip="'Убрать'"
          @click="removeFile(a)"
        >
          <X aria-hidden="true" />
        </button>
      </li>
    </ul>
    <textarea
      ref="input"
      v-model="draft"
      class="composer__input"
      rows="1"
      maxlength="8000"
      aria-label="Сообщение ИИ"
      :aria-controls="picker.open ? 'mention-list' : null"
      :aria-activedescendant="picker.open ? pickerRef?.activeId ?? null : null"
      :disabled="inputOff"
      :placeholder="placeholder"
      @input="onInput"
      @click="updatePicker"
      @keyup.left="updatePicker"
      @keyup.right="updatePicker"
      @blur="onBlur"
      @keydown="onKeydown"
      @paste="onPaste"
    />
    <input
      ref="fileInput"
      hidden
      type="file"
      accept="image/*"
      multiple
      @change="onFiles"
    />
    <input
      ref="cameraInput"
      hidden
      type="file"
      accept="image/*"
      capture="environment"
      @change="onFiles"
    />
    <div class="composer__row">
      <button
        ref="tasksBtn"
        class="composer__tasks"
        :class="{ 'is-open': tasksOpen }"
        type="button"
        v-tip="'Готовые задачи — или «/» в начале строки'"
        aria-haspopup="dialog"
        :aria-expanded="tasksOpen"
        :disabled="inputOff"
        @mousedown.prevent
        @click="tasksOpen ? closeTasks(true) : openTasks()"
      >
        <ListChecks aria-hidden="true" />
        <span>Задачи</span>
      </button>
      <button
        class="composer__icon"
        type="button"
        v-tip="'Упомянуть кампанию, группу, период или проект'"
        aria-label="Упомянуть кампанию, группу, период или проект"
        :disabled="inputOff"
        @mousedown.prevent
        @click="insertAt"
      >
        <AtSign aria-hidden="true" />
      </button>
      <UiMenu
        v-if="touch"
        class="composer__clip-menu"
        size="sm"
        label="Приложить картинку"
        :disabled="inputOff || attachments.length >= MAX_FILES"
      >
        <template #trigger><Paperclip aria-hidden="true" /></template>
        <UiMenuItem :icon="Camera" @click="cameraInput?.click()">Снять фото</UiMenuItem>
        <UiMenuItem :icon="Images" @click="fileInput?.click()">Выбрать из галереи</UiMenuItem>
      </UiMenu>
      <button
        v-else
        class="composer__icon"
        type="button"
        v-tip="CLIP_TIP"
        aria-label="Приложить картинку"
        :disabled="inputOff || attachments.length >= MAX_FILES"
        @mousedown.prevent
        @click="fileInput?.click()"
      >
        <Paperclip aria-hidden="true" />
      </button>
      <button
        v-if="speech.supported"
        class="composer__icon composer__mic"
        :class="{ 'is-on': speech.listening.value }"
        type="button"
        v-tip="speech.listening.value ? 'Остановить запись' : MIC_TIP"
        :aria-label="speech.listening.value ? 'Остановить запись' : 'Сказать голосом'"
        :aria-pressed="speech.listening.value"
        :disabled="inputOff"
        @mousedown.prevent
        @click="toggleMic"
      >
        <MicOff v-if="speech.listening.value" aria-hidden="true" />
        <Mic v-else aria-hidden="true" />
      </button>
      <UiSelect
        v-if="modelItems.length > 1"
        class="composer__model"
        size="sm"
        :model-value="selectedClass"
        :options="modelOptions"
        :disabled="modelBusy || inputOff"
        aria-label="Модель ответа"
        v-tip="'Модель ответа'"
        @change="chooseModel"
      />
      <span class="composer__tip">{{ composerTip }}</span>
      <button
        class="composer__send"
        type="submit"
        :disabled="!canSend"
        :aria-label="busy ? 'ИИ отвечает' : 'Отправить'"
        v-tip="busy ? 'ИИ отвечает' : 'Отправить'"
      >
        <span v-if="busy" class="spinner" aria-hidden="true" />
        <ArrowUp v-else aria-hidden="true" />
      </button>
    </div>
  </form>
</template>

<script setup>
import { ArrowUp, AtSign, Camera, Images, ListChecks, Mic, MicOff, Paperclip, X } from '@lucide/vue'
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { aiApi, errorText } from '../../api/web'
import { useChat } from '../../composables/useChat'
import { useSpeech } from '../../composables/useSpeech'
import { pushToast } from '../../composables/useToasts'
import { plural } from '../../utils/format'
import { shrinkImage } from '../../utils/image'
import UiMenu from '../ui/UiMenu.vue'
import UiMenuItem from '../ui/UiMenuItem.vue'
import UiSelect from '../ui/UiSelect.vue'
import MentionPicker from './MentionPicker.vue'
import PromptPicker from './PromptPicker.vue'

/**
 * Поле ввода чата: текст, готовые задачи («Задачи» и «/»), @-упоминания (кампания, группа, период, проект),
 * картинки (скрепка, вставка, перетаскивание), модель ответа и кнопка отправки.
 */

// Подсказка к классу модели в списке «Модель ответа».
const MODEL_HINTS = {
  1: 'Быстрые ответы, расходует меньше лимита',
  2: 'Для большинства задач',
  3: 'Думает дольше, расходует лимит быстрее',
}
// Подсказка у микрофона: где идёт распознавание (Chrome отправляет звук в Google).
const MIC_TIP = 'Сказать голосом. Распознаёт браузер: в Chrome — на серверах Google'
// Поле ввода растёт с текстом до ~8 строк, дальше — своя прокрутка.
const INPUT_MAX = 200
// Картинок в одном сообщении — как на сервере (ai_attachments.MAX_PER_MESSAGE).
const MAX_FILES = 4
const CLIP_TIP = 'Приложить картинку — или вставьте (Ctrl+V), или перетащите в поле'
// Телефон: у скрепки выбор «Снять фото» / «Выбрать из галереи».
const touch = typeof window !== 'undefined' && window.matchMedia?.('(pointer: coarse)').matches

const {
  general,
  panel,
  ws,
  draft,
  mentions,
  attachments,
  attachBusy,
  readyAttachments,
  modelsSeeImages,
  otherProjects,
  input,
  status,
  aiConfigured,
  sending,
  showIntro,
  pendingActions,
  pendingAsks,
  waitingWrites,
  pendingAsk,
  answerPending,
  onBoard,
  modelItems,
  selectedClass,
  modelBusy,
  chooseModel,
  send,
} = useChat()

const askText = computed(() => {
  const n = waitingWrites.value.length
  return `В чате ${plural(n, 'ждёт', 'ждут', 'ждут')} решения ${n} ${plural(n, 'предложение', 'предложения', 'предложений')}`
})

const picker = reactive({ open: false, query: '', start: 0, end: 0 })
const pickerRef = ref(null)

const modelOptions = computed(() =>
  modelItems.value.map((m) => ({ value: m.id, label: m.name, hint: MODEL_HINTS[m.dots] || '' })),
)

const placeholder = computed(() => {
  if (!aiConfigured.value) return 'ИИ временно недоступен'
  if (status.value === 'running') return 'ИИ отвечает…'
  if (pendingActions.value.length && pendingActions.value.every((a) => onBoard.value.has(a.id))) {
    return 'Решите этап на доске или напишите своими словами'
  }
  if (pendingAsks.value.length) return 'Ответьте в карточке или напишите своими словами'
  if (waitingWrites.value.length) return 'Решите предложения в карточках или напишите что-то ещё'
  return showIntro.value ? 'Спросите или поручите задачу…' : 'Уточните или попросите что-то ещё…'
})
const composerTip = computed(
  () => 'Enter — отправить · Shift+Enter — перенос · / — задачи · @ — кампания, группа, период',
)
// Пока ИИ отвечает, писать нельзя: остановить ход нечем, новое сообщение ушло бы в середину ответа.
const inputOff = computed(() => status.value === 'running' || !aiConfigured.value)
const busy = computed(() => sending.value || status.value === 'running')
const canSend = computed(
  () =>
    (!!draft.value.trim() || readyAttachments.value.length > 0) &&
    !attachBusy.value &&
    !sending.value &&
    status.value !== 'running' &&
    aiConfigured.value,
)

/** Высота поля по тексту. Страница зовёт её после замера своей высоты и при изменении окна. */
function autosize() {
  const el = input.value
  if (!el) return
  el.style.height = 'auto'
  // Пустое поле меряем по подсказке: длинная подсказка на узком экране иначе обрезается на первой строке.
  const empty = !el.value
  if (empty) el.value = el.placeholder
  const height = el.scrollHeight
  if (empty) el.value = ''
  el.style.height = `${Math.min(height, INPUT_MAX)}px`
  el.style.overflowY = height > INPUT_MAX ? 'auto' : 'hidden'
}

// --- Готовые задачи -----------------------------------------------------------------------------

const place = computed(() => (general ? 'general' : ws?.value?.campaigns_count ? 'project' : 'first'))
const tasksOpen = ref(false)
const tasksBtn = ref(null)
// Куда встанет выбранная задача: курсор на момент открытия списка.
let taskAt = { start: 0, end: 0 }

function openTasks() {
  const el = input.value
  const pos = el?.selectionStart ?? draft.value.length
  taskAt = { start: pos, end: el?.selectionEnd ?? pos }
  closePicker()
  tasksOpen.value = true
}

function closeTasks(refocus) {
  tasksOpen.value = false
  if (!refocus) return
  const el = input.value
  nextTick(() => {
    el?.focus()
    el?.setSelectionRange(taskAt.start, taskAt.end)
  })
}

/** Текст задачи — в поле на место курсора, отдельной строкой; отправляет человек сам. */
function pickTask(text) {
  const before = draft.value.slice(0, taskAt.start)
  const after = draft.value.slice(taskAt.end)
  const lead = before && !before.endsWith('\n') ? '\n' : ''
  const tail = after && !after.startsWith('\n') ? '\n' : ''
  draft.value = `${before}${lead}${text}${tail}${after}`
  const caret = before.length + lead.length + text.length
  taskAt = { start: caret, end: caret }
  closeTasks(true)
}

/** «/» в начале строки открывает задачи; сам знак убираем. */
function slashTasks(e) {
  const el = input.value
  if (!el || e.inputType !== 'insertText' || e.data !== '/') return false
  const caret = el.selectionStart ?? 0
  const at = caret - 1
  if (draft.value[at] !== '/' || (at > 0 && draft.value[at - 1] !== '\n')) return false
  draft.value = draft.value.slice(0, at) + draft.value.slice(caret)
  nextTick(() => {
    el.setSelectionRange(at, at)
    openTasks()
  })
  return true
}

function onInput(e) {
  if (!slashTasks(e)) updatePicker()
}

// --- @-упоминания ------------------------------------------------------------------------------

function closePicker() {
  picker.open = false
}

/** Окно «@», пока перед курсором стоит «@запрос» (в начале строки или после пробела). */
function updatePicker() {
  const el = input.value
  if (!el) return closePicker()
  const caret = el.selectionStart ?? 0
  const before = el.value.slice(0, caret)
  const at = before.lastIndexOf('@')
  if (at === -1 || (at > 0 && !/\s/.test(before[at - 1]))) return closePicker()
  const query = before.slice(at + 1)
  if (query.length > 60 || /^\s|\n/.test(query)) return closePicker()
  Object.assign(picker, { open: true, query, start: at, end: caret })
}

// Фокус ушёл в само окно (календарь своего периода) — окно остаётся.
function onBlur(e) {
  if (!pickerRef.value?.contains(e.relatedTarget)) closePicker()
}

const mentionKey = (m) => (m.type === 'period' ? `period:${m.from}:${m.to}` : `${m.type || 'project'}:${m.id}`)

function pickMention(m) {
  const el = input.value
  const insert = `@${m.name} `
  draft.value = draft.value.slice(0, picker.start) + insert + draft.value.slice(picker.end)
  if (!mentions.value.some((x) => mentionKey(x) === mentionKey(m))) mentions.value.push(m)
  const caret = picker.start + insert.length
  closePicker()
  nextTick(() => {
    el?.focus()
    el?.setSelectionRange(caret, caret)
  })
}

/** Кнопка «@»: ставит @ в курсор и открывает список — для тех, кто не знает про клавишу, и для телефона. */
function insertAt() {
  const el = input.value
  if (!el) return
  const pos = el.selectionStart ?? draft.value.length
  const before = draft.value.slice(0, pos)
  const lead = before && !/\s$/.test(before) ? ' ' : ''
  draft.value = `${before}${lead}@${draft.value.slice(el.selectionEnd ?? pos)}`
  const caret = pos + lead.length + 1
  nextTick(() => {
    el.focus()
    el.setSelectionRange(caret, caret)
    updatePicker()
  })
}

function onKeydown(e) {
  if (picker.open && pickerRef.value?.onKey(e)) {
    e.preventDefault()
    return
  }
  if (e.key === 'Escape' && pendingAsk.value) {
    pendingAsk.value = null
    e.preventDefault()
    return
  }
  if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey) {
    e.preventDefault()
    send()
  }
}

// --- Картинки ----------------------------------------------------------------------------------

const fileInput = ref(null)
const cameraInput = ref(null)
const dropping = ref(false)
let dragDepth = 0
let fileKey = 0

/** Картинки из выбора, вставки или перетаскивания: уменьшаем в браузере и сразу загружаем. */
function addFiles(list) {
  const images = [...(list || [])].filter((f) => f.type.startsWith('image/'))
  if (!images.length) return false
  const room = MAX_FILES - attachments.value.length
  if (room <= 0) {
    pushToast(`В сообщении — до ${MAX_FILES} картинок`, 'error')
    return true
  }
  if (images.length > room) pushToast(`В сообщении — до ${MAX_FILES} картинок, лишние не добавлены`, 'error')
  for (const file of images.slice(0, room)) upload(file)
  return true
}

async function upload(file) {
  const item = reactive({
    key: ++fileKey,
    id: null,
    name: (file.name || '').replace(/\.[^.]+$/, '').slice(0, 120),
    preview: '',
    width: 0,
    height: 0,
    // Модели картинок не видят — сразу «В объявления».
    purpose: modelsSeeImages.value ? 'ai' : 'ads',
    direct_problem: '',
    busy: true,
    error: '',
  })
  attachments.value.push(item)
  try {
    const shrunk = await shrinkImage(file)
    item.preview = shrunk.dataUrl
    let res
    try {
      res = await aiApi.uploadAttachment({ data: shrunk.dataUrl, name: item.name, purpose: item.purpose })
    } catch (err) {
      // Для объявлений не подошла по размеру — оставляем «Показать ИИ», если модели видят картинки.
      if (item.purpose !== 'ads' || !modelsSeeImages.value) throw err
      res = await aiApi.uploadAttachment({ data: shrunk.dataUrl, name: item.name, purpose: 'ai' })
    }
    Object.assign(item, res, { preview: shrunk.dataUrl })
  } catch (err) {
    item.error = err?.message === 'image' ? 'Не удалось прочитать картинку' : err?.message === 'too-big' ? 'Слишком большая картинка' : errorText(err)
  } finally {
    item.busy = false
  }
}

async function setPurpose(item, purpose) {
  if (!item.id || item.purpose === purpose) return
  item.busy = true
  try {
    const res = await aiApi.setAttachmentPurpose(item.id, purpose)
    Object.assign(item, res, { preview: item.preview })
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    item.busy = false
  }
}

function removeFile(item) {
  attachments.value = attachments.value.filter((a) => a !== item)
  input.value?.focus()
}

function onFiles(e) {
  addFiles(e.target.files)
  e.target.value = ''
  input.value?.focus()
}

/** Скриншот из буфера: Ctrl+V в поле. Текст вставляется как обычно. */
function onPaste(e) {
  if (inputOff.value) return
  const files = [...(e.clipboardData?.files || [])]
  if (files.some((f) => f.type.startsWith('image/'))) {
    addFiles(files)
    if (!e.clipboardData.getData('text')) e.preventDefault()
  }
}

const dragsFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files')

function onDragEnter(e) {
  if (inputOff.value || !dragsFiles(e)) return
  e.preventDefault()
  dragDepth += 1
  dropping.value = true
}

function onDragOver(e) {
  if (inputOff.value || !dragsFiles(e)) return
  e.preventDefault()
  e.dataTransfer.dropEffect = 'copy'
}

function onDragLeave() {
  dragDepth = Math.max(0, dragDepth - 1)
  if (!dragDepth) dropping.value = false
}

function onDrop(e) {
  dragDepth = 0
  dropping.value = false
  if (inputOff.value || !dragsFiles(e)) return
  e.preventDefault()
  if (!addFiles(e.dataTransfer.files)) pushToast('Приложить можно только картинки', 'error')
  input.value?.focus()
}

// --- Голосовой ввод ------------------------------------------------------------------------------

// Сказанное встаёт после того, что уже было в поле на момент начала записи.
let spokenBase = ''
const speech = useSpeech((said) => {
  draft.value = spokenBase + (spokenBase && said ? ' ' : '') + said
})

function toggleMic() {
  if (!speech.listening.value) {
    spokenBase = draft.value.replace(/\s+$/, '')
    closePicker()
  }
  speech.toggle()
  input.value?.focus()
}

watch(speech.error, (text) => text && pushToast(text, 'error'))
// Запись не переживает отправку и ход ИИ.
watch([inputOff, sending], ([off, busyNow]) => {
  if (off || busyNow) speech.stop()
})

watch([draft, showIntro, placeholder], () => nextTick(autosize))

defineExpose({ autosize })
</script>
