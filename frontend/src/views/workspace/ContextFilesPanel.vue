<template>
  <section id="context-files" class="panel">
    <div class="panel__head">
      <div>
        <h2>Файлы для ИИ</h2>
        <p class="hint">
          Контекст проекта текстом: .txt и .md по одному или zip-архивом с ними — например, выгрузка диалога из ChatGPT.
          ИИ читает эти файлы в каждом чате пространства.
        </p>
      </div>
      <div class="row-actions">
        <button class="btn btn--accent" type="button" :disabled="uploading" @click="fileInput?.click()">
          {{ uploading ? uploadText : 'Загрузить файлы' }}
        </button>
        <input
          ref="fileInput"
          class="ctx-input"
          type="file"
          multiple
          accept=".txt,.md,.zip,text/plain,text/markdown,application/zip"
          aria-label="Файлы контекста"
          @change="onPick"
        />
      </div>
    </div>

    <div
      class="ctx-drop"
      :class="{ 'is-over': dragOver }"
      @dragover.prevent="dragOver = true"
      @dragleave.prevent="dragOver = false"
      @drop.prevent="onDrop"
    >
      Перетащите сюда .txt, .md или .zip — до {{ mb(limits.upload_bytes) }} за раз, в архиве до
      {{ limits.zip_files }} текстовых файлов. Файл с тем же именем заменит загруженный раньше.
    </div>

    <p v-if="error" class="form-error">{{ error }}</p>
    <div v-if="problems.length" class="notice notice--warn">
      <strong>Не загружено:</strong>
      <ul class="ctx-problems">
        <li v-for="(p, i) in problems" :key="i">{{ p.name }} — {{ p.reason }}</li>
      </ul>
    </div>

    <template v-if="items.length">
      <div class="ctx-usage">
        <p class="ctx-usage__text">
          В ИИ уходит <b>{{ num(usage.sent_chars) }}</b> из {{ num(usage.chars) }}
          {{ plural(usage.chars, 'символа', 'символов', 'символов') }} — около {{ num(usage.tokens) }}
          {{ plural(usage.tokens, 'токена', 'токенов', 'токенов') }} при каждом вызове модели. Лимит пространства —
          {{ num(usage.limit) }} {{ plural(usage.limit, 'символ', 'символа', 'символов') }}.
        </p>
        <div
          class="meter"
          :class="{ 'is-near': usage.sent_chars >= usage.limit * 0.9 }"
          role="progressbar"
          :aria-valuenow="fillPct"
          aria-valuemin="0"
          aria-valuemax="100"
        >
          <span :style="{ width: `${fillPct}%` }" />
        </div>
        <div v-field class="field ctx-limit">
          <label>Лимит на файлы в промпте, символов</label>
          <div class="ctx-limit__row">
            <input
              v-model.number="limitDraft"
              type="number"
              :min="limits.prompt_min"
              :max="limits.prompt_max"
              step="1000"
              @keydown.enter.prevent="saveLimit"
            />
            <button class="btn btn--ghost" type="button" :disabled="!limitChanged || savingLimit" @click="saveLimit">
              {{ savingLimit ? 'Сохраняем…' : 'Сохранить' }}
            </button>
          </div>
          <small class="field-hint">
            От {{ num(limits.prompt_min) }} до {{ num(limits.prompt_max) }}, по умолчанию
            {{ num(limits.prompt_default) }}. {{ num(limitDraft || 0) }} символов — около
            {{ num(Math.round((limitDraft || 0) / limits.chars_per_token)) }} токенов в каждом вызове: больше лимит —
            дороже ответы, а модель с небольшим контекстом (например, YandexGPT) может не вместить разговор.
          </small>
        </div>
        <p v-if="usage.cut.length || usage.out.length" class="notice notice--warn ctx-usage__warn">
          Не всё поместилось в лимит.
          <template v-if="usage.cut.length">Обрезан: {{ usage.cut.join(', ') }}.</template>
          <template v-if="usage.out.length"> ИИ не видит: {{ usage.out.join(', ') }}.</template>
          Файлы идут в промпт по порядку загрузки — выключите ненужные, и ИИ увидит следующие.
        </p>
      </div>

      <ul class="ctx-files">
        <li v-for="f in items" :key="f.id" class="ctx-files__item" :class="{ 'is-off': !f.enabled }">
          <label class="check-row ctx-files__main">
            <input
              type="checkbox"
              :checked="f.enabled"
              :disabled="busyId === f.id"
              :aria-label="`Отправлять ИИ «${f.filename}»`"
              @change="toggle(f, $event.target.checked)"
            />
            <span class="ctx-files__name">
              {{ f.filename }}
              <small class="cell-note">
                {{ num(f.chars) }} {{ plural(f.chars, 'символ', 'символа', 'символов') }}
                <template v-if="f.source"> · из {{ f.source }}</template>
                · {{ formatDateTime(f.uploaded_at) }}
              </small>
            </span>
          </label>
          <span class="chip" :class="STATES[f.state]?.cls">{{ stateText(f) }}</span>
          <button class="btn btn--ghost" type="button" :disabled="busyId === f.id" @click="remove(f)">Удалить</button>
        </li>
      </ul>
      <div class="form-actions">
        <button class="btn btn--ghost btn--danger" type="button" :disabled="!!busyId || uploading" @click="clearAll">
          Удалить все
        </button>
      </div>
    </template>
    <p v-else-if="loaded" class="hint">Файлов пока нет — ИИ знает о проекте только бриф и заметки.</p>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { errorText, wsApi } from '../../api/web'
