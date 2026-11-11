<template>
  <div class="ws-toolbar">
    <p class="sync-status">
      Общие списки минус-фраз для кампаний разных клиентов. В Директ пишем только после предпросмотра и вашего
      подтверждения.
    </p>
    <details ref="newMenu" class="bulk-more neg-new">
      <summary class="btn btn--accent"><Plus :size="15" aria-hidden="true" />Новый список</summary>
      <div class="bulk-more__menu" role="menu">
        <button class="menu-item" type="button" role="menuitem" @click="startDraft('blank')">
          <FilePlus aria-hidden="true" />Пустой список
        </button>
        <button
          v-for="t in templates"
          :key="t.key"
          class="menu-item"
          type="button"
          role="menuitem"
          @click="startDraft(t.key)"
        >
          <ListPlus aria-hidden="true" />{{ t.name }} <small>{{ t.phrases.length }}</small>
        </button>
      </div>
    </details>
  </div>

  <div v-if="loading" class="ccard ccard--note">Загружаем списки…</div>

  <template v-else>
    <h2 class="section-h">Списки <span class="cgroup__n">{{ lists.length }}</span></h2>
    <div v-if="lists.length" class="ccard">
      <button
        v-for="l in lists"
        :key="l.id"
        class="prow neg-row"
        :class="{ 'is-sel': l.id === selectedId }"
        type="button"
        :aria-pressed="l.id === selectedId"
        @click="selectList(l.id)"
      >
        <span class="prow__main">
          <b>{{ l.name }}</b>
          <p>
            {{ l.phrases_count }} {{ plural(l.phrases_count, 'фраза', 'фразы', 'фраз') }} ·
            {{ l.linked ? `в ${l.linked} ${plural(l.linked, 'кампании', 'кампаниях', 'кампаниях')}` : 'ещё не применён' }}
          </p>
        </span>
        <span v-if="l.stale" class="st st--warn">ждут обновления: {{ l.stale }}</span>
        <ChevronRight class="neg-row__go" :size="16" aria-hidden="true" />
      </button>
    </div>
    <div v-else class="ccard ccard--note">
      Списков пока нет. Начните с шаблона — например, «Работа и вакансии» — и поправьте под своих клиентов.
    </div>

    <section v-if="editing" class="ccard neg-edit">
      <h3>{{ selectedId ? 'Список' : 'Новый список' }}</h3>
      <form @submit.prevent="save">
        <div v-field class="field">
          <label>Название</label>
          <input v-model="form.name" type="text" maxlength="255" placeholder="Общие стоп-слова" />
        </div>
        <div v-field class="field">
          <label>Минус-фразы — по одной в строке</label>
          <textarea v-model="form.text" rows="10" placeholder="бесплатно&#10;скачать&#10;своими руками" />
        </div>
        <p class="neg-count">
          {{ draftPhrases.length }} {{ plural(draftPhrases.length, 'фраза', 'фразы', 'фраз') }} ·
          {{ draftChars.toLocaleString('ru-RU') }} из 20 000 символов. Можно вставить строку из Директа вида
          «-бесплатно -скачать».
        </p>
        <p v-if="selectedId && detail?.stale" class="neg-stale">
          Список менялся после применения: {{ detail.stale }}
          {{ plural(detail.stale, 'кампания ждёт', 'кампании ждут', 'кампаний ждут') }} обновления. Откройте
          предпросмотр и примените заново.
        </p>
        <div class="neg-foot">
          <button class="btn btn--accent" type="submit" :disabled="busy || !dirty">
            {{ selectedId ? 'Сохранить' : 'Создать список' }}
          </button>
          <button v-if="dirty" class="btn btn--ghost" type="button" :disabled="busy" @click="resetForm">
            {{ selectedId ? 'Отменить правки' : 'Отмена' }}
          </button>
          <button
            v-if="selectedId"
            class="btn btn--ghost neg-del"
            type="button"
            :disabled="busy || detail?.linked > 0"
            :title="detail?.linked ? 'Сначала снимите список со всех кампаний' : ''"
            @click="remove"
          >
            <Trash2 :size="15" aria-hidden="true" />Удалить
          </button>
        </div>
      </form>
    </section>

    <template v-if="selectedId && detail">
      <div class="neg-camps-h">
        <h2 class="section-h">Где работает список</h2>
        <div class="field neg-search">
          <input v-model="search" type="search" aria-label="Поиск кампании" placeholder="Клиент или кампания" />
        </div>
      </div>
      <p class="neg-lead">
        Отметьте кампании, где должен работать список. Если снять галочку у кампании со списком, из неё уйдут только
        фразы, которые добавил этот список.
      </p>

      <div v-if="groups.length" class="ccard neg-camps">
        <div v-for="g in groups" :key="g.key" class="neg-group">
          <label class="neg-group__h">
            <input
              type="checkbox"
              :checked="g.allChecked"
              :indeterminate.prop="g.someChecked && !g.allChecked"
              :disabled="!g.selectable.length"
              @change="toggleGroup(g, $event.target.checked)"
            />
            <b>{{ g.name }}</b>
            <span class="cgroup__n">{{ g.checkedCount }} из {{ g.items.length }}</span>
          </label>
          <label v-for="t in g.items" :key="t.id" class="neg-target" :class="{ 'is-off': isDisabled(t) }">
            <input
              type="checkbox"
              :checked="checked.has(t.id)"
              :disabled="isDisabled(t)"
              @change="toggle(t.id, $event.target.checked)"
            />
            <span class="neg-target__name">
              {{ t.name }}
              <small>
                {{ t.client_login }} · № {{ t.campaign_id }} · минус-фраз {{ t.negatives }}
                <template v-if="t.state"> · {{ t.state }}</template>
              </small>
            </span>
            <span v-if="linkOf(t.id)" class="st" :class="linkOf(t.id).stale ? 'st--warn' : 'st--ok'">
              {{ linkOf(t.id).stale ? 'ждёт обновления' : 'применён' }}
            </span>
            <span v-if="t.blocked" class="st st--off">{{ blockedLabel(t.blocked) }}</span>
          </label>
        </div>
      </div>
      <div v-else-if="targets.length" class="ccard ccard--note">Ничего не нашлось.</div>
      <div v-else class="ccard ccard--note">
        Кампаний нет. Подключите Яндекс и загрузите кабинеты на странице «Кампании».
      </div>

      <div class="neg-bar">
        <span>
          Отмечено {{ checked.size }}
          <template v-if="selectionDiff.attach || selectionDiff.detach">
            · привязать {{ selectionDiff.attach }} · отвязать {{ selectionDiff.detach }}
          </template>
        </span>
        <span v-if="dirty" class="sync-status">Сначала сохраните правки списка.</span>
        <button
          class="btn btn--accent"
          type="button"
          :disabled="previewing || applying || dirty || !canPreview"
          @click="loadPreview"
        >
          {{ previewing ? 'Считаем…' : 'Показать, что изменится' }}
        </button>
      </div>
    </template>

    <template v-if="preview">
      <h2 class="section-h">Что изменится в Директе</h2>
      <p class="neg-lead">
        Список «{{ preview.list.name }}». Посчитано по последней загрузке{{
          preview.synced_at ? ` (${formatTime(preview.synced_at)})` : ''
        }}; перед записью минус-фразы перечитываются из Директа, а добавленные вручную фразы не трогаем.
      </p>
      <div class="bigstats">
        <div class="bigstat"><span>Изменится кампаний</span><b>{{ preview.totals.changed }}</b></div>
        <div class="bigstat"><span>Добавим фраз</span><b>{{ preview.totals.add }}</b></div>
        <div class="bigstat"><span>Уберём фраз</span><b>{{ preview.totals.remove }}</b></div>
        <div class="bigstat">
          <span>Пропустим</span><b>{{ preview.totals.blocked }}</b>
          <small v-if="preview.totals.blocked" class="is-warn">архив, медийные или лимит</small>
        </div>
      </div>

      <div v-if="previewRows.length" class="ccard table-scroll neg-preview">
        <table class="table">
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
                <span v-if="row.blocked" class="st st--off">{{ blockedLabel(row.blocked) }}</span>
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
      <p v-if="unchangedCount" class="meta-note">
        Без изменений в Директе: {{ unchangedCount }}
        {{ plural(unchangedCount, 'кампания', 'кампании', 'кампаний') }} — нужные фразы там уже есть.
      </p>

      <div class="neg-bar">
        <span v-if="applying" class="sync-status">{{ applyText }}</span>
        <button class="btn btn--ghost" type="button" :disabled="applying" @click="preview = null">Отмена</button>
        <button class="btn btn--accent" type="button" :disabled="applying || !canApply" @click="apply">
          {{ applying ? 'Применяем…' : 'Применить в Директ' }}
        </button>
      </div>
    </template>

    <section v-if="result" class="ccard neg-result" :class="result.status === 'ok' ? 'is-ok' : 'is-warn'">
      <b>{{ resultSummary(result) }}</b>
      <ul v-if="result.errors.length">
        <li v-for="(e, i) in result.errors" :key="i">
          {{ errorWhere(e) }}: {{ e.message || ERRORS[e.error] || e.error }}
        </li>
      </ul>
    </section>
  </template>
