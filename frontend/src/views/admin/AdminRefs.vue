<template>
  <div class="adm-refs" :class="{ 'is-loading': loading && data }">
    <div class="adm-refs__bar">
      <button class="btn btn--accent btn--sm" type="button" @click="openLink()"><Plus aria-hidden="true" /> Создать ссылку</button>
      <button class="btn btn--sm" type="button" @click="openGroup()"><FolderPlus aria-hidden="true" /> Создать группу</button>
      <button class="btn btn--ghost btn--sm" type="button" :disabled="!sections.length" @click="expandAll">
        <ChevronsUpDown aria-hidden="true" /> Развернуть все
      </button>
      <button class="btn btn--ghost btn--sm" type="button" :disabled="!sections.length" @click="collapseAll">
        <ChevronsDownUp aria-hidden="true" /> Свернуть все
      </button>
      <UiSwitch :model-value="showPersonal" :disabled="!data" @update:model-value="setPersonal">
        {{ showPersonal ? 'Личные показаны' : 'Личные скрыты' }}
        <small>· {{ num(data?.personal_count || 0) }} {{ plural(data?.personal_count || 0, 'личная ссылка', 'личные ссылки', 'личных ссылок') }}</small>
      </UiSwitch>
      <span class="adm-refs__gap"></span>
      <AdminPeriod :period="data?.period" :query="query" :steps="false" :disabled="loading && !data" @period="setPeriod" />
    </div>

    <div v-if="selected.size" class="adm-refs__sel" role="region" aria-label="Выбранные ссылки">
      <strong>Выбрано: {{ num(selected.size) }}</strong>
      <button class="btn btn--sm" type="button" :disabled="busy || !selectedAdmin.length" @click="openMove(selectedAdmin)">
        Перенести в группу
      </button>
      <button class="btn btn--sm" type="button" :disabled="busy" @click="bulk('deactivate', [...selected])">Выключить</button>
      <button class="btn btn--sm" type="button" :disabled="busy" @click="bulk('activate', [...selected])">Включить</button>
      <button class="btn btn--sm" type="button" :disabled="busy || !selectedAdmin.length" @click="removeMany(selectedAdmin)">
        Удалить
      </button>
      <button class="btn btn--ghost btn--sm" type="button" @click="selected = new Set()">Снять выбор</button>
      <small v-if="selectedAdmin.length < selected.size" class="adm-refs__selnote">
        Личные ссылки можно только выключить или включить.
      </small>
    </div>

    <p v-if="error" class="form-error">{{ error }}</p>
    <p v-else-if="!data" class="empty">Загружаем…</p>
    <p v-else-if="!sections.length" class="empty">
      Ссылок пока нет. Ссылка вида <code>/?ref=код</code> — для блогера, рассылки или рекламы.
    </p>
    <div v-else v-scroll-x class="table-scroll adm-refs__wrap">
      <table class="table adm-refs__table">
        <thead>
          <tr>
            <th class="adm-refs__check">
              <UiCheckbox
                :model-value="allChecked"
                :indeterminate="selected.size > 0 && !allChecked"
                aria-label="Выбрать все ссылки"
                @update:model-value="checkAll"
              />
            </th>
            <th class="num">ID</th>
            <th>Код</th>
            <th>Источник</th>
            <th>Ссылка</th>
            <th class="num">Клики</th>
            <th v-tip="`Клики по дням с ${formatDay(data.spark_from)} по сегодня`">7 дней</th>
            <th class="num" v-tip="'Зарегистрировались за период'">Рег.</th>
            <th class="num" v-tip="'Сколько человек оплатили за период'">Оплаты</th>
            <th class="num" v-tip="'Какая доля зарегистрированных за период хоть раз оплатила'">Конверсия</th>
            <th class="num" v-tip="'Оплаты пришедших за период минус возвраты'">Доход</th>
            <th class="num" v-tip="'Вторые и следующие платежи пришедших'">Повтор. опл.</th>
            <th class="num" v-tip="'Сколько людей привели пришедшие по ссылке через свои личные ссылки'">Каскад</th>
            <th class="num" v-tip="'Оплаты людей из каскада минус возвраты'">Доп. доход</th>
            <th>Создан</th>
            <th class="adm-refs__gear" aria-label="Действия"></th>
          </tr>
        </thead>
        <tbody v-for="s in sections" :key="s.key" :class="{ 'is-collapsed': collapsed.has(s.key) }">
          <tr class="adm-refs__group">
            <td class="adm-refs__check">
              <UiCheckbox
                :model-value="groupChecked(s) === 'all'"
                :indeterminate="groupChecked(s) === 'some'"
                :aria-label="`Выбрать ссылки группы «${s.name}»`"
                @update:model-value="checkGroup(s, $event)"
              />
            </td>
            <td colspan="4">
              <button
                class="adm-refs__toggle"
                type="button"
                :aria-expanded="!collapsed.has(s.key)"
                @click="toggleSection(s.key)"
              >
                <ChevronRight class="adm-refs__chev" aria-hidden="true" />
                <i class="adm-refs__dot" :style="{ background: s.color }"></i>
                <strong>{{ s.name }}</strong>
                <span class="adm-refs__count">{{ num(s.items.length) }}</span>
              </button>
            </td>
            <td class="num">{{ num(s.total.clicks) }}</td>
            <td class="adm-refs__spark"><Sparkline :points="sparkPoints(s.total.spark)" value-key="v" :height="22" :color="s.color" /></td>
            <td class="num">{{ num(s.total.registrations) }}</td>
            <td class="num">{{ num(s.total.payers) }}</td>
            <td class="num">{{ conv(s.total) }}</td>
            <td class="num">{{ rubles(s.total.revenue_rub) }}</td>
            <td class="num">{{ num(s.total.repeat_payments) }}</td>
            <td class="num">{{ num(s.total.cascade) }}</td>
            <td class="num">{{ rubles(s.total.cascade_rub) }}</td>
            <td></td>
            <td class="adm-refs__gear">
              <UiMenu v-if="s.group" size="sm" :label="`Группа «${s.name}»`">
                <UiMenuItem :icon="Pencil" @click="openGroup(s.group)">Изменить</UiMenuItem>
                <UiMenuItem :icon="ArrowUp" :disabled="s.index === 0" @click="moveGroup(s.group, -1)">Выше</UiMenuItem>
                <UiMenuItem :icon="ArrowDown" :disabled="s.index === groups.length - 1" @click="moveGroup(s.group, 1)">
                  Ниже
                </UiMenuItem>
                <UiMenuItem :icon="Trash2" danger @click="removeGroup(s.group)">Удалить группу</UiMenuItem>
              </UiMenu>
            </td>
          </tr>
          <template v-if="!collapsed.has(s.key)">
            <tr v-for="r in s.items" :key="r.id" :class="{ 'is-muted': !r.active, 'is-selected': selected.has(r.id) }">
              <td class="adm-refs__check">
                <UiCheckbox :model-value="selected.has(r.id)" :aria-label="`Выбрать ${r.code}`" @update:model-value="check(r.id, $event)" />
              </td>
              <td class="num adm-refs__id">{{ r.id }}</td>
              <td>
                <code class="adm-refs__code">{{ r.code }}</code>
                <span v-if="!r.active" class="chip chip--info">выключена</span>
              </td>
              <td class="adm-refs__name">
                <template v-if="r.personal">
                  <RouterLink :to="`${ADMIN_BASE}/users?user=${r.owner_id}`">{{ r.owner_email || `#${r.owner_id}` }}</RouterLink>
                </template>
                <template v-else>{{ r.name || '—' }}</template>
              </td>
              <td class="adm-refs__url">
                <span :title="r.url">{{ shortUrl(r.url) }}</span>
                <button class="icon-btn adm-refs__copy" type="button" v-tip="'Скопировать ссылку'" aria-label="Скопировать ссылку" @click="copy(r.url)">
                  <Copy aria-hidden="true" />
                </button>
              </td>
              <td class="num">{{ num(r.clicks) }}</td>
              <td class="adm-refs__spark" v-tip="sparkTip(r.spark)">
                <Sparkline :points="sparkPoints(r.spark)" value-key="v" :height="22" :color="s.color" />
              </td>
              <td class="num">{{ num(r.registrations) }}</td>
              <td class="num" v-tip="r.payments !== r.payers ? `${num(r.payments)} ${plural(r.payments, 'платёж', 'платежа', 'платежей')}` : ''">
                {{ num(r.payers) }}
              </td>
              <td class="num">{{ conv(r) }}</td>
              <td class="num">{{ rubles(r.revenue_rub) }}</td>
              <td class="num">{{ num(r.repeat_payments) }}</td>
              <td class="num">{{ num(r.cascade) }}</td>
              <td class="num">{{ rubles(r.cascade_rub) }}</td>
              <td class="adm-refs__date">{{ shortDate(r.created_at) }}</td>
              <td class="adm-refs__gear">
                <UiMenu size="sm" :label="`Ссылка ${r.code}`">
                  <UiMenuItem v-if="!r.personal" :icon="Pencil" @click="openLink(r)">Переименовать</UiMenuItem>
                  <UiMenuItem v-if="!r.personal" :icon="FolderInput" @click="openMove([r.id], r.group_id)">
                    Перенести в группу
                  </UiMenuItem>
                  <UiMenuItem :icon="r.active ? PowerOff : Power" @click="bulk(r.active ? 'deactivate' : 'activate', [r.id])">
                    {{ r.active ? 'Выключить' : 'Включить' }}
                  </UiMenuItem>
                  <UiMenuItem v-if="!r.personal" :icon="Trash2" danger @click="removeMany([r.id])">Удалить</UiMenuItem>
                </UiMenu>
              </td>
            </tr>
            <tr v-if="!s.items.length" class="adm-refs__empty">
              <td></td>
              <td colspan="15">В группе пока нет ссылок — выберите ссылки флажками и перенесите их сюда.</td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
    <p v-if="data && sections.length" class="hint">
      Код запоминается в браузере при первом заходе на 90 дней и записывается пользователю при регистрации; следующие
      ссылки его не перебивают. Период считает клики, регистрации и оплаты по дате события; за «всё время» клики — общий
      счётчик ссылки. Выключенная ссылка не считает клики и не записывается новым пользователям.
    </p>
  </div>

  <Modal :open="!!linkForm" :title="linkForm?.id ? `Ссылка ${linkForm.code}` : 'Новая ссылка'" :locked="busy" @close="linkForm = null">
    <form v-if="linkForm" id="ref-link-form" class="form-grid" @submit.prevent="saveLink">
      <p v-if="formError" class="form-error">{{ formError }}</p>
      <div v-field class="field">
        <label>Код</label>
        <input
          v-model.trim="linkForm.code"
          type="text"
          maxlength="40"
          required
          autocomplete="off"
          placeholder="blogger-anna"
          @input="linkForm.code = linkForm.code.toLowerCase()"
        />
        <small class="field-hint">Латинские буквы, цифры, «-» и «_», от 2 до 40 символов.</small>
      </div>
      <div v-field class="field">
        <label>Источник</label>
        <input v-model="linkForm.name" type="text" maxlength="120" placeholder="Кто или где размещает ссылку" />
      </div>
      <div v-if="!linkForm.id" v-field class="field">
        <label>Группа</label>
        <UiSelect v-model="linkForm.group_id" :options="groupOptions" block />
      </div>
      <p v-if="linkForm.id && linkForm.code !== linkForm.was" class="field-hint">
        Старый адрес перестанет работать: код запоминается в браузере, а ищется по нему ссылка.
      </p>
    </form>
    <template #footer>
      <button class="btn btn--ghost" type="button" :disabled="busy" @click="linkForm = null">Отмена</button>
      <button class="btn btn--accent" type="submit" form="ref-link-form" :disabled="busy">
        {{ busy ? 'Сохраняем…' : linkForm?.id ? 'Сохранить' : 'Создать' }}
      </button>
    </template>
  </Modal>

  <Modal :open="!!groupForm" :title="groupForm?.id ? `Группа «${groupForm.was}»` : 'Новая группа'" :locked="busy" @close="groupForm = null">
    <form v-if="groupForm" id="ref-group-form" class="form-grid" @submit.prevent="saveGroup">
      <p v-if="formError" class="form-error">{{ formError }}</p>
      <div v-field class="field">
        <label>Название</label>
        <input v-model="groupForm.name" type="text" maxlength="60" required placeholder="Блогеры" />
      </div>
      <div class="field">
        <label id="ref-group-color">Цвет</label>
        <div class="adm-refs__swatches" role="radiogroup" aria-labelledby="ref-group-color">
          <button
            v-for="c in COLORS"
            :key="c"
            type="button"
            role="radio"
            class="adm-refs__swatch"
            :class="{ 'is-on': groupForm.color === c }"
            :aria-checked="groupForm.color === c"
            :aria-label="c"
            :style="{ background: c }"
            @click="groupForm.color = c"
          ></button>
        </div>
      </div>
    </form>
    <template #footer>
      <button class="btn btn--ghost" type="button" :disabled="busy" @click="groupForm = null">Отмена</button>
      <button class="btn btn--accent" type="submit" form="ref-group-form" :disabled="busy">
        {{ busy ? 'Сохраняем…' : groupForm?.id ? 'Сохранить' : 'Создать' }}
      </button>
    </template>
  </Modal>

  <Modal :open="!!moveForm" title="Перенести в группу" :locked="busy" @close="moveForm = null">
    <form v-if="moveForm" id="ref-move-form" class="form-grid" @submit.prevent="saveMove">
      <div v-field class="field">
        <label>{{ moveForm.ids.length > 1 ? `Ссылки (${num(moveForm.ids.length)}) — в группу` : 'В группу' }}</label>
        <UiSelect v-model="moveForm.group_id" :options="groupOptions" block />
      </div>
    </form>
    <template #footer>
      <button class="btn btn--ghost" type="button" :disabled="busy" @click="moveForm = null">Отмена</button>
      <button class="btn btn--accent" type="submit" form="ref-move-form" :disabled="busy">
        {{ busy ? 'Переносим…' : 'Перенести' }}
      </button>
    </template>
  </Modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import {
  ArrowDown,
  ArrowUp,
  ChevronRight,
  ChevronsDownUp,
  ChevronsUpDown,
  Copy,
  FolderInput,
  FolderPlus,
  Pencil,
  Plus,
  Power,
  PowerOff,
  Trash2,
} from '@lucide/vue'
import Sparkline from '../../components/Sparkline.vue'
import Modal from '../../components/ui/Modal.vue'
import UiCheckbox from '../../components/ui/UiCheckbox.vue'
import UiMenu from '../../components/ui/UiMenu.vue'
import UiMenuItem from '../../components/ui/UiMenuItem.vue'
import UiSelect from '../../components/ui/UiSelect.vue'
import UiSwitch from '../../components/ui/UiSwitch.vue'
import AdminPeriod from './AdminPeriod.vue'
import { ADMIN_BASE } from './adminNav'
import { groupColor } from './adminUsers'
import { useAdminPeriod } from './useAdminPeriod'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { askConfirm } from '../../composables/useConfirm'
import { pushToast } from '../../composables/useToasts'
import { formatDay, num, pct, plural, rubles } from '../../utils/format'

