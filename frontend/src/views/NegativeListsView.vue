<template>
  <AppShell>
        <div class="cabinet__head">
          <div>
            <h1>Минус-фразы</h1>
            <p>
              Общие списки стоп-слов для кампаний разных клиентов. В Директ пишем только после
              предпросмотра и подтверждения.
            </p>
          </div>
        </div>

        <div v-if="loading" class="panel"><p class="empty">Загружаем списки…</p></div>

        <template v-else>
          <section class="panel">
            <div class="panel__head">
              <h2>Списки</h2>
              <div class="row-actions">
                <select v-model="templateKey" aria-label="Создать список" @change="startDraft">
                  <option value="">+ Новый список…</option>
                  <option value="blank">Пустой</option>
                  <option v-for="t in templates" :key="t.key" :value="t.key">
                    Шаблон: {{ t.name }} ({{ t.phrases.length }})
                  </option>
                </select>
              </div>
            </div>
            <div v-if="lists.length" class="list">
              <div
                v-for="l in lists"
                :key="l.id"
                class="list-row neg-list-row"
                :class="{ 'is-selected': l.id === selectedId }"
                role="button"
                tabindex="0"
                @click="selectList(l.id)"
                @keydown.enter="selectList(l.id)"
              >
                <div>
                  <strong>{{ l.name }}</strong>
                  <small>
                    {{ l.phrases_count }} {{ plural(l.phrases_count, 'фраза', 'фразы', 'фраз') }} ·
                    {{ l.linked ? `в ${l.linked} ${plural(l.linked, 'кампании', 'кампаниях', 'кампаниях')}` : 'не применён' }}
                  </small>
                </div>
                <span v-if="l.stale" class="chip chip--warning">Ждут обновления: {{ l.stale }}</span>
              </div>
            </div>
            <p v-else class="empty">
              Списков пока нет. Начните с шаблона — например, «Работа и вакансии» — и поправьте под клиентов.
            </p>
          </section>

          <section v-if="editing" class="panel">
            <h2>{{ selectedId ? 'Список' : 'Новый список' }}</h2>
            <form class="filters" @submit.prevent="save">
              <div class="field field--wide">
                <label for="neg-name">Название</label>
                <input id="neg-name" v-model="form.name" type="text" maxlength="255" placeholder="Общие стоп-слова" />
              </div>
              <div class="field neg-phrases">
                <label for="neg-phrases">Минус-фразы — по одной в строке</label>
                <textarea
                  id="neg-phrases"
                  v-model="form.text"
                  rows="10"
                  placeholder="бесплатно&#10;скачать&#10;своими руками"
                />
                <small class="cell-note">
                  {{ draftPhrases.length }} {{ plural(draftPhrases.length, 'фраза', 'фразы', 'фраз') }} ·
                  {{ draftChars.toLocaleString('ru-RU') }} из 20 000 символов. Можно вставить строку из
                  Директа вида «-бесплатно -скачать».
                </small>
              </div>
              <button class="btn btn--accent" type="submit" :disabled="busy || !dirty">
                {{ selectedId ? 'Сохранить' : 'Создать' }}
              </button>
              <button v-if="dirty" class="btn btn--ghost" type="button" :disabled="busy" @click="resetForm">
                {{ selectedId ? 'Отменить правки' : 'Отмена' }}
              </button>
              <button
                v-if="selectedId"
                class="btn btn--ghost"
                type="button"
                :disabled="busy || detail?.linked > 0"
                :title="detail?.linked ? 'Сначала снимите список со всех кампаний' : ''"
                @click="remove"
              >
                Удалить
              </button>
            </form>
            <p v-if="selectedId && detail?.stale" class="hint tone-warn">
              Список менялся после применения: {{ detail.stale }}
              {{ plural(detail.stale, 'кампания ждёт', 'кампании ждут', 'кампаний ждут') }} обновления.
              Откройте предпросмотр и примените заново.
            </p>
          </section>

          <section v-if="selectedId && detail" class="panel">
            <div class="panel__head">
              <div>
                <h2>Кампании</h2>
                <p class="hint">
                  Отметьте кампании, где должен работать список. Снятая галочка у кампании со списком —
                  отвязка: из неё уйдут только фразы, которые добавил этот список.
                </p>
              </div>
              <div class="filters">
                <div class="field">
                  <label for="neg-search">Поиск</label>
                  <input id="neg-search" v-model="search" type="search" placeholder="Клиент или кампания" />
                </div>
              </div>
            </div>

            <div v-if="groups.length" class="neg-groups">
              <div v-for="g in groups" :key="g.key" class="neg-group">
                <label class="check-row neg-group__head">
                  <input
                    type="checkbox"
                    :checked="g.allChecked"
                    :indeterminate.prop="g.someChecked && !g.allChecked"
                    :disabled="!g.selectable.length"
                    @change="toggleGroup(g, $event.target.checked)"
                  />
                  <strong>{{ g.name }}</strong>
                  <small>{{ g.checkedCount }} из {{ g.items.length }}</small>
                </label>
                <label
                  v-for="t in g.items"
                  :key="t.id"
                  class="check-row neg-target"
                  :class="{ 'is-off': isDisabled(t) }"
                >
                  <input
                    type="checkbox"
                    :checked="checked.has(t.id)"
                    :disabled="isDisabled(t)"
                    @change="toggle(t.id, $event.target.checked)"
                  />
                  <span>
                    {{ t.name }}
                    <small class="cell-note">
                      {{ t.client_login }} · {{ t.campaign_id }} · минус-фраз {{ t.negatives }}
                      <template v-if="t.state"> · {{ t.state }}</template>
                    </small>
                  </span>
                  <span v-if="linkOf(t.id)" class="chip" :class="linkOf(t.id).stale ? 'chip--warning' : ''">
                    {{ linkOf(t.id).stale ? 'ждёт обновления' : 'применён' }}
                  </span>
                  <span v-if="t.blocked" class="chip chip--info">{{ blockedLabel(t.blocked) }}</span>
                </label>
              </div>
            </div>
            <p v-else-if="targets.length" class="empty">Ничего не нашлось.</p>
            <p v-else class="empty">
              Кампаний нет. Подключите Яндекс и синхронизируйте кабинеты на странице «Кампании».
            </p>

            <div class="bulk-bar neg-actions">
              <span>
                Отмечено {{ checked.size }}
                <template v-if="selectionDiff.attach || selectionDiff.detach">
                  · привязать {{ selectionDiff.attach }} · отвязать {{ selectionDiff.detach }}
                </template>
              </span>
              <button
                class="btn btn--accent"
                type="button"
                :disabled="previewing || applying || dirty || !canPreview"
                @click="loadPreview"
              >
                {{ previewing ? 'Считаем…' : 'Предпросмотр' }}
              </button>
              <span v-if="dirty" class="sync-status">Сначала сохраните правки списка.</span>
            </div>
          </section>

          <section v-if="preview" class="panel">
            <h2>Предпросмотр</h2>
            <p class="hint">
              Что изменится в Директе для списка «{{ preview.list.name }}». Посчитано по последнему
              синку{{ preview.synced_at ? ` (${formatTime(preview.synced_at)})` : '' }}; перед записью
              минус-фразы перечитываются из Директа, а добавленные вручную фразы не трогаем.
            </p>
            <div class="stats-grid">
              <div class="stat-card">
                <small>Изменится кампаний</small>
                <strong>{{ preview.totals.changed }}</strong>
              </div>
              <div class="stat-card">
                <small>Добавим фраз</small>
                <strong>{{ preview.totals.add }}</strong>
              </div>
              <div class="stat-card">
                <small>Уберём фраз</small>
                <strong>{{ preview.totals.remove }}</strong>
              </div>
              <div class="stat-card">
                <small>Пропустим</small>
                <strong :class="preview.totals.blocked ? 'tone-warn' : ''">{{ preview.totals.blocked }}</strong>
              </div>
            </div>

            <div class="table-scroll">
              <table class="table neg-preview">
                <thead>
                  <tr>
                    <th>Кампания</th>
                    <th>Действие</th>
                    <th>Добавим</th>
                    <th>Уберём</th>
                    <th class="num">Фраз было → станет</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in previewRows" :key="row.campaign_row_id" :class="{ 'is-off': row.blocked }">
                    <td>
                      {{ row.campaign_name }}
                      <span class="cell-note">{{ row.client_name || row.client_login }}</span>
                    </td>
                    <td>
                      <span v-if="row.blocked" class="chip chip--info">{{ blockedLabel(row.blocked) }}</span>
                      <template v-else>{{ ACTION_LABELS[row.action] }}</template>
                    </td>
                    <td :title="row.add.join(', ')">{{ phraseSample(row.add) }}</td>
                    <td :title="row.remove.join(', ')">{{ phraseSample(row.remove) }}</td>
                    <td class="num">
                      {{ row.before }} → {{ row.after }}
                      <span class="cell-note">{{ row.chars.toLocaleString('ru-RU') }} симв.</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-if="unchangedCount" class="hint neg-unchanged">
              Без изменений в Директе: {{ unchangedCount }}
              {{ plural(unchangedCount, 'кампания', 'кампании', 'кампаний') }} — нужные фразы там уже есть.
            </p>

            <div class="bulk-bar neg-actions">
              <button class="btn btn--accent" type="button" :disabled="applying || !canApply" @click="apply">
                {{ applying ? 'Применяем…' : 'Применить в Директ' }}
              </button>
              <button class="btn btn--ghost" type="button" :disabled="applying" @click="preview = null">
                Отмена
              </button>
              <span v-if="applying" class="sync-status">{{ applyText }}</span>
            </div>
          </section>

          <section v-if="result" class="panel">
            <h2>Результат</h2>
            <p :class="result.status === 'ok' ? 'tone-ok' : 'tone-warn'">{{ resultSummary(result) }}</p>
            <ul v-if="result.errors.length" class="neg-errors">
              <li v-for="(e, i) in result.errors" :key="i" class="tone-bad">
                {{ errorWhere(e) }}: {{ e.message || ERRORS[e.error] || e.error }}
              </li>
            </ul>
          </section>
        </template>
  </AppShell>
