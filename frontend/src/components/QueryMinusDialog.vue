<template>
  <Modal :open="!!row" :title="pick ? 'Минус-фраза' : 'В минус'" :locked="busy" @close="close">
    <template v-if="row">
      <p class="qmd__lead">
        Запрос <b>«{{ row.query }}»</b>
        <template v-if="row.cost_rub"> · {{ rubles(row.cost_rub) }}<template v-if="!row.conversions"> без заявок</template></template>
      </p>
      <QueryWords v-model="phrase" :query="row.query" :disabled="busy" />
      <div v-field class="field qmd__field">
        <label>Минус-фраза</label>
        <input v-model="phrase" type="text" maxlength="4096" placeholder="весь запрос целиком" :disabled="busy" />
        <small class="field-hint">В минус уйдёт: «{{ effective }}»</small>
      </div>
      <div v-if="expert" v-field class="field qmd__field">
        <label>Куда добавить</label>
        <UiSelect v-model="level" :options="levelOptions" :disabled="busy" />
      </div>
      <p v-if="problems.length" class="field-hint tone-bad">{{ problems.join('; ') }}</p>
      <p v-if="warning" class="field-hint tone-warn">{{ warning }}</p>
      <p v-if="error" class="form-error">{{ error }}</p>
      <p v-if="!pick" class="field-hint">Действие появится в «Истории» — его можно отменить.</p>
    </template>
    <template #footer>
      <button class="btn btn--ghost" type="button" :disabled="busy" @click="close">Отмена</button>
      <button class="btn btn--accent" type="button" :disabled="busy || !!problems.length" @click="submit">
        {{ pick ? 'Готово' : busy ? 'Добавляем…' : 'Добавить в минус' }}
      </button>
    </template>
  </Modal>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import Modal from './ui/Modal.vue'
import UiSelect from './ui/UiSelect.vue'
import QueryWords from './QueryWords.vue'
import { errorText, wsApi } from '../api/web'
import { askConfirm } from '../composables/useConfirm'
import { useExpert } from '../composables/useExpert'
import { pushToast } from '../composables/useToasts'
import { undoAction } from '../composables/useUndo'
import { rubles } from '../utils/format'

/**
 * Один запрос в минус: слова-«таблетки», поле фразы и та же проверка, что на экране чистки, —
 * формат фразы, ваши ключи, отметка «целевой» и другие нужные запросы (целевые или с заявками), которые фраза отсечёт.
 * Если что-то задето — спрашиваем явно, как в структуре кампании.
 * pick — только выбрать фразу (решение остаётся на экране до «Минусовать»), без записи в Директ.
 */
const props = defineProps({
  /** Строка запроса как в /queries: query, query_norm, label, adgroups, cost_rub, conversions. null — окно закрыто. */
  row: { type: Object, default: null },
  workspaceId: { type: [Number, String], default: null },
  /** Начальная фраза; null — фраза ИИ, если он её предложил, иначе пусто. */
  initialPhrase: { type: String, default: null },
  initialLevel: { type: String, default: '' },
  pick: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'done', 'pick'])
const { expert } = useExpert()

const phrase = ref('')
const level = ref('adgroup')
const busy = ref(false)
const error = ref('')
const check = ref(null)

const hasGroup = computed(() => !!props.row?.adgroups?.length)
const levelOptions = computed(() => [
  { value: 'adgroup', label: 'на группу', disabled: !hasGroup.value },
  { value: 'campaign', label: 'на кампанию' },
])
const effective = computed(() => phrase.value.trim() || props.row?.query || '')
const checkKey = computed(() => `${level.value}|${phrase.value.trim()}`)
const fresh = computed(() => (check.value?.key === checkKey.value ? check.value : null))
const problems = computed(() => fresh.value?.problems || [])

// «Вы отметили запрос целевым» — как TARGET_NOTE на сервере; предложение ИИ — мягче.
const targetNote = computed(() => {
  const l = props.row?.label
  if (l?.verdict !== 'target') return ''
  return l.confirmed ? 'вы отметили запрос целевым' : 'ИИ считает запрос целевым'
})

function conflictNote(c) {
  const list = (c.conflicts || []).slice(0, 3).map((k) => `«${k.keyword}»`)
  const more = c.total > list.length ? ` и ещё ${c.total - list.length}` : ''
  return `${c.total === 1 ? 'заденет ключ' : 'заденет ключи'} ${list.join(', ')}${more}`
}

// Другие запросы проекта, которые фраза тоже отсечёт: вы отметили их целевыми или по ним были заявки.
function keptNote(c) {
  const list = (c.targets || []).slice(0, 3)
  if (c.targetsTotal === 1 && list.length)
    return `отсечёт запрос «${list[0].query}» — ${list[0].target ? 'вы отметили его целевым' : 'по нему были заявки'}`
  const more = c.targetsTotal > list.length ? ` и ещё ${c.targetsTotal - list.length}` : ''
  return `отсечёт запросы, которые вы отметили целевыми или по которым были заявки: ${list.map((k) => `«${k.query}»`).join(', ')}${more}`
}

