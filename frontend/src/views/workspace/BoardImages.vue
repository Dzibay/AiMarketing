<template>
  <div class="board-lines board-imgs">
    <div class="board-lines__head">
      <span>Картинки</span>
      <small :class="{ 'tone-bad': onCount > MAX }">{{ onCount }} из {{ MAX }}</small>
    </div>
    <ul v-if="items.length" class="board-imgs__list">
      <li
        v-for="i in items"
        :key="i.hash"
        class="board-imgs__item"
        :class="{ 'is-pending': i.pending, 'is-off': !board.isOn(i), 'is-own': i.own }"
        :title="[i.text, i.note].filter(Boolean).join(' · ')"
      >
        <img v-if="i.preview_url" :src="i.preview_url" :alt="i.text" loading="lazy" />
        <span v-else class="board-imgs__none">нет превью</span>
        <span class="board-imgs__name">{{ i.text }}</span>
        <input
          v-if="i.pending"
          type="checkbox"
          :checked="board.isOn(i)"
          :disabled="board.disabled.value"
          :aria-label="i.text"
          @change="board.toggle(i)"
        />
        <button
          class="board-x"
          type="button"
          :title="i.pending && !i.added ? 'Снять галочку' : 'Убрать'"
          :aria-label="`Убрать картинку: ${i.text}`"
          :disabled="board.disabled.value"
          @click="board.remove('image', group, [i])"
        >
          ×
        </button>
      </li>
    </ul>
    <p v-else class="hint">Без картинок объявление покажется только текстом. ИИ предложит их на этапе «Картинки» — или добавьте сами.</p>

    <div class="board-imgs__tools">
      <select
        v-if="!account && images.accounts.value.length > 1"
        class="board-select"
        aria-label="Кабинет для картинок"
        @change="images.chooseAccount(Number($event.target.value) || null)"
      >
        <option value="">Кабинет…</option>
        <option v-for="a in images.accounts.value" :key="a.id" :value="a.id">{{ a.name || a.client_login }}</option>
      </select>
      <label class="btn btn--xs" :class="{ 'is-disabled': !canAdd || !account }" :title="account ? '' : 'Сначала выберите кабинет'">
        Загрузить
        <input
          type="file"
          accept="image/png,image/jpeg,image/gif"
          multiple
          hidden
          :disabled="!canAdd || !account"
          @change="upload"
        />
      </label>
      <button
        class="btn btn--xs"
        type="button"
        :class="{ active: mode === 'library' }"
        :aria-pressed="mode === 'library'"
        :disabled="!canAdd"
        @click="open('library')"
      >
        Из кампаний
      </button>
      <button
        class="btn btn--xs"
        type="button"
        :class="{ active: mode === 'generate' }"
        :aria-pressed="mode === 'generate'"
        :disabled="!canAdd || !images.canGenerate.value"
        :title="images.canGenerate.value ? '' : 'Генерация картинок не подключена'"
        @click="open('generate')"
      >
        Сгенерировать
      </button>
      <RouterLink v-if="!images.canGenerate.value" class="ai-check__link" to="/cabinet/settings#image-gen">
        подключить генерацию
      </RouterLink>
      <span v-if="busy" class="hint"><span class="spinner" /> {{ busy }}</span>
    </div>

    <div v-if="mode === 'library'" class="board-imgs__panel">
      <p v-if="library === null" class="hint"><span class="spinner" /> Ищу картинки в кампаниях пространства…</p>
      <p v-else-if="!library.length" class="hint">В объявлениях кампаний пространства картинок нет.</p>
      <template v-else>
        <div class="board-imgs__grid">
          <button
            v-for="l in library"
            :key="`${l.account_id}:${l.hash}`"
            type="button"
            class="board-imgs__choice"
            :class="{ 'is-on': picked.includes(l) }"
            :aria-pressed="picked.includes(l)"
            :disabled="hashes.has(l.hash)"
            :title="[(l.campaigns || []).join(', '), `в ${l.ads} ${plural(l.ads, 'объявлении', 'объявлениях', 'объявлениях')}`].filter(Boolean).join(' · ')"
            @click="pick(l)"
          >
            <img :src="l.preview_url" alt="" loading="lazy" />
            <small>{{ hashes.has(l.hash) ? 'уже в группе' : (l.campaigns || [])[0] || 'кампания' }}</small>
          </button>
        </div>
        <div class="board-imgs__tools">
          <button class="btn btn--accent btn--xs" type="button" :disabled="!picked.length || !canAdd" @click="addPicked">
            Добавить{{ picked.length ? ` ${picked.length}` : '' }}
          </button>
          <button class="ai-check__link" type="button" @click="mode = ''">Закрыть</button>
          <small class="hint">Картинку из другого кабинета скопируем при создании кампании.</small>
        </div>
      </template>
    </div>

    <form v-if="mode === 'generate'" class="board-imgs__panel" @submit.prevent="generate">
      <textarea
        v-model="prompt"
        class="ai-input ai-input--area"
        rows="2"
        maxlength="1000"
        placeholder="Что нарисовать: товар, сцена, настроение. Без текста на картинке — Директ такие не любит"
        aria-label="Описание картинки"
      />
      <div class="board-imgs__tools">
        <select v-model.number="count" class="board-select" aria-label="Сколько картинок">
          <option v-for="n in countOptions" :key="n" :value="n">{{ n }} {{ plural(n, 'картинка', 'картинки', 'картинок') }}</option>
        </select>
        <button class="btn btn--accent btn--xs" type="submit" :disabled="!prompt.trim() || !canAdd || !account || !!busy">
          Сгенерировать
        </button>
        <button class="ai-check__link" type="button" @click="mode = ''">Закрыть</button>
        <small class="hint">Платно — по тарифу вашего API.</small>
      </div>
    </form>

    <p v-if="error" class="hint tone-bad">{{ error }}</p>
  </div>