/**
 * Реф-ссылки (docs/admin-plan.md, раздел 5): ссылки по группам со строкой итогов, период (по умолчанию — всё время,
 * общий с дашбордом), личные ссылки пользователей — отдельной группой по переключателю. Числа считает сервер
 * (app/admin_refs.py), итоги групп — сумма строк.
 */
const COLORS = ['#2f9e6e', '#2f7fd6', '#d9822b', '#8b5cf6', '#d6456b', '#7a8f1f', '#1fa3b5', '#6b7280']
const COLLAPSED_KEY = 'aimarketing-admin-refs-collapsed'

const route = useRoute()
const router = useRouter()
const { query, setPeriod } = useAdminPeriod({ fallback: 'all' })
const showPersonal = computed(() => route.query.personal === '1')

const data = ref(null)
const loading = ref(false)
const error = ref('')
const busy = ref(false)
const selected = ref(new Set())
const collapsed = ref(new Set(recallCollapsed()))
const linkForm = ref(null)
const groupForm = ref(null)
const moveForm = ref(null)
const formError = ref('')

const groups = computed(() => data.value?.groups || [])
const items = computed(() => data.value?.items || [])
const byId = computed(() => new Map(items.value.map((r) => [r.id, r])))
const groupOptions = computed(() => [
  { value: null, label: 'Без группы' },
  ...groups.value.map((g) => ({ value: g.id, label: g.name })),
])

