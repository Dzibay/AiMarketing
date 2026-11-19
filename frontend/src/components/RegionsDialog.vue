<template>
  <Modal :open="open" :title="title" :locked="saving" @close="emit('close')">
    <p v-if="loading" class="hint">Загружаем регионы…</p>
    <UiNotice v-else-if="loadError" tone="error">{{ loadError }}</UiNotice>

    <template v-else-if="step === 'pick'">
      <p class="hint">
        <template v-if="group">Регионы, где показывается реклама группы.</template>
        <template v-else-if="groupsCount > 1">
          Новый список встанет во все {{ groupsCount }} {{ plural(groupsCount, 'группу', 'группы', 'групп') }} кампании.
        </template>
        <template v-else>Регионы, где показывается реклама кампании.</template>
      </p>
      <UiNotice v-if="!group && overview && !overview.same" tone="warn">
        Сейчас у групп разные регионы. После сохранения у всех групп будет один общий список.
      </UiNotice>
      <RegionPicker v-model="value" :selected="overview?.selected || []" />
    </template>

    <template v-else-if="plan">
      <dl class="rdlg__diff">
        <div>
          <dt>Было</dt>
          <dd>{{ plan.before }}</dd>
        </div>
        <div>
          <dt>Станет</dt>
          <dd><b>{{ plan.after }}</b></dd>
        </div>
      </dl>
      <p v-if="!plan.changed" class="hint">Эти регионы уже стоят — менять нечего.</p>
      <p v-else-if="plan.groups > 1" class="rdlg__count">
        Изменится в {{ plan.changed }} из {{ plan.groups }} {{ plural(plan.groups, 'группы', 'групп', 'групп') }}.
      </p>
      <template v-if="plan.mixed && plan.changed">
        <p class="hint">У групп были разные регионы — каждая получит новый список:</p>
        <ul class="rdlg__groups">
          <li v-for="item in plan.items" :key="item.id">
            <b>{{ item.name }}</b> <span>было: {{ item.before }}</span>
          </li>
          <li v-if="plan.groups > plan.items.length" class="hint">и ещё {{ plan.groups - plan.items.length }}</li>
        </ul>
      </template>
      <p v-if="plan.changed" class="hint">
        Изменение сразу уйдёт в Директ. Вернуть прежние регионы можно кнопкой «Отменить» или в истории изменений.
      </p>
    </template>

    <UiNotice v-if="formError" tone="error">{{ formError }}</UiNotice>

    <template #footer>
      <template v-if="step === 'pick'">
        <button class="btn btn--ghost" type="button" @click="emit('close')">Отмена</button>
        <button class="btn btn--accent" type="button" :disabled="loading || !!loadError || checking" @click="check">
          {{ checking ? 'Проверяем…' : 'Дальше' }}
        </button>
      </template>
      <template v-else>
        <button class="btn btn--ghost" type="button" :disabled="saving" @click="step = 'pick'">Назад</button>
        <button class="btn btn--accent" type="button" :disabled="saving || !plan?.changed" @click="save">
          {{ saving ? 'Сохраняем…' : 'Сохранить' }}
        </button>
      </template>
    </template>
  </Modal>
</template>

<script setup>
// Смена регионов готовой кампании (все её группы) или одной группы: выбор → «было → станет» → запись в Директ
// через журнал с откатом.
import { computed, ref, watch } from 'vue'
import Modal from './ui/Modal.vue'
import UiNotice from './ui/UiNotice.vue'
import RegionPicker from './RegionPicker.vue'
import { errorText, webApi } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { undoAction } from '../composables/useUndo'
import { plural } from '../utils/format'

const props = defineProps({
  open: { type: Boolean, default: false },
  campaignId: { type: Number, required: true },
  // Группа { id, name } — меняем только её; без группы — все группы кампании.
  group: { type: Object, default: null },
})
const emit = defineEmits(['close', 'done'])

const overview = ref(null)
const value = ref([])
const loading = ref(false)
const loadError = ref('')
const step = ref('pick')
const plan = ref(null)
const checking = ref(false)
const saving = ref(false)
const formError = ref('')

const title = computed(() => (props.group ? `Регионы группы «${props.group.name}»` : 'Регионы кампании'))
const groupsCount = computed(() => overview.value?.groups?.length || 0)

watch(
  () => props.open,
  (open) => {
    if (open) load()
  },
  { immediate: true },
)

async function load() {
  step.value = 'pick'
  plan.value = null
  formError.value = ''
  loadError.value = ''
  loading.value = true
  try {
    const data = await webApi.campaignRegions(props.campaignId)
    overview.value = data
    const own = props.group ? (data.groups || []).find((g) => g.id === props.group.id) : null
    value.value = [...(props.group ? own?.region_ids || [] : data.region_ids || [])]
  } catch (err) {
    loadError.value = errorText(err)
  } finally {
    loading.value = false
  }
}

function send(dryRun) {
  return props.group
    ? webApi.setAdGroupRegions(props.group.id, value.value, dryRun)
    : webApi.setCampaignRegions(props.campaignId, value.value, dryRun)
}

async function check() {
  formError.value = ''
  if (!value.value.some((id) => id > 0)) {
    formError.value = 'Выберите хотя бы один регион показа'
    return
  }
  checking.value = true
  try {
    plan.value = await send(true)
    step.value = 'confirm'
  } catch (err) {
    formError.value = errorText(err)
  } finally {
    checking.value = false
  }
}

async function save() {
  formError.value = ''
  saving.value = true
  try {
    const res = await send(false)
    const failed = (res.errors || []).length
    const done = () => emit('done')
    if (failed) {
      pushToast(
        `Регионы обновлены в ${res.updated} из ${res.groups} ${plural(res.groups, 'группы', 'групп', 'групп')}. ` +
          `Не обновились: ${res.errors[0]}${failed > 1 ? ` и ещё ${failed - 1}` : ''}`,
        'warn',
        undoAction(res, done),
      )
    } else {
      pushToast(props.group ? 'Регионы группы обновлены' : 'Регионы кампании обновлены', 'ok', undoAction(res, done))
    }
    emit('done')
    emit('close')
  } catch (err) {
    formError.value = errorText(err)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.rdlg__diff {
  display: grid;
  gap: 0.6rem;
  margin: 0 0 0.9rem;
}

.rdlg__diff div {
  display: grid;
  grid-template-columns: 5rem 1fr;
  gap: 0.6rem;
  align-items: baseline;
}

.rdlg__diff dt {
  color: var(--muted);
  font-size: 0.84rem;
}

.rdlg__diff dd {
  margin: 0;
}

.rdlg__count {
  margin: 0 0 0.6rem;
  font-weight: 560;
}

.rdlg__groups {
  display: grid;
  gap: 0.3rem;
  margin: 0 0 0.9rem;
  padding-left: 1.1rem;
}

.rdlg__groups span {
  color: var(--muted);
}
</style>
