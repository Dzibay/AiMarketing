<template>
  <PageHeader>
    <template v-if="data" #description>
      {{ formatDate(data.date_from) }} — {{ formatDate(data.date_to) }} · конверсии каждого проекта — по его цели
    </template>
    <template #actions>
      <div class="seg" role="group" aria-label="Период статистики">
        <button
          v-for="d in [7, 14, 30]"
          :key="d"
          type="button"
          :class="{ active: days === d }"
          :aria-pressed="days === d"
          @click="setDays(d)"
        >
          {{ d }} дней
        </button>
      </div>
      <template v-if="projects.length">
        <RouterLink v-if="projects.length === 1" class="btn btn--accent" :to="newCampaignLink(projects[0].id)">
          <Plus aria-hidden="true" />Новая кампания
        </RouterLink>
        <UiMenu v-else primary label="Новая кампания: в какой проект?">
          <template #trigger><Plus aria-hidden="true" />Новая кампания<ChevronDown aria-hidden="true" /></template>
          <UiMenuItem v-for="p in projects" :key="p.id" :to="newCampaignLink(p.id)">{{ niceQuotes(p.name) }}</UiMenuItem>
        </UiMenu>
      </template>
    </template>
  </PageHeader>

  <template v-if="!expert">
    <p v-if="loading && !data" class="empty">Загружаем кампании…</p>
    <EmptyState v-else-if="!data" :icon="CloudOff" :text="loadError || 'Не удалось загрузить кампании.'">
      <template #action><button class="btn btn--ghost" type="button" @click="load">Повторить</button></template>
    </EmptyState>
    <EmptyState v-else-if="!projects.length" :icon="Megaphone">
      Проектов пока нет. Подключите Директ и Метрику в
      <RouterLink to="/cabinet/connections">подключениях</RouterLink> — проекты появятся по счётчикам.
    </EmptyState>
    <EmptyState
      v-else-if="!simpleRows.length"
      :icon="Megaphone"
      text="В проектах нет кампаний. Нажмите «Новая кампания» — ИИ поможет собрать первую."
    />
    <CampaignGroups
      v-else
      :class="{ 'is-loading': loading }"
      :rows="simpleRows"
      :kpi-of="(c) => kpiByProject.get(c.project.id)"
      :busy="busy"
      :selected="selected"
      :days="days"
      @toggle="quickToggle"
      @pick="toggle"
    />
    <ExpertHint v-if="data && simpleRows.length">Фильтры, архив, массовые действия и таблица со всеми цифрами</ExpertHint>
    <CampaignAsk
      v-if="data && simpleRows.length"
      :rows="simpleRows"
      :selected="selected"
      @unselect="toggle($event, false)"
    />
  </template>

  <section v-else class="panel">
    <p v-if="loading && !data" class="empty">Загружаем кампании…</p>
    <EmptyState v-else-if="!data" :icon="CloudOff" :text="loadError || 'Не удалось загрузить кампании.'" plain>
      <template #action><button class="btn btn--ghost" type="button" @click="load">Повторить</button></template>
    </EmptyState>
    <EmptyState v-else-if="!projects.length" :icon="Megaphone" plain>
      Проектов пока нет. Подключите Директ и Метрику в
      <RouterLink to="/cabinet/connections">подключениях</RouterLink> — проекты появятся по счётчикам.
    </EmptyState>
    <EmptyState
      v-else-if="!data.campaigns.length"
      :icon="Megaphone"
      text="В проектах нет кампаний. Нажмите «Новая кампания» — ИИ поможет собрать первую."
      plain
    />
    <CampaignTable
      v-else
      v-model:selected="selected"
      :items="data.campaigns"
      :link-of="(c) => `/w/${c.project.id}/campaigns/${c.campaign_row_id}`"
      :busy="busy"
      :loading="loading"
      :scope="projectScope"
      show-project
      @toggle="quickToggle"
      @duplicate="openDuplicate"
      @archive="archiveOne"
      @unarchive="(c) => runBulk('unarchive', [c.campaign_row_id])"
    >
      <template #notices>
        <UiNotice v-if="bulkErrors.length" tone="warn" closable @close="bulkErrors = []">
          <strong>Не выполнено для {{ bulkErrors.length }}:</strong>
          <ul class="notice-list">
            <li v-for="e in bulkErrors" :key="e.campaign_row_id">
              {{ e.name || e.campaign_row_id }}<template v-if="e.client_login"> ({{ e.client_login }})</template>:
              {{ e.message || ERRORS[e.error] || e.error }}
            </li>
          </ul>
        </UiNotice>
      </template>
      <template #bulk>
        <button v-if="picked.canResume" class="btn btn--ghost btn--sm" type="button" :disabled="busy" @click="bulk('resume')">
          <Play aria-hidden="true" />Запустить
        </button>
        <button v-if="picked.canPause" class="btn btn--ghost btn--sm" type="button" :disabled="busy" @click="bulk('pause')">
          <Pause aria-hidden="true" />Остановить
        </button>
      </template>
    </CampaignTable>
  </section>

  <DuplicateDialog
    v-if="duplicate.workspace"
    :open="duplicate.open"
    :workspace="duplicate.workspace"
    :campaign="duplicate.campaign"
    @close="duplicate.open = false"
    @done="onDuplicated"
  />
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { ChevronDown, CloudOff, Megaphone, Pause, Play, Plus } from '@lucide/vue'
import { ERRORS, errorText, webApi, wsApi } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { undoAction } from '../composables/useUndo'
import { useWorkspaces } from '../composables/useWorkspaces'
import { useExpert } from '../composables/useExpert'
import { confirmCampaignArchive, confirmCampaignsBulk, confirmCampaignToggle } from '../utils/campaignConfirm'
import { campaignTypeShort, formatDate, lastDays, niceQuotes } from '../utils/format'
import { useHead } from '../utils/useHead'
import EmptyState from '../components/ui/EmptyState.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import UiMenu from '../components/ui/UiMenu.vue'
import UiMenuItem from '../components/ui/UiMenuItem.vue'
import UiNotice from '../components/ui/UiNotice.vue'
import CampaignGroups from '../components/CampaignGroups.vue'
import CampaignTable from '../components/CampaignTable.vue'
import CampaignAsk from '../components/CampaignAsk.vue'
import ExpertHint from '../components/ExpertHint.vue'
import DuplicateDialog from './workspace/DuplicateDialog.vue'