import { askConfirm } from '../../composables/useConfirm'
import { pushToast } from '../../composables/useToasts'
import { readFileBase64 } from '../../utils/adExtras'
import { formatDateTime, num, plural } from '../../utils/format'
import { useWs } from './context'

const { ws } = useWs()

const STATES = {
  full: { cls: 'chip--ok' },
  cut: { cls: 'chip--warning' },
  out: { cls: 'chip--critical' },
  off: { cls: 'chip--info' },
}
const EXTENSIONS = ['.txt', '.md', '.zip']

const items = ref([])
const usage = ref({ limit: 0, chars: 0, sent_chars: 0, tokens: 0, cut: [], out: [] })
const limits = reactive({
  upload_bytes: 5 * 1024 * 1024,
  zip_files: 50,
  prompt_min: 5000,
  prompt_max: 100000,
  prompt_default: 20000,
  chars_per_token: 3,
})
const limitDraft = ref(0)
const savingLimit = ref(false)
const loaded = ref(false)
const error = ref('')
const problems = ref([])
const uploading = ref(false)
const uploadText = ref('')
const busyId = ref(null)
const dragOver = ref(false)
const fileInput = ref(null)

const limitChanged = computed(() => !!limitDraft.value && limitDraft.value !== usage.value.limit)

const fillPct = computed(() =>
  usage.value.limit ? Math.min(100, Math.round((usage.value.sent_chars / usage.value.limit) * 100)) : 0,
)

function mb(bytes) {
  return `${Math.round((bytes / 1024 / 1024) * 10) / 10} МБ`
}

function stateText(f) {
  if (f.state === 'full') return 'целиком'
  if (f.state === 'cut') return `обрезан до ${num(f.sent)}`
  if (f.state === 'out') return 'не поместился'
  return 'выключен'
}

function apply(res) {
  items.value = res.items || []
  if (res.usage) {
    // Несохранённую правку лимита не затираем при загрузке файлов.
    if (!limitDraft.value || limitDraft.value === usage.value.limit) limitDraft.value = res.usage.limit
    usage.value = res.usage
  }
  if (res.limits) Object.assign(limits, res.limits)
}

async function saveLimit() {
  if (!limitChanged.value || savingLimit.value) return
  const value = Math.round(Number(limitDraft.value))
  if (!(value >= limits.prompt_min && value <= limits.prompt_max)) {
    error.value = `Лимит — от ${num(limits.prompt_min)} до ${num(limits.prompt_max)} символов`
    return
  }
  savingLimit.value = true
  error.value = ''
  try {
    limitDraft.value = value
    apply(await wsApi.setContextFilesLimit(ws.value.id, value))
    pushToast(`Лимит файлов для ИИ — ${num(value)} символов`, 'ok')
  } catch (err) {
    error.value = errorText(err)
  } finally {
    savingLimit.value = false
  }
}

async function load() {
  error.value = ''
  try {
    apply(await wsApi.contextFiles(ws.value.id))
  } catch (err) {
    error.value = errorText(err)
  } finally {
    loaded.value = true
  }
}

function onPick(event) {
  const files = [...(event.target.files || [])]
  event.target.value = ''
  upload(files)
}

function onDrop(event) {
  dragOver.value = false
  upload([...(event.dataTransfer?.files || [])])
}