</template>

<script setup>
import { askConfirm } from '../composables/useConfirm'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import AppShell from '../components/AppShell.vue'
import { ERRORS, errorText, jobProgressText, webApi } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { useHead } from '../utils/useHead'

useHead('Минус-фразы — AiMarketing', true)

const ACTION_LABELS = { attach: 'Привязать', update: 'Обновить', detach: 'Отвязать' }
const BLOCKED_LABELS = {
  disabled: 'аккаунт выключен',
  archived: 'в архиве',
  unsupported: 'медийная',
  'negatives-limit': 'больше 20 000 симв.',
}
const SAMPLE_SIZE = 4
// Те же правила, что parse_phrases на бэкенде: счётчик под полем, а не валидация.
const NOT_COUNTED = /[\s\-!+"[\]]/g
const MINUS_SPLIT = /\s+-(?=\S)/

const loading = ref(true)
const busy = ref(false)
const lists = ref([])
const templates = ref([])
const targets = ref([])
const selectedId = ref(null)
const detail = ref(null)
const drafting = ref(false)
const templateKey = ref('')
const form = reactive({ name: '', text: '' })
const saved = reactive({ name: '', text: '' })
const checked = ref(new Set())
const search = ref('')
const preview = ref(null)
const previewing = ref(false)
const applying = ref(false)
const applyText = ref('')
const result = ref(null)

const editing = computed(() => drafting.value || selectedId.value !== null)
const dirty = computed(() => form.name !== saved.name || form.text !== saved.text)
const draftPhrases = computed(() => parsePhrases(form.text))
const draftChars = computed(() => draftPhrases.value.reduce((sum, p) => sum + p.replace(NOT_COUNTED, '').length, 0))

const links = computed(() => {
  const map = new Map()
  for (const c of detail.value?.campaigns || []) map.set(c.campaign_row_id, c)
  return map
})

const groups = computed(() => {
  const needle = search.value.trim().toLowerCase()
  const byClient = new Map()
  for (const t of targets.value) {
    const clientName = t.client_name || 'Без клиента'
    const hay = `${clientName} ${t.name} ${t.client_login} ${t.campaign_id}`.toLowerCase()
    if (needle && !hay.includes(needle)) continue
    const key = t.client_id == null ? 'none' : String(t.client_id)
    if (!byClient.has(key)) byClient.set(key, { key, name: clientName, items: [] })
    byClient.get(key).items.push(t)
  }
  return [...byClient.values()]
    .sort((a, b) => (a.key === 'none') - (b.key === 'none') || a.name.localeCompare(b.name, 'ru'))
    .map((g) => {
      const items = g.items.sort((a, b) => a.name.localeCompare(b.name, 'ru'))
      const selectable = items.filter((t) => !isDisabled(t))
      const checkedCount = items.filter((t) => checked.value.has(t.id)).length
      return {
        ...g,
        items,
        selectable,
        checkedCount,
        someChecked: checkedCount > 0,
        allChecked: selectable.length > 0 && selectable.every((t) => checked.value.has(t.id)),
      }
    })
})

const selectionDiff = computed(() => {
  let attach = 0
  let detach = 0
  for (const id of checked.value) if (!links.value.has(id)) attach += 1
  for (const id of links.value.keys()) if (!checked.value.has(id)) detach += 1
  return { attach, detach }
})

const canPreview = computed(() => checked.value.size > 0 || links.value.size > 0)
const previewRows = computed(() => (preview.value?.campaigns || []).filter((r) => r.changed || r.blocked))
const unchangedCount = computed(
  () => (preview.value?.campaigns || []).filter((r) => !r.changed && !r.blocked).length,
)
// Кампании без правок тоже применяем: так снимается отметка «ждёт обновления».
const canApply = computed(() => preview.value && preview.value.totals.campaigns > preview.value.totals.blocked)

watch([checked, () => selectedId.value], () => {
  preview.value = null
})

function plural(n, one, few, many) {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return one
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few
  return many
}

function phraseKey(phrase) {
  return phrase.toLowerCase().replaceAll('ё', 'е').split(/\s+/).filter(Boolean).join(' ')
}

function parsePhrases(text) {
  const out = []
  const seen = new Set()
  for (const raw of String(text || '').split(/[\n,;]/)) {
    const chunk = raw.trim()
    for (const part of chunk.startsWith('-') ? chunk.split(MINUS_SPLIT) : [chunk]) {
      const phrase = part.trim().replace(/^-+/, '').split(/\s+/).filter(Boolean).join(' ')
      const key = phraseKey(phrase)
      if (phrase && !seen.has(key)) {
        seen.add(key)
        out.push(phrase)
      }
    }
  }
  return out
}

function isDisabled(t) {
  // Со связанной кампании список можно снять даже в архиве — предпросмотр покажет, что выйдет.
  return Boolean(t.blocked) && !links.value.has(t.id)
}

function linkOf(id) {
  return links.value.get(id)
}

function blockedLabel(reason) {
  return BLOCKED_LABELS[reason] || reason
}

function phraseSample(phrases) {
  if (!phrases.length) return '—'
  const head = phrases.slice(0, SAMPLE_SIZE).join(', ')
  return phrases.length > SAMPLE_SIZE ? `${head} и ещё ${phrases.length - SAMPLE_SIZE}` : head
}

function formatTime(iso) {
  return new Date(iso).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

function errorWhere(e) {
  const target = targets.value.find((t) => t.id === e.campaign_row_id)
  return target ? `${target.name} (${e.client_login})` : e.client_login
}

function resultSummary(r) {
  const parts = [`кампаний обновлено ${r.updated}`, `фраз добавлено ${r.added}`, `убрано ${r.removed}`]
  if (r.skipped.length) parts.push(`пропущено ${r.skipped.length}`)
  if (r.errors.length) parts.push(`ошибок ${r.errors.length}`)
  const head = r.status === 'ok' ? 'Готово' : 'Применено частично'
  return `${head}: ${parts.join(', ')}.`
}

function toggle(id, on) {
  const next = new Set(checked.value)
  if (on) next.add(id)
  else next.delete(id)
  checked.value = next
}

function toggleGroup(g, on) {
  const next = new Set(checked.value)
  for (const t of g.selectable) {
    if (on) next.add(t.id)
    else next.delete(t.id)
  }
  checked.value = next
}

function fillForm(name, phrases) {
  form.name = saved.name = name
  form.text = saved.text = phrases.join('\n')
}

function resetForm() {
  if (selectedId.value && detail.value) {
    fillForm(detail.value.name, detail.value.phrases)
  } else {
    drafting.value = false
    fillForm('', [])
  }
}

function startDraft() {
  const key = templateKey.value
  templateKey.value = ''
  if (!key) return
  const tpl = templates.value.find((t) => t.key === key)
  selectedId.value = null
  detail.value = null
  result.value = null
  drafting.value = true
  fillForm('', [])
  // Шаблон — несохранённый черновик: правим и только потом создаём.
  if (tpl) {
    form.name = tpl.name
    form.text = tpl.phrases.join('\n')
  }
}

async function selectList(id) {
  if (id === selectedId.value) return
  drafting.value = false
  result.value = null
  selectedId.value = id
  await loadDetail()
}

async function loadDetail() {
  const id = selectedId.value
  if (!id) return
  try {
    const data = await webApi.negativeList(id)
    if (selectedId.value !== id) return
    detail.value = data
    fillForm(data.name, data.phrases)
    checked.value = new Set(data.campaigns.map((c) => c.campaign_row_id))
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

async function loadLists() {
  const data = await webApi.negativeLists()
  lists.value = data.items
  templates.value = data.templates
}

async function loadTargets() {
  targets.value = (await webApi.negativeTargets()).items
}

async function save() {
  busy.value = true
  try {
    const payload = { name: form.name, phrases: form.text }
    if (selectedId.value) {
      await webApi.updateNegativeList(selectedId.value, payload)
      pushToast('Список сохранён')
    } else {
      const created = await webApi.createNegativeList(payload)
      drafting.value = false
      selectedId.value = created.id
      pushToast('Список создан — отметьте кампании и откройте предпросмотр')
    }
    await Promise.all([loadLists(), loadDetail()])
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function remove() {
  if (!await askConfirm(`Удалить список «${detail.value.name}»? В Директе ничего не изменится.`)) return
  busy.value = true
  try {
    await webApi.deleteNegativeList(selectedId.value)
    selectedId.value = null
    detail.value = null
    fillForm('', [])
    await loadLists()
    pushToast('Список удалён')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function loadPreview() {
  previewing.value = true
  result.value = null
  try {
    preview.value = await webApi.negativePreview(selectedId.value, [...checked.value])
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    previewing.value = false
  }
}

async function apply() {
  const t = preview.value.totals
  const question = t.changed
    ? `Записать в Директ: ${t.changed} ${plural(t.changed, 'кампания', 'кампании', 'кампаний')}, ` +
      `+${t.add} / −${t.remove} фраз?`
    : 'В Директе ничего не изменится — только обновим привязки списка. Продолжить?'
  if (!await askConfirm(question)) return
  applying.value = true
  applyText.value = 'Ставим задачу…'
  try {
    const out = await webApi.negativeApply(selectedId.value, [...checked.value], (job) => {
      applyText.value = `Применяем: ${jobProgressText(job)}`
    })
    result.value = out
    preview.value = null
    pushToast(resultSummary(out), out.status === 'ok' ? 'info' : 'error')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    applying.value = false
    await Promise.all([loadLists(), loadTargets(), loadDetail()]).catch(() => {})
    // loadDetail заново отмечает связанные кампании — предпросмотр сбросился бы и так.
    preview.value = null
  }
}

onMounted(async () => {
  try {
    await Promise.all([loadLists(), loadTargets()])
    if (lists.value.length) await selectList(lists.value[0].id)
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    loading.value = false
  }
})
</script>
