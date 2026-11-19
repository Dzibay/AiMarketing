<template>
  <Modal :open="open" title="Копия кампании" :locked="running" :wide="step === 'review'" @close="$emit('close')">
    <form v-if="step === 'form'" id="duplicate-form" class="form-grid" @submit.prevent="toReview">
      <p class="field-hint field--wide">
        Копируются настройки, группы, ключевые фразы (со ставками при ручной стратегии), объявления и корректировки
        ставок. Копия создаётся остановленной; объявления — черновиками.
      </p>
      <div v-field class="field field--wide">
        <label>Название копии</label>
        <input v-model.trim="form.name" type="text" maxlength="255" required />
      </div>
      <div v-field class="field field--wide">
        <label>Проект для копии</label>
        <UiSelect v-model="form.target_workspace_id" :options="workspaceOptions" />
        <p v-if="form.target_workspace_id !== workspace.id" class="field-hint">
          Копия получит счётчик выбранного проекта вместо № {{ workspace.counter_id }}.
        </p>
      </div>
      <div v-field class="field field--wide">
        <label>Кабинет Директа</label>
        <UiSelect v-model="form.target_account_id" :options="accountOptions" placeholder="Выберите кабинет" required />
        <p v-if="accountsError" class="field-hint tone-bad">{{ accountsError }}</p>
        <p v-else-if="!accounts.length && !otherAccounts.length" class="field-hint tone-warn">
          Нет ни одного кабинета Директа — подключите логин на странице «Подключения».
        </p>
        <p v-else-if="otherCabinet" class="field-hint">
          В другой кабинет не переносятся картинки, визитки и турбо-страницы — они живут в кабинете-источнике.
          <template v-if="targetAccount.counter_access?.hint"> {{ targetAccount.counter_access.hint }}</template>
        </p>
      </div>
      <div class="field field--wide">
        <label>Где показывать копию</label>
        <RegionPicker v-model="form.region_ids" :selected="regionNames" />
        <p class="field-hint">Новый город — и в текстах: на следующем шаге покажем, что заменим.</p>
      </div>
      <p v-if="formError" class="form-error field--wide">{{ formError }}</p>
    </form>

    <form v-else id="duplicate-form" class="form-grid" @submit.prevent="submit">
      <div v-field class="field">
        <label>Старый город в текстах</label>
        <input v-model.trim="replace.from_city" type="text" maxlength="100" :disabled="running" />
      </div>
      <div v-field class="field">
        <label>Новый город</label>
        <input v-model.trim="replace.to_city" type="text" maxlength="100" :disabled="running" />
      </div>
      <div v-field class="field">
        <label>Старый сайт</label>
        <input v-model.trim="replace.from_site" type="text" maxlength="300" :disabled="running" />
      </div>
      <div v-field class="field">
        <label>Новый сайт</label>
        <input v-model.trim="replace.to_site" type="text" maxlength="300" :disabled="running" />
      </div>
      <div class="field--wide dup-actions">
        <button class="btn btn--ghost btn--sm" type="button" :disabled="running || loading" @click="loadPreview">
          {{ loading ? 'Ищем…' : 'Найти заново' }}
        </button>
        <span v-if="replaceStale" class="field-hint">Поля замен изменились — нажмите «Найти заново», чтобы увидеть список.</span>
        <span v-else class="field-hint">Пустое поле — не менять. Город склоняем сами: «в Москве» → «в Казани».</span>
      </div>

      <div class="field--wide">
        <p v-if="loading && !preview" class="sync-status">Ищем старый город и сайт в кампании…</p>
        <template v-else-if="preview">
          <div v-if="preview.items.length" class="dup-head">
            <UiCheckbox :model-value="allOn" :indeterminate="!allOn && skip.size < preview.items.length" @update:model-value="toggleAll">
              Заменить в копии: {{ preview.items.length - skip.size }} из {{ preview.items.length }}
            </UiCheckbox>
          </div>
          <p v-else class="field-hint">Ни старого города, ни старого сайта в текстах нет — копия будет как оригинал.</p>
          <ul v-if="preview.items.length" class="dup-list">
            <li v-for="item in preview.items" :key="item.key" class="dup-row">
              <UiCheckbox :model-value="!skip.has(item.key)" :disabled="running" @update:model-value="toggle(item.key)">
                <span class="dup-row__where">{{ item.field }} · {{ niceQuotes(item.where) }}</span>
                <span class="dup-row__before">{{ item.before }}</span>
                <span class="dup-row__after">{{ item.after }}</span>
                <small v-if="item.too_long" class="tone-warn">После замены {{ item.too_long }} — Директ может не принять</small>
              </UiCheckbox>
            </li>
          </ul>
          <p v-if="preview.more" class="field-hint">И ещё {{ preview.more }} строк — заменим так же.</p>
          <UiNotice v-if="preview.left.length" tone="warn">
            Не заменим — поправьте в копии сами:
            {{ preview.left.map((l) => `«${l.text}»${l.count > 1 ? ` ×${l.count}` : ''}`).join(', ') }}
          </UiNotice>
          <UiNotice v-for="w in preview.warnings" :key="w" tone="warn">{{ w }}</UiNotice>
          <p v-if="preview.bid_modifiers?.count" class="field-hint">
            Корректировки ставок перенесём: {{ preview.bid_modifiers.count }}<template v-if="preview.bid_modifiers.skipped">
              (не перенесём выключенные и ретаргетинг другого кабинета: {{ preview.bid_modifiers.skipped }})</template
            >.
          </p>
          <UiCheckbox v-if="preview.shared_sets?.count" v-model="copySharedSets" :disabled="running">
            Подключить наборы минус-фраз ({{ preview.shared_sets.count }})
            <small>
              Перенесём в кабинет копии<template v-if="preview.shared_sets.names.length">:
                {{ preview.shared_sets.names.map((n) => `«${n}»`).join(', ') }}</template
              >. Такой же набор там уже есть — подключим его.
            </small>
          </UiCheckbox>
        </template>
      </div>
      <p v-if="running" class="sync-status field--wide">{{ progressText || 'Копируем…' }}</p>
      <p v-if="formError" class="form-error field--wide">{{ formError }}</p>
    </form>

    <template #footer>
      <button v-if="step === 'review'" class="btn btn--ghost" type="button" :disabled="running" @click="step = 'form'">
        Назад
      </button>
      <button v-else class="btn btn--ghost" type="button" :disabled="running" @click="$emit('close')">Отмена</button>
      <button
        class="btn btn--accent"
        type="submit"
        form="duplicate-form"
        :disabled="running || loading || replaceStale || !form.target_account_id || !form.region_ids.some((r) => r > 0)"
      >
        {{ running ? 'Копируем…' : step === 'form' ? 'Дальше' : 'Создать копию' }}
      </button>
    </template>
  </Modal>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import Modal from '../../components/ui/Modal.vue'