// --- загрузка ---

let seq = 0
async function load() {
  const my = ++seq
  loading.value = true
  error.value = ''
  try {
    const out = await adminApi.refs({ ...query.value, personal: showPersonal.value ? 1 : '' })
    if (my !== seq) return
    data.value = out
    // Выбор переживает перезагрузку, но не ссылки, которых больше нет.
    const ids = new Set(out.items.map((r) => r.id))
    selected.value = new Set([...selected.value].filter((id) => ids.has(id)))
  } catch (err) {
    if (my === seq) error.value = errorText(err)
  } finally {
    if (my === seq) loading.value = false
  }
}

watch([query, showPersonal], load, { immediate: true, deep: true })

function setPersonal(on) {
  const { personal: _, ...rest } = route.query
  router.replace({ query: on ? { ...rest, personal: '1' } : rest })
}

// --- группы и итоги ---

const ZERO = () => ({
  clicks: 0,
  spark: [0, 0, 0, 0, 0, 0, 0],
  registrations: 0,
  converted: 0,
  payers: 0,
  payments: 0,
  repeat_payments: 0,
  revenue_rub: 0,
  cascade: 0,
  cascade_rub: 0,
})

function totals(rows) {
  const t = ZERO()
  for (const r of rows) {
    for (const k of Object.keys(t)) {
      if (k === 'spark') r.spark.forEach((v, i) => (t.spark[i] += v))
      else t[k] += r[k]
    }
  }
  return t
}