function upperFirst(text) {
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** Причины подтвердить одной фразой, как у add_query_as_negative: «…целевым, и минус-фраза заденет ключ …». */
function reasonText(c) {
  const parts = []
  if (targetNote.value) parts.push(targetNote.value)
  if (c?.total) parts.push(parts.length ? `минус-фраза ${conflictNote(c)}` : conflictNote(c))
  if (c?.targetsTotal) parts.push(c.total ? keptNote(c) : `минус-фраза ${keptNote(c)}`)
  return parts.length ? upperFirst(parts.join(', и ')) : ''
}

const warning = computed(() => {
  const text = reasonText(fresh.value)
  return text ? `${text}.` : ''
})

watch(
  () => props.row,
  (row) => {
    error.value = ''
    check.value = null
    if (!row) return
    const l = row.label
    const ai = l?.verdict === 'negative' ? l.negative_phrase || '' : ''
    phrase.value = props.initialPhrase ?? ai
    const want = props.initialLevel || (l?.verdict === 'negative' && l.level) || 'adgroup'
    level.value = hasGroup.value ? want : 'campaign'
    scheduleCheck()
  },
  { immediate: true },
)

let timer = null
let seq = 0

function scheduleCheck() {
  clearTimeout(timer)
  timer = setTimeout(runCheck, 300)
}

async function runCheck() {
  const row = props.row
  const id = props.workspaceId
  if (!row || !id) return null
  const key = checkKey.value
  const my = ++seq
  try {
    const res = await wsApi.queriesCheck(id, [
      { query_norm: row.query_norm, negative_phrase: phrase.value.trim(), level: level.value },
    ])
    const it = (res.items || [])[0] || {}
    const out = {
      key,
      problems: it.problems || [],
      conflicts: it.conflicts || [],
      total: it.conflicts_total || 0,
      targets: it.targets_hit || [],
      targetsTotal: it.targets_total || 0,
    }
    if (my === seq) check.value = out
    return out
  } catch {
    // Подсказка не обязательна: при записи фраза всё равно проверяется на сервере.
    return null
  }
}

watch([phrase, level], () => {
  error.value = ''
  if (props.row) scheduleCheck()
})

function close() {
  if (!busy.value) emit('close')
}

async function submit() {
  const row = props.row
  if (!row) return
  if (props.pick) {
    emit('pick', { phrase: phrase.value.trim(), level: level.value })
    return
  }
  busy.value = true
  error.value = ''
  try {
    clearTimeout(timer)
    const c = fresh.value || (await runCheck())
    if (c?.problems.length) {
      error.value = c.problems.join('; ')
      return
    }
    const reason = reasonText(c)
    let force = false
    if (reason) {
      // Те же слова и кнопки, что у «В минус» в структуре кампании: добавляем только после явного «да».
      const ok = await askConfirm(`${reason}. Всё равно добавить «${effective.value}» в минус-фразы?`, {
        title: c?.total
          ? 'Минус-фраза заденет ваши ключи'
          : c?.targetsTotal
            ? 'Минус-фраза отсечёт нужные запросы'
            : 'Запрос отмечен целевым',
        confirmText: 'Всё равно добавить',
        danger: true,
      })
      if (!ok) return
      force = !!c?.total
    }
    const res = await wsApi.queriesApply(props.workspaceId, {
      items: [
        {
          query_norm: row.query_norm,
          verdict: 'negative',
          negative_phrase: phrase.value.trim(),
          level: level.value,
          add_keyword: false,
          adgroup_row_id: null,
        },
      ],
      force,
    })
    const it = (res.items || []).find((i) => i.query_norm === row.query_norm) || {}
    if (it.status !== 'ok' && it.status !== 'partial') {
      error.value = it.message || 'Не получилось добавить минус-фразу.'
      return
    }
    const where = (it.level || level.value) === 'campaign' ? 'кампании' : 'группы'
    pushToast(`«${it.phrase || effective.value}» — в минус-фразах ${where}`, 'ok', undoAction(res, () => emit('done', { row, undone: true })))
    emit('done', { row, res })
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}

onBeforeUnmount(() => clearTimeout(timer))
</script>

<style scoped>
.qmd__lead {
  margin: 0 0 12px;
  color: var(--muted);
  overflow-wrap: anywhere;
}
.qmd__lead b {
  color: var(--ink);
}
.qmd__field {
  margin-top: 14px;
}
.qmd__field .ui-select {
  align-self: flex-start;
}
</style>