const { expert } = useExpert()

useHead('Кампании — Толк', true)

const ACTION_TEXT = { pause: 'Остановлено', resume: 'Запущено', archive: 'В архиве', unarchive: 'Возвращено из архива' }

const router = useRouter()
const { workspaces } = useWorkspaces()
const data = ref(null)
const loading = ref(false)
const loadError = ref('')
const days = ref(7)
const busy = ref(false)
const selected = ref(new Set())
const bulkErrors = ref([])
const duplicate = reactive({ open: false, campaign: null, workspace: null })
let seq = 0

const projects = computed(() => data.value?.projects || [])
const kpiByProject = computed(() => new Map(projects.value.map((p) => [p.id, p.kpi])))
const projectScope = computed(() => ({
  label: 'Проект',
  all: 'Все проекты',
  options: projects.value.map((p) => ({ value: String(p.id), label: niceQuotes(p.name) })),
  of: (c) => c.project.id,
}))

// Простой режим: фильтров нет, поэтому берём всё, кроме архива.
const simpleRows = computed(() =>
  (data.value?.campaigns || [])
    .filter((c) => c.state !== 'ARCHIVED')
    .map((c) => ({
      ...c,
      link: `/w/${c.project.id}/campaigns/${c.campaign_row_id}`,
      where: [campaignTypeShort(c), niceQuotes(c.project.name)].filter(Boolean).join(' · '),
    })),
)

