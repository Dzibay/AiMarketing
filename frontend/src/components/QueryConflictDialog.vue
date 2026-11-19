<template>
  <Modal :open="!!row" title="Минус-фраза задевает ключи" wide :locked="busy" @close="close">
    <template v-if="row">
      <p v-if="loading" class="hint">Ищем, как обойти…</p>
      <UiNotice v-else-if="loadError" tone="error">{{ loadError }}</UiNotice>
      <template v-else-if="data">
        <p class="qcd__lead">
          Запрос <b>«{{ data.query }}»</b>. Минус-фраза <b>«{{ data.phrase }}»</b> {{ LEVELS[data.level] }}
          <template v-if="data.conflicts_total">заденет {{ keywordsText(data.conflicts, data.conflicts_total) }}.</template>
          <template v-else>уже не задевает ключи — её можно добавить как обычно.</template>
        </p>
        <template v-if="data.conflicts_total">
          <p v-if="!data.options.length" class="hint">
            Готового выхода нет. Поправьте минус-фразу вручную или добавьте её всё равно.
          </p>
          <div v-else class="qcd__opts" role="group" aria-label="Как обойти пересечение">
            <button
              v-for="o in data.options"
              :key="o.key"
              type="button"
              class="qcd__opt"
              :class="{ 'is-on': picked === o.key }"
              :aria-pressed="picked === o.key"
              :disabled="busy"
              @click="picked = o.key"
            >
              <strong>{{ titleOf(o) }}</strong>
              <template v-if="o.kind === 'groups'">
                <span>
                  «−{{ o.phrase }}» уйдёт в {{ o.groups.length === 1 ? 'группу' : 'группы' }}
                  {{ o.groups.map((g) => `«${g.name}»`).join(', ') }}.
                </span>
                <span v-for="g in o.left_out" :key="g.id">
                  В группе «{{ g.name }}» запрос ещё будет показываться — там ключ
                  {{ g.keywords.map((k) => `«${k}»`).join(', ') }}.
                </span>
              </template>
              <span v-else-if="o.kind === 'narrow'">
                В минус уйдёт «{{ o.phrase }}» {{ LEVELS[o.level] }} — ключи не задевает, этот запрос отсечёт.
              </span>
              <template v-else-if="o.kind === 'keyword'">
                <span v-for="k in o.keywords" :key="k.keyword_row_id">
                  «{{ k.keyword }}» → <b>{{ k.new_keyword }}</b>
                  <template v-if="k.adgroup_name"> в группе «{{ k.adgroup_name }}»</template>
                </span>
                <span>Минус-фраза не нужна: по ключу будут приходить только запросы из тех же слов.</span>
                <span v-if="o.lost.total" class="tone-warn">
                  Перестанут приходить ещё {{ o.lost.total }} {{ plural(o.lost.total, 'запрос', 'запроса', 'запросов') }},
                  например {{ o.lost.examples.map((q) => `«${q}»`).join(', ') }}<template v-if="o.lost.with_conversions"
                    >; с заявками — {{ o.lost.with_conversions }}</template
                  >.
                </span>
                <span class="qcd__note">Это правка ключа в Директе — спросим отдельно.</span>
              </template>
              <span v-if="o.targets_total" class="tone-warn">
                Отсечёт и нужные запросы: {{ o.targets_hit.map((t) => `«${t.query}»`).join(', ')
                }}<template v-if="o.targets_total > o.targets_hit.length"> и ещё {{ o.targets_total - o.targets_hit.length }}</template>.
              </span>
            </button>
          </div>
          <p class="hint">Действие появится в «Истории» — его можно отменить.</p>
        </template>
      </template>
      <p v-if="error" class="form-error">{{ error }}</p>
    </template>
    <template #footer>
      <button class="btn btn--ghost" type="button" :disabled="busy" @click="close">Отмена</button>
      <button v-if="chosen" class="btn btn--accent" type="button" :disabled="busy" @click="submit">
        {{ busy ? 'Применяем…' : chosen.kind === 'keyword' ? 'Изменить ключ…' : 'Добавить в минус' }}
      </button>
    </template>
  </Modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import Modal from './ui/Modal.vue'
import UiNotice from './ui/UiNotice.vue'
import { errorText, wsApi } from '../api/web'
import { askConfirm } from '../composables/useConfirm'
import { pushToast } from '../composables/useToasts'
import { undoAction } from '../composables/useUndo'
import { plural } from '../utils/format'

/**
 * Минус-фраза задевает ключи — готовые выходы одной кнопкой с тем, что получится:
 * только в группы без задетого ключа, фраза плюс слово из запроса или ключ в кавычки (отдельное согласие).
 * Варианты считает сервер той же проверкой, что при применении; применяет — тоже он, с записью в историю.
 */