async function upload(files) {
  if (!files.length || uploading.value) return
  uploading.value = true
  error.value = ''
  problems.value = []
  let added = 0
  let replaced = 0
  try {
    for (const [i, file] of files.entries()) {
      const lower = file.name.toLowerCase()
      if (!EXTENSIONS.some((ext) => lower.endsWith(ext))) {
        problems.value.push({ name: file.name, reason: 'подходят только .txt, .md и .zip' })
        continue
      }
      if (file.size > limits.upload_bytes) {
        problems.value.push({ name: file.name, reason: `больше ${mb(limits.upload_bytes)}` })
        continue
      }
      uploadText.value = files.length > 1 ? `Загружаем ${i + 1} из ${files.length}…` : 'Загружаем…'
      try {
        const data = await readFileBase64(file)
        const res = await wsApi.uploadContextFile(ws.value.id, { filename: file.name, data })
        apply(res)
        added += res.added.length
        replaced += res.replaced.length
        const prefix = files.length > 1 ? `${file.name}: ` : ''
        problems.value.push(...res.skipped.map((s) => ({ name: prefix + s.name, reason: s.reason })))
      } catch (err) {
        problems.value.push({ name: file.name, reason: errorText(err) })
      }
    }
  } finally {
    uploading.value = false
    uploadText.value = ''
  }
  if (added || replaced) {
    const parts = []
    if (added) parts.push(`добавлено ${added}`)
    if (replaced) parts.push(`заменено ${replaced}`)
    pushToast(`Файлы для ИИ: ${parts.join(', ')}`, 'ok')
  }
}

async function toggle(f, enabled) {
  busyId.value = f.id
  error.value = ''
  try {
    apply(await wsApi.setContextFile(ws.value.id, f.id, enabled))
  } catch (err) {
    error.value = errorText(err)
    await load()
  } finally {
    busyId.value = null
  }
}

async function remove(f) {
  const ok = await askConfirm(`Удалить «${f.filename}»? ИИ перестанет его учитывать.`, {
    confirmText: 'Удалить',
    danger: true,
  })
  if (!ok) return
  busyId.value = f.id
  error.value = ''
  try {
    apply(await wsApi.deleteContextFile(ws.value.id, f.id))
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busyId.value = null
  }
}

async function clearAll() {
  const ok = await askConfirm(`Удалить все файлы для ИИ (${items.value.length})?`, {
    confirmText: 'Удалить все',
    danger: true,
  })
  if (!ok) return
  error.value = ''
  problems.value = []
  try {
    apply(await wsApi.clearContextFiles(ws.value.id))
  } catch (err) {
    error.value = errorText(err)
  }
}

onMounted(load)
watch(
  () => ws.value?.id,
  (next, prev) => {
    if (next && next !== prev) {
      items.value = []
      problems.value = []
      limitDraft.value = 0
      loaded.value = false
      load()
    }
  },
)
</script>

<style scoped>
.ctx-input {
  display: none;
}
.ctx-drop {
  margin: 4px 0 12px;
  padding: 14px 16px;
  border: 1.5px dashed var(--line-2);
  border-radius: var(--r-sm);
  color: var(--muted);
  font-size: 0.9rem;
  text-align: center;
  transition: border-color 0.15s, background 0.15s;
}
.ctx-drop.is-over {
  border-color: var(--accent);
  background: var(--accent-soft);
}
.ctx-problems {
  margin: 4px 0 0;
  padding-left: 18px;
}
.ctx-usage {
  margin: 8px 0 12px;
}
.ctx-usage__text {
  margin: 0 0 6px;
}
.ctx-usage__warn {
  margin-top: 8px;
}
.ctx-limit {
  margin: 10px 0 0;
}
.ctx-limit__row {
  display: flex;
  gap: 8px;
  align-items: center;
}
.ctx-limit__row input {
  width: 160px;
}
.ctx-files {
  margin: 0;
  padding: 0;
  list-style: none;
}
.ctx-files__item {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 6px 0;
  border-bottom: 1px solid var(--line);
}
.ctx-files__item.is-off .ctx-files__name {
  opacity: 0.6;
}
.ctx-files__main {
  flex: 1;
  min-width: 0;
}
.ctx-files__name {
  min-width: 0;
  overflow-wrap: anywhere;
}
.ctx-files__name small {
  display: block;
}
@media (max-width: 640px) {
  .ctx-files__item {
    flex-wrap: wrap;
  }
  .ctx-files__main {
    flex-basis: 100%;
  }
}
</style>