import UiSelect from '../../components/ui/UiSelect.vue'
import UiCheckbox from '../../components/ui/UiCheckbox.vue'
import UiNotice from '../../components/ui/UiNotice.vue'
import RegionPicker from '../../components/RegionPicker.vue'
import { errorText, jobProgressText, webApi, wsApi } from '../../api/web'
import { loadWorkspaces, useWorkspaces } from '../../composables/useWorkspaces'
import { niceQuotes } from '../../utils/format'

const props = defineProps({
  open: { type: Boolean, default: false },
  workspace: { type: Object, required: true },
  // Строка кампании: id, name, account_id.
  campaign: { type: Object, default: null },
})
const emit = defineEmits(['close', 'done'])

const { workspaces } = useWorkspaces()
const form = reactive({ name: '', target_workspace_id: null, target_account_id: null, region_ids: [] })
const replace = reactive({ from_city: '', to_city: '', from_site: '', to_site: '' })
const step = ref('form')
const regionNames = ref([])
const originalRegions = ref([])
const preview = ref(null)
const skip = ref(new Set())
// Поля замен, по которым построен список: копия меняет только то, что человек видел.
const previewedReplace = ref(null)
const copySharedSets = ref(true)
const nameTouched = ref(false)
const accounts = ref([])
const otherAccounts = ref([])
const accountsError = ref('')
const loading = ref(false)
const running = ref(false)
const progressText = ref('')
const formError = ref('')

const workspaceChoices = computed(() => {
  const list = workspaces.value.filter((w) => !w.archived || w.id === props.workspace.id)
  return list.some((w) => w.id === props.workspace.id) ? list : [props.workspace, ...list]
})
const workspaceOptions = computed(() =>
  workspaceChoices.value.map((w) => ({
    value: w.id,
    label: niceQuotes(w.name),
    hint: [`счётчик ${w.counter_id}`, niceQuotes(w.client_name)].filter(Boolean).join(' · '),
  })),
)
const accountOptions = computed(() => [
  ...accounts.value.map((a) => ({
    value: a.id,
    label: accountLabel(a),
    disabled: !a.enabled,
    group: 'Кабинеты проекта',
  })),
  ...otherAccounts.value.map((a) => ({
    value: a.id,
    label: accountLabel(a),
    group: 'Другие кабинеты — подключатся к проекту',
  })),
])
const targetAccount = computed(
  () => [...accounts.value, ...otherAccounts.value].find((a) => a.id === form.target_account_id) || null,
)
const otherCabinet = computed(() => !!targetAccount.value && targetAccount.value.id !== props.campaign?.account_id)
const allOn = computed(() => skip.value.size === 0)
const replaceStale = computed(
  () =>
    step.value === 'review' &&
    !!preview.value &&
    !!previewedReplace.value &&
    Object.keys(previewedReplace.value).some((k) => (replace[k] || '') !== previewedReplace.value[k]),
)

function accountLabel(a) {
  const name = a.name && a.name !== a.client_login ? ` — ${a.name}` : ''
  return `${a.client_login}${name}${a.id === props.campaign?.account_id ? ' — тот же кабинет' : ''}`
}