const sections = computed(() => {
  if (!data.value) return []
  const admin = items.value.filter((r) => !r.personal)
  const out = groups.value.map((g, index) => {
    const rows = admin.filter((r) => r.group_id === g.id)
    return { key: `g${g.id}`, name: g.name, color: groupColor(g.id, g.color), group: g, index, items: rows, total: totals(rows) }
  })
  const loose = admin.filter((r) => !r.group_id)
  if (loose.length) out.push({ key: 'none', name: 'Без группы', color: 'var(--faint)', group: null, items: loose, total: totals(loose) })
  if (showPersonal.value) {
    const own = items.value
      .filter((r) => r.personal)
      .sort((a, b) => b.registrations - a.registrations || b.clicks - a.clicks || b.id - a.id)
    out.push({ key: 'personal', name: 'Личные ссылки пользователей', color: 'var(--ink)', group: null, items: own, total: totals(own) })
  }
  return out
})

function recallCollapsed() {
  try {
    const v = JSON.parse(localStorage.getItem(COLLAPSED_KEY) || '[]')
    return Array.isArray(v) ? v.filter((x) => typeof x === 'string') : []
  } catch {
    return []
  }
}

function rememberCollapsed() {
  try {
    localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...collapsed.value]))
  } catch {
    /* память браузера недоступна — состояние живёт до перезагрузки */
  }
}