</template>

<script setup>
import { askConfirm } from '../composables/useConfirm'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ChevronRight, FilePlus, ListPlus, Plus, Trash2 } from '@lucide/vue'
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
const newMenu = ref(null)
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

function startDraft(key) {
  if (newMenu.value) newMenu.value.open = false
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
    // Итог применения и предпросмотр относились к удалённому списку.
    result.value = null
    preview.value = null
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

<style scoped>
.ws-toolbar .btn,
.neg-foot .btn {
  gap: 6px;
}
.neg-new .bulk-more__menu {
  min-width: 260px;
}
.neg-new .menu-item small {
  margin-left: auto;
  color: var(--faint);
}
.neg-row {
  width: 100%;
  border-right: 0;
  border-bottom: 0;
  border-left: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.neg-row .prow__main {
  flex: 1;
}
.neg-row:hover {
  background: var(--surface-2);
}
.neg-row.is-sel {
  background: color-mix(in srgb, var(--lime) 14%, transparent);
}
.neg-row__go {
  flex-shrink: 0;
  color: var(--faint);
}
.neg-edit {
  margin-top: 12px;
  padding: 18px;
}
.neg-edit h3 {
  margin: 0 0 12px;
  font-size: 15px;
}
.neg-edit form {
  display: grid;
  gap: 12px;
}
.neg-edit textarea {
  min-height: 200px;
  resize: vertical;
}
.neg-count,
.neg-lead {
  margin: 0;
  color: var(--faint);
  font-size: 13px;
}
.neg-lead {
  margin-bottom: 10px;
  color: var(--muted);
}
.neg-stale {
  margin: 0;
  color: var(--warn);
  font-size: 13px;
}
.neg-foot {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.neg-del {
  margin-left: auto;
  color: var(--danger);
}
.neg-camps-h {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 8px 16px;
}
.neg-search {
  width: min(280px, 100%);
  margin-bottom: 8px;
}
.neg-group + .neg-group {
  border-top: 1px solid var(--line);
}
.neg-group__h,
.neg-target {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 18px;
  cursor: pointer;
}
.neg-group__h {
  background: var(--surface-2);
  font-size: 14px;
}
.neg-target {
  border-top: 1px solid var(--line);
  font-size: 14px;
}
.neg-target.is-off {
  cursor: default;
  opacity: 0.55;
}
.neg-group__h input,
.neg-target input {
  flex-shrink: 0;
  width: 16px;
  height: 16px;
  margin: 0;
  accent-color: var(--ink);
}
.neg-target__name {
  flex: 1;
  min-width: 0;
}
.neg-target__name small {
  display: block;
  color: var(--faint);
  font-size: 12px;
}
.neg-target .st {
  flex-shrink: 0;
  font-size: 13px;
}
.neg-bar {
  position: sticky;
  bottom: 12px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px 12px;
  margin-top: 12px;
  padding: 10px 12px 10px 18px;
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
  box-shadow: var(--shadow-pop);
}
.neg-bar > span:first-child {
  margin-right: auto;
  color: var(--muted);
  font-size: 14px;
}
.neg-preview {
  margin-top: 12px;
}
.neg-result {
  margin-top: 16px;
  padding: 16px 18px;
}
.neg-result.is-ok b {
  color: var(--ok);
}
.neg-result.is-warn b {
  color: var(--warn);
}
.neg-result ul {
  margin: 8px 0 0;
  padding-left: 18px;
  color: var(--danger);
  font-size: 14px;
}
@media (max-width: 720px) {
  .neg-row {
    flex-direction: row;
    align-items: center;
  }
  .neg-target {
    flex-wrap: wrap;
  }
  .neg-target__name {
    flex-basis: calc(100% - 26px);
  }
  .neg-target .st {
    margin-left: 26px;
  }
}
</style>