// Что имеет смысл для выбранных: запустить нечего, если всё уже идёт; черновик Директ не запустит.
const picked = computed(() => {
  const items = (data.value?.campaigns || []).filter((c) => selected.value.has(c.campaign_row_id))
  return {
    canPause: items.some((c) => c.state === 'ON'),
    canResume: items.some((c) => c.state !== 'ON' && c.state !== 'ARCHIVED' && c.status !== 'DRAFT'),
  }
})

function newCampaignLink(projectId) {
  return { path: `/w/${projectId}/campaigns`, query: { new: 1 } }
}

function toggle(id, on) {
  const next = new Set(selected.value)
  if (on) next.add(id)
  else next.delete(id)
  selected.value = next
}

async function load() {
  const my = ++seq
  loading.value = true
  loadError.value = ''
  try {
    const res = await webApi.portfolio(lastDays(days.value))
    if (my !== seq) return
    data.value = res
    const ids = new Set(res.campaigns.map((c) => c.campaign_row_id))
    selected.value = new Set([...selected.value].filter((id) => ids.has(id)))
  } catch (err) {
    if (my === seq) loadError.value = errorText(err)
  } finally {
    if (my === seq) loading.value = false
  }
}

function setDays(d) {
  days.value = d
  load()
}

// Действия идут через проект кампании — так они попадают в историю этого проекта.
async function runBulk(action, ids = [...selected.value]) {
  if (!ids.length) return
  const byProject = new Map()
  for (const c of data.value?.campaigns || []) {
    if (!ids.includes(c.campaign_row_id)) continue
    if (!byProject.has(c.project.id)) byProject.set(c.project.id, [])
    byProject.get(c.project.id).push(c.campaign_row_id)
  }
  busy.value = true
  bulkErrors.value = []
  let applied = 0
  let last = null
  try {
    for (const [projectId, campaignIds] of byProject) {
      last = await wsApi.bulk(projectId, { campaign_ids: campaignIds, action })
      applied += last.applied || 0
      bulkErrors.value.push(...(last.errors || []))
    }
    pushToast(
      `${ACTION_TEXT[action]}: ${applied}${bulkErrors.value.length ? `, ошибок: ${bulkErrors.value.length}` : ''}`,
      bulkErrors.value.length ? 'error' : 'ok',
      // Кампании из разных проектов — разные записи журнала: одной кнопкой их не отменить.
      byProject.size === 1 ? undoAction(last, load) : {},
    )
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function bulk(action) {
  if (!(await confirmCampaignsBulk(action, selected.value.size))) return
  return runBulk(action)
}

// Одна кампания из строки (выключатель или «⋯»): выбор не трогаем.
async function quickToggle(c) {
  if (!(await confirmCampaignToggle(c))) return
  return runBulk(c.state === 'ON' ? 'pause' : 'resume', [c.campaign_row_id])
}

async function archiveOne(c) {
  if (!(await confirmCampaignArchive(c))) return
  return runBulk('archive', [c.campaign_row_id])
}

function openDuplicate(c) {
  if (!c) return
  duplicate.workspace = workspaces.value.find((w) => w.id === c.project.id) || { id: c.project.id, name: c.project.name }
  duplicate.campaign = { id: c.campaign_row_id, name: c.name, account_id: c.account_id }
  duplicate.open = true
}

function onDuplicated(result) {
  duplicate.open = false
  const warnings = result.warnings || []
  pushToast(
    `Копия готова: групп ${result.groups ?? 0}, фраз ${result.keywords ?? 0}, объявлений ${result.ads ?? 0}` +
      (warnings.length ? `. Предупреждения: ${warnings.slice(0, 3).join('; ')}` : ''),
    warnings.length ? 'warn' : 'ok',
    undoAction(result, () => (router.currentRoute.value.path === '/cabinet/campaigns' ? load() : router.push('/cabinet/campaigns'))),
  )
  const target = result.target_workspace_id || duplicate.workspace?.id
  if (target && result.campaign?.id) {
    router.push(`/w/${target}/campaigns/${result.campaign.id}`)
    return
  }
  load()
}

onMounted(load)
</script>

<style scoped>
.notice-list {
  margin: 4px 0 0;
  padding-left: 18px;
}
</style>