function toggleSection(key) {
  const next = new Set(collapsed.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  collapsed.value = next
  rememberCollapsed()
}

function expandAll() {
  collapsed.value = new Set()
  rememberCollapsed()
}

function collapseAll() {
  collapsed.value = new Set(sections.value.map((s) => s.key))
  rememberCollapsed()
}

// --- выбор строк ---

const selectedAdmin = computed(() => [...selected.value].filter((id) => byId.value.get(id) && !byId.value.get(id).personal))
const allChecked = computed(() => items.value.length > 0 && items.value.every((r) => selected.value.has(r.id)))

function check(id, on) {
  const next = new Set(selected.value)
  if (on) next.add(id)
  else next.delete(id)
  selected.value = next
}

function checkAll(on) {
  selected.value = on ? new Set(items.value.map((r) => r.id)) : new Set()
}

function groupChecked(s) {
  const n = s.items.filter((r) => selected.value.has(r.id)).length
  if (!n) return 'none'
  return n === s.items.length ? 'all' : 'some'
}

function checkGroup(s, on) {
  const next = new Set(selected.value)
  for (const r of s.items) {
    if (on) next.add(r.id)
    else next.delete(r.id)
  }
  selected.value = next
}

// --- вид ячеек ---

function conv(r) {
  return r.registrations ? pct((r.converted / r.registrations) * 100) : '—'
}

function sparkPoints(spark) {
  return (spark || []).map((v) => ({ v }))
}

function sparkTip(spark) {
  const sum = (spark || []).reduce((a, b) => a + b, 0)
  return `${num(sum)} ${plural(sum, 'клик', 'клика', 'кликов')} за 7 дней · сегодня ${num(spark?.[6] || 0)}`
}

function shortUrl(url) {
  return url.replace(/^https?:\/\//, '')
}

function shortDate(iso) {
  return new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' })
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text)
    pushToast('Ссылка скопирована')
  } catch {
    pushToast('Не удалось скопировать — выделите ссылку вручную', 'error')
  }
}

