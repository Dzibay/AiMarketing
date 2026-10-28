<template>
  <section class="panel">
    <div class="panel__head">
      <div>
        <h2>Заголовки</h2>
        <p class="hint">
          Директ не отдаёт статистику по заголовкам, только по объявлениям. Точные цифры — у объявлений с одним
          заголовком (так ИИ и делает тесты) и из таблицы Мастера отчётов с группировкой «Заголовок». Лучшие —
          по конверсиям ключевой цели.
        </p>
      </div>
      <RouterLink class="btn btn--accent" :to="aiLink">Масштабировать с ИИ</RouterLink>
    </div>

    <details class="hl-upload">
      <summary>
        Отчёты Мастера: {{ reports.length }}
        <small class="hint">— загрузить CSV или XLSX с колонками «Заголовок», «Клики», «Конверсии»…</small>
      </summary>
      <form class="hl-upload__form" @submit.prevent="upload">
        <select v-model="uploadCampaign">
          <option :value="null">Кампании — из колонки «№ кампании» или все</option>
          <option v-for="c in campaigns" :key="c.campaign_row_id" :value="c.campaign_row_id">{{ c.name }}</option>
        </select>
        <input ref="fileInput" type="file" accept=".csv,.xlsx,.txt" @change="file = $event.target.files[0] || null" />
        <button class="btn btn--ghost" type="submit" :disabled="!file || uploading">
          {{ uploading ? 'Загружаем…' : 'Загрузить' }}
        </button>
      </form>
      <p v-if="uploadError" class="form-error">{{ uploadError }}</p>
      <ul v-if="reports.length" class="hl-reports">
        <li v-for="r in reports" :key="r.id">
          <span>
            {{ r.filename || 'без имени' }} · {{ num(r.headlines) }} {{ plural(r.headlines, 'заголовок', 'заголовка', 'заголовков') }},
            {{ num(r.conversions) }} конв.
            <template v-if="r.campaign_name"> · {{ r.campaign_name }}</template>
            <small class="cell-note">{{ formatDateTime(r.uploaded_at) }}</small>
          </span>
          <button class="btn btn--ghost" type="button" @click="remove(r)">Удалить</button>
        </li>
      </ul>
      <p class="hint">
        Кампания из загруженного отчёта считается только по нему — за период выгрузки, без двойного счёта с
        объявлениями.
      </p>
    </details>

    <template v-if="summary">
      <div v-if="!summary.headlines.length" class="empty">
        Нет заголовков с точной статистикой: в выбранных кампаниях объявления с несколькими заголовками. Загрузите отчёт
        Мастера или запустите тест, где в объявлении один заголовок.
      </div>
      <div v-else class="table-scroll">
        <table class="table">
          <thead>
            <tr>
              <th class="num">#</th>
              <th>Заголовок</th>
              <th class="num">Конв.</th>
              <th class="num">CR</th>
              <th class="num">CPA</th>
              <th class="num">Клики</th>
              <th class="num">CTR</th>
              <th class="num">Расход</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="h in shownHeadlines" :key="h.title" :class="{ 'is-thin': !h.enough }">
              <td class="num">{{ h.rank }}</td>
              <td class="hl-title">
                {{ h.title }}
                <span v-if="!h.enough" class="chip" title="Меньше 3 конверсий и 100 кликов">мало данных</span>
                <span v-if="h.sources.includes('report')" class="chip">отчёт</span>
                <small v-if="h.ads.length" class="cell-note">
                  {{ h.ads.length }} {{ plural(h.ads.length, 'объявление', 'объявления', 'объявлений') }}:
                  {{ [...new Set(h.ads.map((a) => a.adgroup_name))].slice(0, 3).join(', ') }}
                </small>
              </td>
              <td class="num"><b>{{ num(h.conversions) }}</b></td>
              <td class="num">{{ h.cr != null ? pct(h.cr, 2) : '—' }}</td>
              <td class="num">{{ h.cpa_rub != null ? rub(h.cpa_rub * 100) : '—' }}</td>
              <td class="num">{{ num(h.clicks) }}</td>
              <td class="num">{{ h.ctr != null ? pct(h.ctr, 2) : '—' }}</td>
              <td class="num">{{ rub(h.cost_kopecks) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <button
        v-if="summary.headlines.length > LIMIT"
        class="btn btn--ghost"
        type="button"
        @click="showAll = !showAll"
      >
        {{ showAll ? 'Свернуть' : `Показать все (${summary.headlines.length})` }}
      </button>

      <details v-if="summary.mixed.length" class="hl-mixed">
        <summary>
          Комбинаторные объявления: {{ summary.mixed.length }} — цифры общие на все их заголовки, победителя внутри не
          видно
        </summary>
        <ul>
          <li v-for="m in summary.mixed.slice(0, 30)" :key="m.ad_row_id">
            <b>{{ num(m.conversions) }} конв.</b>, {{ num(m.clicks) }} кл.{{ m.cr != null ? `, CR ${pct(m.cr, 2)}` : '' }} ·
            {{ m.adgroup_name }}
            <small class="cell-note">{{ m.titles.join(' | ') }}</small>
          </li>
        </ul>
      </details>
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { errorText, wsApi } from '../../api/web'
import { askConfirm } from '../../composables/useConfirm'
import { pushToast } from '../../composables/useToasts'
import { readFileBase64 } from '../../utils/adExtras'
import { formatDateTime, num, pct, plural, rub } from '../../utils/format'
import { useWs } from './context'

const props = defineProps({
  // report.headlines из отчёта по объявлениям: {headlines, mixed}; null — отчёт ещё не строили.
  summary: { type: Object, default: null },
  campaigns: { type: Array, default: () => [] },
  pickedIds: { type: Array, default: () => [] },
})
const emit = defineEmits(['changed'])

const LIMIT = 15

const { ws } = useWs()

const reports = ref([])
const uploadCampaign = ref(null)
const file = ref(null)
const fileInput = ref(null)
const uploading = ref(false)
const uploadError = ref('')
const showAll = ref(false)

const shownHeadlines = computed(() => (showAll.value ? props.summary.headlines : props.summary.headlines.slice(0, LIMIT)))

const aiLink = computed(() => {
  const one = props.pickedIds.length === 1 ? props.campaigns.find((c) => c.campaign_row_id === props.pickedIds[0]) : null
  return {
    path: `/w/${ws.value.id}/chat`,
    query: { ask: 'headlines', cid: one ? String(one.campaign_row_id) : undefined, name: one?.name || undefined },
  }
})

async function loadReports() {
  try {
    reports.value = (await wsApi.headlineReports(ws.value.id)).items
  } catch (err) {
    uploadError.value = errorText(err)
  }
}

async function upload() {
  if (!file.value) return
  uploading.value = true
  uploadError.value = ''
  try {
    const data = await readFileBase64(file.value)
    const r = await wsApi.uploadHeadlineReport(ws.value.id, {
      filename: file.value.name,
      data,
      campaign_row_id: uploadCampaign.value,
    })
    pushToast(`Загружено заголовков: ${r.headlines}`, 'ok')
    file.value = null
    if (fileInput.value) fileInput.value.value = ''
    await loadReports()
    emit('changed')
  } catch (err) {
    uploadError.value = errorText(err)
  } finally {
    uploading.value = false
  }
}

async function remove(r) {
  const ok = await askConfirm(`Удалить загруженный отчёт «${r.filename || 'без имени'}»?`, {
    confirmText: 'Удалить',
    danger: true,
  })
  if (!ok) return
  try {
    await wsApi.deleteHeadlineReport(ws.value.id, r.id)
    await loadReports()
    emit('changed')
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

onMounted(loadReports)
</script>

<style scoped>
.panel__head {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  justify-content: space-between;
}
.hl-upload,
.hl-mixed {
  margin: 8px 0 12px;
}
.hl-upload summary,
.hl-mixed summary {
  cursor: pointer;
}
.hl-upload__form {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin: 8px 0;
}
.hl-reports {
  margin: 8px 0;
  padding: 0;
  list-style: none;
}
.hl-reports li {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  padding: 4px 0;
  border-bottom: 1px solid var(--line);
}
.hl-reports .cell-note,
.hl-title .cell-note,
.hl-mixed .cell-note {
  display: block;
}
.hl-title {
  min-width: 260px;
  max-width: 480px;
}
.is-thin td {
  color: var(--muted);
}
.hl-mixed ul {
  margin: 6px 0 0;
  padding-left: 18px;
}
</style>