async function loadAccounts(wsId) {
  accountsError.value = ''
  try {
    // Кабинеты проекта + остальные кабинеты пользователя (с подсказкой про доступ к счётчику).
    const data = await wsApi.campaignOptions(wsId)
    if (form.target_workspace_id !== wsId) return
    accounts.value = data.accounts || []
    otherAccounts.value = data.other_accounts || []
    const usable = accounts.value.filter((a) => a.enabled)
    const same = usable.find((a) => a.id === props.campaign?.account_id)
    form.target_account_id = (same || usable[0])?.id ?? null
  } catch (err) {
    accounts.value = []
    otherAccounts.value = []
    form.target_account_id = null
    accountsError.value = errorText(err)
  }
}

async function loadRegions(cid) {
  try {
    const data = await webApi.campaignRegions(cid)
    if (props.campaign?.id !== cid) return
    regionNames.value = data.selected || []
    originalRegions.value = data.region_ids || []
    if (!form.region_ids.length) form.region_ids = [...originalRegions.value]
  } catch {
    // Регионы не прочитали — человек выберет их сам.
  }
}

watch(
  () => props.open,
  async (open) => {
    if (!open || !props.campaign) return
    formError.value = ''
    progressText.value = ''
    step.value = 'form'
    preview.value = null
    nameTouched.value = false
    form.name = `${props.campaign.name} (копия)`.slice(0, 255)
    form.target_workspace_id = props.workspace.id
    form.target_account_id = null
    form.region_ids = []
    loadWorkspaces().catch(() => {})
    loadAccounts(props.workspace.id)
    loadRegions(props.campaign.id)
  },
  { immediate: true },
)

watch(
  () => form.target_workspace_id,
  (id, prev) => {
    if (id && prev && id !== prev) loadAccounts(id)
  },
)

function target() {
  return {
    target_account_id: form.target_account_id,
    target_workspace_id: form.target_workspace_id,
    region_ids: form.region_ids,
  }
}

function sameRegions() {
  const a = [...form.region_ids].sort()
  const b = [...originalRegions.value].sort()
  return a.length === b.length && a.every((v, i) => v === b[i])
}

async function toReview() {
  if (!form.target_account_id) return
  nameTouched.value = form.name !== `${props.campaign.name} (копия)`.slice(0, 255)
  // Поля замен — заново по выбранным регионам и проекту.
  Object.assign(replace, { from_city: '', to_city: '', from_site: '', to_site: '' })
  preview.value = null
  previewedReplace.value = null
  step.value = 'review'
  await loadPreview(true)
}

async function loadPreview(fresh = false) {
  loading.value = true
  formError.value = ''
  try {
    const data = await wsApi.duplicatePreview(props.workspace.id, props.campaign.id, {
      ...target(),
      replace: fresh === true ? null : { ...replace },
    })
    preview.value = data
    skip.value = new Set()
    Object.assign(replace, {
      from_city: data.from_city || '',
      to_city: data.to_city || '',
      from_site: data.from_site || '',
      to_site: data.to_site || '',
    })
    previewedReplace.value = { ...replace }
    copySharedSets.value = true
    if (!nameTouched.value && data.suggested_name && data.suggested_name !== props.campaign.name) {
      form.name = data.suggested_name.slice(0, 255)
    }
  } catch (err) {
    formError.value = errorText(err)
  } finally {
    loading.value = false
  }
}

function toggle(key) {
  const next = new Set(skip.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  skip.value = next
}

function toggleAll(on) {
  skip.value = on ? new Set() : new Set(preview.value.items.map((i) => i.key))
}

async function submit() {
  if (!props.campaign || !form.target_account_id || replaceStale.value) return
  running.value = true
  formError.value = ''
  try {
    const payload = { name: form.name, ...target() }
    if (sameRegions()) delete payload.region_ids
    // Замены — только те, что человек видел в списке.
    if (preview.value) payload.replace = { ...(previewedReplace.value || replace), skip: [...skip.value] }
    if (otherCabinet.value && preview.value?.shared_sets?.count && copySharedSets.value) payload.copy_shared_sets = true
    const result = await wsApi.duplicate(props.workspace.id, props.campaign.id, payload, (job) => {
      progressText.value = jobProgressText(job)
    })
    emit('done', { ...result, target_workspace_id: form.target_workspace_id })
  } catch (err) {
    formError.value = errorText(err)
  } finally {
    running.value = false
  }
}
</script>

<style scoped>
.dup-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.dup-head {
  margin-bottom: 8px;
}
.dup-list {
  list-style: none;
  margin: 0 0 12px;
  padding: 0;
  max-height: 320px;
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius);
}
.dup-row {
  padding: 8px 12px;
  border-bottom: 1px solid var(--line);
}
.dup-row:last-child {
  border-bottom: 0;
}
.dup-row .ui-check__text > span {
  display: block;
}
.dup-row__where {
  color: var(--muted);
  font-size: 0.85em;
}
.dup-row__before {
  color: var(--muted);
  text-decoration: line-through;
  word-break: break-word;
}
.dup-row__after {
  color: var(--ink);
  word-break: break-word;
}
</style>