</template>

<script setup>
/**
 * Картинки группы на доске: превью с галочками ждущей таблицы и «убрать»; добавить — загрузить свои,
 * взять из объявлений других кампаний пространства или сгенерировать по описанию (если API подключён).
 * Загруженное и сгенерированное ложится в библиотеку кабинета, на доску — правкой по хэшу.
 */
import { computed, inject, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { aiApi, errorText, webApi } from '../../api/web'
import { plural } from '../../utils/format'
import { IMAGE_MAX_BYTES, readFileBase64 } from '../../utils/adExtras'

const props = defineProps({
  items: { type: Array, required: true },
  group: { type: String, required: true },
})
const board = inject('board')
const images = board.images

const MAX = 5
const GENERATE_MAX = 4

const mode = ref('')
const busy = ref('')
const error = ref('')
const library = ref(null)
const picked = ref([])
const prompt = ref('')
const count = ref(1)

const account = computed(() => images.account.value)
const onCount = computed(() => props.items.filter(board.isOn).length)
const free = computed(() => Math.max(0, MAX - onCount.value))
const canAdd = computed(() => free.value > 0 && !board.disabled.value && !busy.value)
const hashes = computed(() => new Set(props.items.map((i) => i.hash)))
const countOptions = computed(() => Array.from({ length: Math.max(1, Math.min(GENERATE_MAX, free.value)) }, (_, i) => i + 1))

async function open(next) {
  error.value = ''
  mode.value = mode.value === next ? '' : next
  if (mode.value === 'generate' && count.value > countOptions.value.length) count.value = countOptions.value.length
  if (mode.value !== 'library' || library.value !== null) return
  try {
    library.value = await images.library()
  } catch (err) {
    library.value = []
    error.value = errorText(err)
  }
}

function pick(item) {
  if (picked.value.includes(item)) picked.value = picked.value.filter((p) => p !== item)
  else if (picked.value.length < free.value) picked.value = [...picked.value, item]
  else error.value = `В группе до ${MAX} картинок — свободно ${free.value}`
}

/** На доску — только новые хэши (одинаковые файлы дают один хэш) и не больше свободных мест; остальное объясняем. */
async function add(list) {
  const seen = new Set(hashes.value)
  const fresh = []
  for (const op of list) {
    if (seen.has(op.text) || fresh.length >= free.value) continue
    seen.add(op.text)
    fresh.push(op)
  }
  if (!fresh.length) {
    if (list.length) error.value = 'Эти картинки в группе уже есть'
    return false
  }
  const ok = await board.edit(fresh.map((op) => ({ op: 'add', kind: 'image', group: props.group, ...op })))
  if (ok && fresh.length < list.length) error.value = `Добавлено ${fresh.length} из ${list.length}: в группе до ${MAX} картинок, повторы пропущены`
  return ok
}

async function addPicked() {
  error.value = ''
  const ops = picked.value.map((l) => ({
    text: l.hash,
    account_id: l.account_id,
    name: `Из «${(l.campaigns || [])[0] || 'кампании'}»`.slice(0, 120),
  }))
  if (await add(ops)) {
    picked.value = []
    mode.value = ''
  }
}

async function upload(event) {
  const files = [...(event.target.files || [])]
  event.target.value = ''
  error.value = ''
  if (!files.length || !account.value) return
  const room = files.slice(0, free.value)
  const problems = []
  if (room.length < files.length) problems.push(`в группе до ${MAX} картинок — взяли ${room.length}`)
  const ops = []
  busy.value = 'Загружаю в кабинет…'
  try {
    for (const file of room) {
      if (file.size > IMAGE_MAX_BYTES) {
        problems.push(`«${file.name}» больше 5 МБ`)
        continue
      }
      try {
        const res = await webApi.uploadAdImage(account.value, { name: file.name.slice(0, 255), data: await readFileBase64(file) })
        ops.push({ text: res.hash, account_id: account.value, name: file.name.replace(/\.[^.]+$/, '').slice(0, 120) || 'Картинка' })
      } catch (err) {
        problems.push(`«${file.name}»: ${errorText(err)}`)
      }
    }
  } finally {
    busy.value = ''
  }
  if (ops.length) await add(ops)
  if (problems.length) error.value = problems.join('; ')
}

async function generate() {
  const text = prompt.value.trim()
  if (!text || !account.value) return
  error.value = ''
  busy.value = 'Рисую — это до минуты…'
  let res
  try {
    res = await aiApi.generateImages(images.wsId(), { account_id: account.value, prompt: text, count: count.value })
  } catch (err) {
    error.value = errorText(err)
    return
  } finally {
    busy.value = ''
  }
  const name = `ИИ · ${text}`.slice(0, 120)
  const ok = await add((res.images || []).map((i) => ({ text: i.hash, account_id: i.account_id, name })))
  if (res.errors?.length) error.value = `Не все получились: ${res.errors.join('; ')}`
  if (ok) {
    prompt.value = ''
    mode.value = ''
  }
}
</script>