// --- ссылки ---

function openLink(r = null) {
  formError.value = ''
  linkForm.value = r
    ? { id: r.id, code: r.code, was: r.code, name: r.name, active: r.active }
    : { id: null, code: '', name: '', active: true, group_id: null }
}

async function saveLink() {
  const f = linkForm.value
  busy.value = true
  formError.value = ''
  try {
    if (f.id) await adminApi.updateRef(f.id, { code: f.code, name: f.name, active: f.active })
    else await adminApi.createRef({ code: f.code, name: f.name, active: true, group_id: f.group_id })
    pushToast(f.id ? 'Ссылка сохранена' : 'Ссылка создана')
    linkForm.value = null
    load()
  } catch (err) {
    formError.value = errorText(err)
  } finally {
    busy.value = false
  }
}

function openMove(ids, groupId = null) {
  moveForm.value = { ids, group_id: groupId }
}

async function saveMove() {
  const { ids, group_id: gid } = moveForm.value
  if (await bulk('move', ids, gid)) moveForm.value = null
}

const DONE = {
  move: (n) => `Перенесено: ${num(n)}`,
  activate: (n) => `Включено: ${num(n)}`,
  deactivate: (n) => `Выключено: ${num(n)}`,
  delete: (n) => `Удалено: ${num(n)}`,
}

async function bulk(action, ids, groupId = null) {
  busy.value = true
  try {
    const res = await adminApi.bulkRefs(ids, action, groupId)
    pushToast(DONE[action](res.done))
    if (action === 'delete' || ids.length > 1) selected.value = new Set()
    await load()
    return true
  } catch (err) {
    pushToast(errorText(err), 'error')
    return false
  } finally {
    busy.value = false
  }
}

async function removeMany(ids) {
  const rows = ids.map((id) => byId.value.get(id)).filter(Boolean)
  const people = rows.reduce((a, r) => a + r.registrations, 0)
  const what = rows.length === 1 ? `ссылку ${rows[0].code}` : `${num(rows.length)} ${plural(rows.length, 'ссылку', 'ссылки', 'ссылок')}`
  const who = people ? ' Пришедшие по ним пользователи останутся, но без источника.' : ''
  if (!(await askConfirm(`Удалить ${what} и статистику кликов?${who}`, { danger: true }))) return
  bulk('delete', ids)
}

// --- группы ---

function openGroup(g = null) {
  formError.value = ''
  groupForm.value = g
    ? { id: g.id, name: g.name, was: g.name, color: g.color || groupColor(g.id) }
    : { id: null, name: '', was: '', color: COLORS[groups.value.length % COLORS.length] }
}

async function saveGroup() {
  const f = groupForm.value
  busy.value = true
  formError.value = ''
  // Цвет по номеру (hsl) — не из палитры: такой не сохраняем, пусть остаётся по номеру.
  const color = COLORS.includes(f.color) ? f.color : ''
  try {
    if (f.id) await adminApi.updateRefGroup(f.id, { name: f.name, color })
    else await adminApi.createRefGroup({ name: f.name, color })
    pushToast(f.id ? 'Группа сохранена' : 'Группа создана')
    groupForm.value = null
    load()
  } catch (err) {
    formError.value = errorText(err)
  } finally {
    busy.value = false
  }
}

async function moveGroup(g, dir) {
  const ids = groups.value.map((x) => x.id)
  const i = ids.indexOf(g.id)
  const j = i + dir
  if (j < 0 || j >= ids.length) return
  ;[ids[i], ids[j]] = [ids[j], ids[i]]
  try {
    const res = await adminApi.orderRefGroups(ids)
    data.value = { ...data.value, groups: res.groups }
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

async function removeGroup(g) {
  const n = items.value.filter((r) => r.group_id === g.id).length
  const links = n ? ` Её ссылки (${num(n)}) останутся и переедут в «Без группы».` : ''
  if (!(await askConfirm(`Удалить группу «${g.name}»?${links}`, { danger: true }))) return
  try {
    await adminApi.deleteRefGroup(g.id)
    pushToast('Группа удалена')
    load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}
</script>
