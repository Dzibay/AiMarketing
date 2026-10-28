<template>
  <Modal :open="open" title="Копия кампании" :locked="running" @close="$emit('close')">
    <form id="duplicate-form" class="form-grid" @submit.prevent="submit">
      <p class="field-hint field--wide">
        Копируются настройки, группы, ключевые фразы (со ставками при ручной стратегии) и текстовые объявления. Копия
        создаётся остановленной; объявления — черновиками.
      </p>
      <div v-field class="field field--wide">
        <label>Название копии</label>
        <input v-model.trim="form.name" type="text" maxlength="255" required />
      </div>
      <div v-field class="field field--wide">
        <label>Пространство для копии</label>
        <select v-model.number="form.target_workspace_id">
          <option v-for="w in workspaceChoices" :key="w.id" :value="w.id">
            {{ w.name }} · счётчик {{ w.counter_id }}{{ w.client_name ? ` · ${w.client_name}` : '' }}
          </option>
        </select>
        <p v-if="form.target_workspace_id !== workspace.id" class="field-hint">
          Копия получит счётчик выбранного пространства вместо № {{ workspace.counter_id }}.
        </p>
      </div>
      <div v-field class="field field--wide">
        <label>Кабинет Директа</label>
        <select v-model.number="form.target_account_id" required>
          <optgroup v-if="accounts.length" label="Кабинеты пространства">
            <option v-for="a in accounts" :key="a.id" :value="a.id" :disabled="!a.enabled || !!a.sandbox !== sourceSandbox">
              {{ accountLabel(a) }}
            </option>
          </optgroup>
          <optgroup v-if="otherAccounts.length" label="Другие кабинеты — подключатся к пространству">
            <option v-for="a in otherAccounts" :key="a.id" :value="a.id" :disabled="!!a.sandbox !== sourceSandbox">
              {{ accountLabel(a) }}
            </option>
          </optgroup>
        </select>
        <p v-if="accountsError" class="field-hint tone-bad">{{ accountsError }}</p>
        <p v-else-if="!accounts.length && !otherAccounts.length" class="field-hint tone-warn">
          Нет ни одного кабинета Директа — подключите логин на странице «Аккаунты».
        </p>
        <p v-else-if="targetAccount && targetAccount.id !== campaign?.account_id" class="field-hint">
          В другой кабинет не переносятся картинки, визитки и турбо-страницы — они живут в кабинете-источнике.
          <template v-if="targetAccount.counter_access?.hint"> {{ targetAccount.counter_access.hint }}</template>
        </p>
      </div>
      <p v-if="running" class="sync-status field--wide">{{ progressText || 'Копируем…' }}</p>
      <p v-if="formError" class="form-error field--wide">{{ formError }}</p>
    </form>
    <template #footer>
      <button class="btn btn--ghost" type="button" :disabled="running" @click="$emit('close')">Отмена</button>
      <button class="btn btn--accent" type="submit" form="duplicate-form" :disabled="running || !form.target_account_id">
        {{ running ? 'Копируем…' : 'Создать копию' }}
      </button>
    </template>
  </Modal>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import Modal from '../../components/ui/Modal.vue'
import { errorText, jobProgressText, wsApi } from '../../api/web'
import { loadWorkspaces, useWorkspaces } from '../../composables/useWorkspaces'

const props = defineProps({
  open: { type: Boolean, default: false },
  workspace: { type: Object, required: true },
  // Строка кампании: id, name, account_id.
  campaign: { type: Object, default: null },
})
const emit = defineEmits(['close', 'done'])

const { workspaces } = useWorkspaces()
const form = reactive({ name: '', target_workspace_id: null, target_account_id: null })
const accounts = ref([])
const otherAccounts = ref([])
const accountsError = ref('')
const sourceSandbox = ref(false)
const running = ref(false)
const progressText = ref('')
const formError = ref('')

const workspaceChoices = computed(() => {
  const list = workspaces.value.filter((w) => !w.archived || w.id === props.workspace.id)
  return list.some((w) => w.id === props.workspace.id) ? list : [props.workspace, ...list]
})
const targetAccount = computed(
  () => [...accounts.value, ...otherAccounts.value].find((a) => a.id === form.target_account_id) || null,
)

function accountLabel(a) {
  const name = a.name && a.name !== a.client_login ? ` — ${a.name}` : ''
  return `${a.client_login}${name}${a.sandbox ? ' (песочница)' : ''}${a.id === props.campaign?.account_id ? ' — тот же кабинет' : ''}`
}

async function loadAccounts(wsId) {
  accountsError.value = ''
  try {
    // Кабинеты пространства + остальные кабинеты пользователя (с подсказкой про доступ к счётчику).
    const data = await wsApi.campaignOptions(wsId)
    if (form.target_workspace_id !== wsId) return
    accounts.value = data.accounts || []
    otherAccounts.value = data.other_accounts || []
    const usable = accounts.value.filter((a) => a.enabled && !!a.sandbox === sourceSandbox.value)
    const same = usable.find((a) => a.id === props.campaign?.account_id)
    form.target_account_id = (same || usable[0])?.id ?? null
  } catch (err) {
    accounts.value = []
    otherAccounts.value = []
    form.target_account_id = null
    accountsError.value = errorText(err)
  }
}

watch(
  () => props.open,
  async (open) => {
    if (!open || !props.campaign) return
    formError.value = ''
    progressText.value = ''
    form.name = `${props.campaign.name} (копия)`.slice(0, 255)
    form.target_workspace_id = props.workspace.id
    form.target_account_id = null
    loadWorkspaces().catch(() => {})
    try {
      const own = await wsApi.accounts(props.workspace.id)
      sourceSandbox.value = !!(own.items || []).find((a) => a.id === props.campaign.account_id)?.sandbox
    } catch {
      sourceSandbox.value = false
    }
    loadAccounts(props.workspace.id)
  },
  { immediate: true },
)

watch(
  () => form.target_workspace_id,
  (id, prev) => {
    if (id && prev && id !== prev) loadAccounts(id)
  },
)

async function submit() {
  if (!props.campaign || !form.target_account_id) return
  running.value = true
  formError.value = ''
  try {
    const result = await wsApi.duplicate(
      props.workspace.id,
      props.campaign.id,
      {
        name: form.name,
        target_account_id: form.target_account_id,
        target_workspace_id: form.target_workspace_id,
      },
      (job) => {
        progressText.value = jobProgressText(job)
      },
    )
    emit('done', { ...result, target_workspace_id: form.target_workspace_id })
  } catch (err) {
    formError.value = errorText(err)
  } finally {
    running.value = false
  }
}
</script>