const props = defineProps({
  /** Запрос: query_norm, query. null — окно закрыто. */
  row: { type: Object, default: null },
  workspaceId: { type: [Number, String], default: null },
  /** Фраза и уровень, которые задели ключи; пусто — как в разметке запроса. */
  phrase: { type: String, default: '' },
  level: { type: String, default: '' },
})
const emit = defineEmits(['close', 'done'])

const LEVELS = { adgroup: 'на группу', campaign: 'на кампанию' }
const data = ref(null)
const loading = ref(false)
const loadError = ref('')
const error = ref('')
const busy = ref(false)
const picked = ref('')
let seq = 0

const chosen = computed(() => (data.value?.options || []).find((o) => o.key === picked.value) || null)

function keywordsText(list, total) {
  const names = (list || []).slice(0, 3).map((k) => `«${k.keyword}»`)
  const more = total > names.length ? ` и ещё ${total - names.length}` : ''
  return `${total === 1 ? 'ключ' : 'ключи'} ${names.join(', ')}${more}`
}

function titleOf(o) {
  if (o.kind === 'groups') return 'Только в группы, где этого ключа нет'
  if (o.kind === 'narrow') return `Уточнить фразу: «${o.phrase}»`
  return o.keywords.length === 1 ? 'Взять ключ в кавычки' : 'Взять ключи в кавычки'
}

watch(
  () => props.row,
  async (row) => {
    data.value = null
    error.value = ''
    loadError.value = ''
    picked.value = ''
    if (!row || !props.workspaceId) return
    const my = ++seq
    loading.value = true
    try {
      const res = await wsApi.queriesResolveOptions(props.workspaceId, {
        query_norm: row.query_norm,
        negative_phrase: props.phrase || null,
        level: props.level || null,
      })
      if (my !== seq) return
      data.value = res
      picked.value = res.options?.[0]?.key || ''
    } catch (err) {
      if (my === seq) loadError.value = errorText(err)
    } finally {
      if (my === seq) loading.value = false
    }
  },
  { immediate: true },
)

function close() {
  if (!busy.value) emit('close')
}

async function submit() {
  const o = chosen.value
  const row = props.row
  if (!o || !row) return
  if (o.kind === 'keyword') {
    const list = o.keywords.map((k) => `«${k.keyword}» → ${k.new_keyword}`).join(', ')
    const lost = o.lost.total
      ? ` Перестанут приходить ещё ${o.lost.total} ${plural(o.lost.total, 'запрос', 'запроса', 'запросов')}.`
      : ''
    const ok = await askConfirm(`Изменим ключевую фразу в Директе: ${list}.${lost} Вернуть можно в «Истории».`, {
      title: o.keywords.length === 1 ? 'Изменить ключ' : 'Изменить ключи',
      confirmText: 'Изменить',
    })
    if (!ok) return
  }
  busy.value = true
  error.value = ''
  try {
    const res = await wsApi.queriesResolve(props.workspaceId, {
      query_norm: row.query_norm,
      negative_phrase: props.phrase || null,
      level: props.level || null,
      option: o.key,
    })
    if (res.status !== 'ok' && res.status !== 'partial') {
      error.value = res.message || 'Не получилось — попробуйте ещё раз.'
      return
    }
    const text =
      res.kind === 'keyword'
        ? `Ключ в кавычках — «${row.query}» больше не придёт`
        : `«${res.phrase}» — в минус-фразах ${res.level === 'campaign' ? 'кампании' : 'групп'}`
    pushToast(res.message ? `${text}. ${res.message}` : text, res.status === 'ok' ? 'ok' : 'warn', undoAction(res, () => emit('done', { row, undone: true })))
    emit('done', { row, res })
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.qcd__lead {
  margin: 0 0 14px;
  color: var(--muted);
  overflow-wrap: anywhere;
}
.qcd__lead b {
  color: var(--ink);
}
.qcd__opts {
  display: grid;
  gap: 8px;
  margin-bottom: 12px;
}
.qcd__opt {
  display: grid;
  gap: 4px;
  width: 100%;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface);
  color: var(--muted);
  font: inherit;
  text-align: left;
  overflow-wrap: anywhere;
  cursor: pointer;
}
.qcd__opt strong,
.qcd__opt b {
  color: var(--ink);
}
.qcd__opt:hover:not(:disabled) {
  border-color: var(--accent-ring);
}
.qcd__opt.is-on {
  border-color: var(--ink);
  box-shadow: inset 0 0 0 1px var(--ink);
  background: var(--lime-soft);
}
.qcd__note {
  font-size: 0.9em;
}
</style>
