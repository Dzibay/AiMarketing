<template>
  <div class="campaign-structure">
    <div class="ws-toolbar">
      <p class="sync-status">
        <template v-if="syncing">Синк структуры: {{ structureProgress || '…' }}</template>
        <template v-else-if="campaign?.synced_at">Структура загружена {{ formatDateTime(campaign.synced_at) }}.</template>
        <template v-else>Структура ещё не загружалась из Директа.</template>
      </p>
      <button class="btn btn--ghost" type="button" :disabled="syncing" @click="syncStructure">
        {{ syncing ? 'Загружаем…' : 'Обновить группы, объявления и фразы' }}
      </button>
    </div>

    <section class="panel">
      <div class="panel__head">
        <div>
          <h2>Группы объявлений</h2>
          <p class="hint">Выберите группу — объявления и фразы ниже отфильтруются по ней.</p>
        </div>
        <form class="filters filters--inline" @submit.prevent="createGroup">
          <input v-model.trim="groupForm.name" type="text" placeholder="Название новой группы" maxlength="255" />
          <button class="btn btn--accent" type="submit" :disabled="busy || !groupForm.name">Создать группу</button>
        </form>
      </div>
      <div v-if="adgroups.length" class="list">
        <div
          class="list-row is-link"
          :class="{ 'is-selected': !selectedGroup }"
          role="button"
          tabindex="0"
          @click="selectedGroup = null"
          @keydown.enter="selectedGroup = null"
        >
          <div>
            <strong>Все группы</strong>
            <small>{{ ads.length }} объявл. · {{ keywords.length }} фраз</small>
          </div>
        </div>
        <div
          v-for="g in adgroups"
          :key="g.id"
          class="list-row is-link"
          :class="{ 'is-selected': selectedGroup === g.id }"
          role="button"
          tabindex="0"
          @click="selectedGroup = g.id"
          @keydown.enter="selectedGroup = g.id"
        >
          <div>
            <strong>{{ g.name }}</strong>
            <small>
              {{ g.status ? CAMPAIGN_STATUSES[g.status] || g.status : '—' }} · ID {{ g.adgroup_id }}
              <template v-if="g.period_clicks || g.period_impressions">
                · {{ num(g.period_clicks) }} кликов · {{ rub(g.period_cost_kopecks) }}
                <template v-if="g.period_conversions"> · {{ num(g.period_conversions) }} конв.</template>
              </template>
            </small>
          </div>
          <div class="row-actions">
            <button class="btn btn--ghost" type="button" @click.stop="renameGroup(g)">Переименовать</button>
            <button class="btn btn--ghost btn--danger" type="button" @click.stop="removeGroup(g)">Удалить</button>
          </div>
        </div>
      </div>
      <p v-else class="empty">Групп нет — создайте первую или обновите структуру из Директа.</p>
    </section>

    <section v-if="selectedGroupRow" class="panel">
      <h2>Минус-фразы группы «{{ selectedGroupRow.name }}»</h2>
      <p class="hint">По одной фразе на строку. Пустой список очищает минус-фразы группы в Директе.</p>
      <div v-field class="field">
        <textarea v-model="negForm.text" rows="5" placeholder="бесплатно&#10;скачать" />
      </div>
      <div class="form-actions">
        <button class="btn btn--accent" type="button" :disabled="busy" @click="saveNegatives">Сохранить минус-фразы</button>
      </div>
    </section>

    <section class="panel">
      <div class="panel__head">
        <h2>Объявления{{ selectedGroupRow ? ` · ${selectedGroupRow.name}` : '' }}</h2>
        <button class="btn btn--accent" type="button" :disabled="!adgroups.length" @click="openAdForm(null)">
          Новое объявление
        </button>
      </div>
      <div v-if="selectedAds.size" class="bulk-bar">
        <span>Выбрано: {{ selectedAds.size }}</span>
        <button class="btn btn--ghost" type="button" :disabled="busy" @click="adAction('suspend')">Остановить</button>
        <button class="btn btn--ghost" type="button" :disabled="busy" @click="adAction('resume')">Запустить</button>
        <button class="btn btn--ghost" type="button" :disabled="busy" @click="adAction('unarchive')">Из архива</button>
        <button class="btn btn--ghost" type="button" :disabled="busy" @click="adAction('archive')">В архив</button>
        <button class="btn btn--ghost btn--danger" type="button" :disabled="busy" @click="adAction('delete')">
          Удалить
        </button>
      </div>
      <div v-if="filteredAds.length" class="list">
        <div v-for="ad in filteredAds" :key="ad.id" class="list-row">
          <label class="check-row">
            <input type="checkbox" :checked="selectedAds.has(ad.id)" @change="toggle(selectedAds, ad.id, $event.target.checked)" />
            <div class="ad-preview">
              <strong>{{ ad.title }}<template v-if="ad.title2"> — {{ ad.title2 }}</template></strong>
              <span>{{ ad.text }}</span>
              <small v-if="variantsLabel(ad)">{{ variantsLabel(ad) }}</small>
              <small>
                {{ displayHost(ad.href) }} · {{ CAMPAIGN_STATES[ad.state] || ad.state || '—' }} /
                {{ CAMPAIGN_STATUSES[ad.status] || ad.status || '—' }} · {{ ad.adgroup_name }}
              </small>
            </div>
          </label>
          <button class="btn btn--ghost" type="button" @click="openAdForm(ad)">Изменить</button>
        </div>
      </div>
      <p v-else class="empty">Объявлений нет.</p>
    </section>

    <section class="panel">
      <div class="panel__head">
        <h2>Ключевые фразы{{ selectedGroupRow ? ` · ${selectedGroupRow.name}` : '' }}</h2>
        <div class="row-actions">
          <input v-model.trim="kwSearch" type="search" class="search-input" placeholder="Найти фразу" />
          <button class="btn btn--ghost" type="button" :disabled="!filteredKeywords.length" @click="exportKeywordsCsv">
            CSV
          </button>
        </div>
      </div>
      <form class="form-grid" @submit.prevent="createKeywords">
        <div v-field class="field">
          <label>Группа</label>
          <select v-model="kwForm.adgroup_row_id" required>
            <option value="">Выберите</option>
            <option v-for="g in adgroups" :key="g.id" :value="String(g.id)">{{ g.name }}</option>
          </select>
        </div>
        <div v-if="!autoBids" v-field class="field">
          <label>Ставка, ₽</label>
          <input v-model="kwForm.bid_rub" type="number" min="0" step="0.01" placeholder="по умолчанию" />
        </div>
        <div v-field class="field field--wide">
          <label>Новые фразы — по одной на строку</label>
          <textarea v-model="kwForm.text" rows="3" placeholder="купить слона&#10;слоны москва -бесплатно" />
        </div>
        <div class="form-actions">
          <button class="btn btn--accent" type="submit" :disabled="busy">Добавить фразы</button>
        </div>
      </form>
      <div v-if="selectedKeywords.size" class="bulk-bar">
        <span>Выбрано: {{ selectedKeywords.size }}</span>
        <button class="btn btn--ghost" type="button" :disabled="busy" @click="keywordAction('suspend')">Остановить</button>
        <button class="btn btn--ghost" type="button" :disabled="busy" @click="keywordAction('resume')">Запустить</button>
        <button class="btn btn--ghost btn--danger" type="button" :disabled="busy" @click="keywordAction('delete')">
          Удалить
        </button>
      </div>
      <div v-if="filteredKeywords.length" class="table-scroll">
        <table class="table">
          <thead>
            <tr>
              <th class="check-col">
                <input
                  type="checkbox"
                  :checked="filteredKeywords.every((k) => selectedKeywords.has(k.id))"
                  aria-label="Выбрать все"
                  @change="toggleAll(selectedKeywords, filteredKeywords, $event.target.checked)"
                />
              </th>
              <th>Фраза</th>
              <th>Статус</th>
              <th class="num">Ставка</th>
              <th class="num">Клики</th>
              <th class="num">Расход</th>
              <th class="num">Конв.</th>
              <th class="num">CPA</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="kw in filteredKeywords" :key="kw.id" :class="{ 'is-off': kw.state && kw.state !== 'ON' }">
              <td class="check-col">
                <input
                  type="checkbox"
                  :checked="selectedKeywords.has(kw.id)"
                  @change="toggle(selectedKeywords, kw.id, $event.target.checked)"
                />
              </td>
              <td>
                {{ kw.keyword }}
                <small class="cell-note">{{ kw.adgroup_name }}</small>
              </td>
              <td :title="keywordState(kw).hint">{{ keywordState(kw).text }}</td>
              <td class="num" :title="autoBids ? 'Автостратегия: ставки фраз назначает Директ' : ''">
                {{ autoBids ? 'авто' : kw.bid_rub != null ? rubles(kw.bid_rub, 2) : '—' }}
              </td>
              <td class="num">{{ num(kw.period_clicks) }}</td>
              <td class="num">{{ rub(kw.period_cost_kopecks) }}</td>
              <td class="num" :class="{ 'tone-bad': !kw.period_conversions && kw.period_cost_kopecks > 0 }">
                {{ num(kw.period_conversions) }}
              </td>
              <td class="num">{{ kw.period_cpa_rub != null ? rubles(kw.period_cpa_rub) : '—' }}</td>
              <td class="row-actions">
                <button v-if="!autoBids" class="btn btn--ghost" type="button" @click="editKeywordBid(kw)">Ставка</button>
                <button v-if="kw.keyword !== '---autotargeting'" class="btn btn--ghost" type="button" @click="editKeywordText(kw)">
                  Текст
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="empty">{{ keywords.length ? 'По фильтру ничего.' : 'Ключевых фраз нет.' }}</p>
    </section>

    <section class="panel">
      <div class="panel__head">
        <div>
          <h2>Поисковые запросы</h2>
          <p class="hint">
            Отчёт Директа по запросам
            <template v-if="searchMeta.date_from">
              за {{ formatDate(searchMeta.date_from) }} — {{ formatDate(searchMeta.date_to) }}
            </template>
            <template v-if="searchMeta.total != null"> · всего {{ num(searchMeta.total) }}</template>.
            Мусорные — в минус-фразы, удачные — в ключи.
          </p>
        </div>
        <button class="btn btn--ghost" type="button" :disabled="busy || syncingQueries" @click="syncSearchQueries">
          {{ syncingQueries ? `Загружаем: ${queriesProgress || '…'}` : `Загрузить за ${searchForm.days} дн.` }}
        </button>
      </div>
      <form class="filters" @submit.prevent="loadSearchQueries">
        <div v-field class="field">
          <label>Дней в отчёте</label>
          <input v-model.number="searchForm.days" type="number" min="1" max="90" />
        </div>
        <div v-field class="field field--wide">
          <label>Поиск</label>
          <input v-model="searchForm.search" type="search" placeholder="часть запроса" />
        </div>
        <div v-field class="field">
          <label>Мин. кликов</label>
          <input v-model="searchForm.min_clicks" type="number" min="0" placeholder="0" />
        </div>
        <div v-field class="field">
          <label>Мин. расход, ₽</label>
          <input v-model="searchForm.min_cost_rub" type="number" min="0" step="1" placeholder="0" />
        </div>
        <label class="check-row">
          <input v-model="searchForm.no_conversions" type="checkbox" />
          Только без конверсий
        </label>
        <button class="btn btn--ghost" type="submit" :disabled="busy">Показать</button>
      </form>

      <div v-if="selectedQueries.size" class="bulk-bar">
        <span>Выбрано: {{ selectedQueries.size }}</span>
        <button class="btn btn--ghost" type="button" :disabled="busy" @click="bulkQueryAction('add_keyword')">В ключи</button>
        <button class="btn btn--ghost" type="button" :disabled="busy" @click="bulkQueryAction('negative_adgroup')">
          Минус в группу
        </button>
        <button class="btn btn--ghost" type="button" :disabled="busy" @click="bulkQueryAction('negative_campaign')">
          Минус в кампанию
        </button>
        <button class="btn btn--ghost" type="button" @click="selectedQueries = new Set()">Снять выбор</button>
      </div>

      <div v-if="searchQueries.length" class="table-scroll">
        <table class="table">
          <thead>
            <tr>
              <th class="check-col">
                <input
                  type="checkbox"
                  :checked="searchQueries.every((q) => selectedQueries.has(q.id))"
                  aria-label="Выбрать все"
                  @change="toggleAll(selectedQueries, searchQueries, $event.target.checked)"
                />
              </th>
              <th>Запрос</th>
              <th class="num">Показы</th>
              <th class="num">Клики</th>
              <th class="num">Расход</th>
              <th class="num">Конв.</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="q in searchQueries" :key="q.id">
              <td class="check-col">
                <input
                  type="checkbox"
                  :checked="selectedQueries.has(q.id)"
                  @change="toggle(selectedQueries, q.id, $event.target.checked)"
                />
              </td>
              <td>
                {{ q.query }}
                <small class="cell-note">
                  {{ q.adgroup_name || 'без группы' }}<template v-if="q.matched_keyword"> · ключ «{{ q.matched_keyword }}»</template>
                </small>
              </td>
              <td class="num">{{ num(q.impressions) }}</td>
              <td class="num">{{ num(q.clicks) }}</td>
              <td class="num">{{ rub(q.cost_kopecks) }}</td>
              <td class="num" :class="{ 'tone-bad': !q.conversions && q.cost_kopecks > 0 }">{{ num(q.conversions) }}</td>
              <td class="row-actions">
                <button class="btn btn--ghost" type="button" :disabled="busy" @click="addQueryKeyword(q)">В ключи</button>
                <button class="btn btn--ghost" type="button" :disabled="busy" @click="addQueryNegative(q, 'adgroup')">
                  −группа
                </button>
                <button class="btn btn--ghost" type="button" :disabled="busy" @click="addQueryNegative(q, 'campaign')">
                  −кампания
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="empty">Запросов нет — загрузите отчёт или ослабьте фильтры.</p>
    </section>

    <Modal :open="adModal.open" :title="adModal.item ? 'Изменить объявление' : 'Новое объявление'" :locked="busy" @close="adModal.open = false">
      <form id="ad-form" class="form-grid" @submit.prevent="saveAd">
        <div v-if="!adModal.item" v-field class="field field--wide">
          <label>Группа</label>
          <select v-model="adForm.adgroup_row_id" required>
            <option value="">Выберите</option>
            <option v-for="g in adgroups" :key="g.id" :value="String(g.id)">{{ g.name }}</option>
          </select>
        </div>
        <div v-field class="field field--wide">
          <label>Заголовок 1 <span class="field-hint">{{ adForm.title.length }}/56</span></label>
          <input v-model="adForm.title" type="text" maxlength="56" required />
        </div>
        <div v-if="!adResponsive" v-field class="field field--wide">
          <label>Заголовок 2 <span class="field-hint">{{ adForm.title2.length }}/30</span></label>
          <input v-model="adForm.title2" type="text" maxlength="30" />
        </div>
        <div v-else v-field class="field field--wide">
          <label>Ещё заголовки — по одному на строку, до 6 <span class="field-hint">до 56 символов</span></label>
          <textarea v-model="adForm.more_titles" rows="3"></textarea>
        </div>
        <div v-field class="field field--wide">
          <label>Текст <span class="field-hint">{{ adForm.text.length }}/81</span></label>
          <textarea v-model="adForm.text" rows="2" maxlength="81" required></textarea>
        </div>
        <div v-if="adResponsive" v-field class="field field--wide">
          <label>Ещё тексты — по одному на строку, до 2 <span class="field-hint">до 81 символа</span></label>
          <textarea v-model="adForm.more_texts" rows="2"></textarea>
        </div>
        <p v-if="adResponsive" class="field-hint field--wide">
          Комбинаторное объявление: Директ сам собирает показы из заголовков и текстов.
        </p>
        <div v-field class="field field--wide">
          <label>Ссылка</label>
          <input v-model="adForm.href" type="url" placeholder="https://…" required />
        </div>
        <p v-if="adExtrasState.loading" class="field-hint field--wide">Загружаем быстрые ссылки, уточнения и картинки…</p>
        <p v-else-if="adExtrasState.error" class="field-hint field--wide">
          Быстрые ссылки, уточнения и картинки не загрузились ({{ adExtrasState.error }}) — они останутся как есть.
        </p>
        <AdExtrasFields
          v-else
          :extras="adExtras"
          :account-id="adModal.item?.account_id || campaign?.account_id || null"
          :max-images="adExtrasState.maxImages"
          :disabled="busy"
        />
        <div class="ad-preview ad-preview--card field--wide">
          <strong>{{ adForm.title || 'Заголовок' }}<template v-if="adForm.title2"> — {{ adForm.title2 }}</template></strong>
          <small>{{ displayHost(adForm.href) || 'site.ru' }}</small>
          <span>{{ adForm.text || 'Текст объявления' }}</span>
        </div>
      </form>
      <template #footer>
        <button class="btn btn--ghost" type="button" :disabled="busy" @click="adModal.open = false">Отмена</button>
        <button class="btn btn--accent" type="submit" form="ad-form" :disabled="busy">
          {{ adModal.item ? 'Сохранить' : 'Создать' }}
        </button>
      </template>
    </Modal>
  </div>
</template>

<script setup>
import { askConfirm, askText } from '../composables/useConfirm'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import Modal from './ui/Modal.vue'
import AdExtrasFields from './AdExtrasFields.vue'
import { emptyExtras, extrasDiff, extrasEmpty, extrasFromApi, extrasPayload, extrasProblem } from '../utils/adExtras'
import { errorText, jobProgressText, webApi, wsApi } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { CAMPAIGN_STATES, CAMPAIGN_STATUSES, formatDate, formatDateTime, num, rub, rubles } from '../utils/format'

const props = defineProps({
  workspaceId: { type: Number, required: true },
  campaignId: { type: Number, required: true },
})
const emit = defineEmits(['loaded'])

const campaign = ref(null)
const adgroups = ref([])
const ads = ref([])
const keywords = ref([])
const searchQueries = ref([])
const searchMeta = reactive({ date_from: null, date_to: null, total: null })
const searchForm = reactive({ days: 14, search: '', min_clicks: '', min_cost_rub: '', no_conversions: false })
const syncing = ref(false)
const syncingQueries = ref(false)
const structureProgress = ref('')
const queriesProgress = ref('')
const busy = ref(false)
const selectedGroup = ref(null)
const selectedAds = ref(new Set())
const selectedKeywords = ref(new Set())
const selectedQueries = ref(new Set())
const kwSearch = ref('')
const groupForm = reactive({ name: '' })
const adModal = reactive({ open: false, item: null })
const adForm = reactive({
  adgroup_row_id: '',
  title: '',
  title2: '',
  text: '',
  more_titles: '',
  more_texts: '',
  href: '',
})
const adExtras = reactive(emptyExtras())
// before — расширения как в Директе: при правке отправляем только изменившиеся части.
const adExtrasState = reactive({ loading: false, error: '', before: emptyExtras(), maxImages: 1 })
// Ставки фраз Директ берёт только при ручной стратегии, при автостратегии назначает их сам.
// Если поиск выключен, ставками управляет стратегия в сетях.
const MANUAL_STRATEGIES = new Set(['', 'HIGHEST_POSITION', 'MAXIMUM_COVERAGE', 'SERVING_OFF', 'UNKNOWN'])
const autoBids = computed(() => {
  const camp = campaign.value
  if (!camp) return false
  const strategy = camp.strategy === 'SERVING_OFF' ? camp.network_strategy : camp.strategy
  return !MANUAL_STRATEGIES.has(strategy || '')
})
// Комбинаторное объявление: так Директ создаёт новые объявления в текстовых кампаниях и ЕПК.
const RESPONSIVE_CAMPAIGNS = new Set(['TEXT_CAMPAIGN', 'UNIFIED_CAMPAIGN'])
const adResponsive = computed(() =>
  adModal.item
    ? adModal.item.type === 'RESPONSIVE_AD'
    : RESPONSIVE_CAMPAIGNS.has(campaign.value?.campaign_type || campaign.value?.type),
)

// Состояние фразы с учётом кампании: у фраз черновика Директ всё равно пишет «ON».
function keywordState(kw) {
  const camp = campaign.value || {}
  if (kw.status === 'REJECTED') return { text: 'Отклонена', hint: '' }
  if (kw.state === 'SUSPENDED') return { text: 'Остановлена', hint: '' }
  if (kw.status === 'DRAFT' || camp.status === 'DRAFT') return { text: 'Черновик', hint: 'Кампания ещё не отправлена на модерацию' }
  if (kw.state === 'ON' && camp.state && camp.state !== 'ON') return { text: 'Включена', hint: 'Фраза включена, но кампания сейчас не показывается' }
  if (kw.state === 'OFF') return { text: 'Не показывается', hint: '' }
  return { text: CAMPAIGN_STATES[kw.state] || kw.state || '—', hint: '' }
}

function variantsLabel(ad) {
  const parts = []
  if ((ad.titles || []).length > 1) parts.push(`заголовков: ${ad.titles.length}`)
  if ((ad.texts || []).length > 1) parts.push(`текстов: ${ad.texts.length}`)
  if ((ad.image_hashes || []).length) parts.push(`картинок: ${ad.image_hashes.length}`)
  return parts.join(' · ')
}

function variantLines(text) {
  return String(text || '')
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
}
const kwForm = reactive({ adgroup_row_id: '', text: '', bid_rub: '' })
const negForm = reactive({ text: '' })

const selectedGroupRow = computed(() => adgroups.value.find((g) => g.id === selectedGroup.value) || null)
const filteredAds = computed(() =>
  selectedGroup.value ? ads.value.filter((a) => a.adgroup_row_id === selectedGroup.value) : ads.value,
)
const filteredKeywords = computed(() => {
  const q = kwSearch.value.toLowerCase()
  return keywords.value.filter(
    (k) => (!selectedGroup.value || k.adgroup_row_id === selectedGroup.value) && (!q || k.keyword.toLowerCase().includes(q)),
  )
})

watch(selectedGroup, (id) => {
  if (id) kwForm.adgroup_row_id = String(id)
  const group = adgroups.value.find((g) => g.id === id)
  negForm.text = (group?.negative_keywords || []).join('\n')
})

function toggle(setRef, id, on) {
  const next = new Set(setRef)
  if (on) next.add(id)
  else next.delete(id)
  if (setRef === selectedAds.value) selectedAds.value = next
  else if (setRef === selectedKeywords.value) selectedKeywords.value = next
  else selectedQueries.value = next
}

function toggleAll(setRef, rows, on) {
  const next = on ? new Set(rows.map((r) => r.id)) : new Set()
  if (setRef === selectedKeywords.value) selectedKeywords.value = next
  else selectedQueries.value = next
}

function displayHost(href) {
  try {
    return new URL(href).host.replace(/^www\./, '')
  } catch {
    return href || ''
  }
}

async function run(fn) {
  busy.value = true
  try {
    await fn()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function load() {
  try {
    const data = await wsApi.campaign(props.workspaceId, props.campaignId)
    campaign.value = data.campaign
    adgroups.value = data.adgroups || []
    ads.value = data.ads || []
    keywords.value = data.keywords || []
    emit('loaded', data.campaign)
    selectedAds.value = new Set()
    selectedKeywords.value = new Set()
    if (selectedGroup.value) {
      const group = adgroups.value.find((g) => g.id === selectedGroup.value)
      if (!group) selectedGroup.value = null
      else negForm.text = (group.negative_keywords || []).join('\n')
    }
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

async function loadSearchQueries() {
  try {
    const data = await webApi.searchQueries(props.campaignId, {
      limit: 200,
      search: searchForm.search.trim(),
      min_clicks: searchForm.min_clicks === '' ? null : Number(searchForm.min_clicks),
      min_cost_rub: searchForm.min_cost_rub === '' ? null : Number(searchForm.min_cost_rub),
      no_conversions: !!searchForm.no_conversions,
    })
    searchQueries.value = data.items || []
    searchMeta.date_from = data.date_from
    searchMeta.date_to = data.date_to
    searchMeta.total = data.total
    selectedQueries.value = new Set()
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

async function syncStructure() {
  syncing.value = true
  try {
    const res = await webApi.syncCampaignStructure(props.campaignId, (job) => {
      structureProgress.value = jobProgressText(job)
    })
    pushToast(`Групп: ${res.adgroups?.adgroups ?? 0}, объявлений: ${res.ads?.ads ?? 0}, фраз: ${res.keywords?.keywords ?? 0}`)
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    syncing.value = false
    structureProgress.value = ''
  }
}

async function syncSearchQueries() {
  syncingQueries.value = true
  try {
    const res = await webApi.syncSearchQueries(props.campaignId, { days: Number(searchForm.days) || 14 }, (job) => {
      queriesProgress.value = jobProgressText(job)
    })
    const q = res.queries?.queries ?? res.queries ?? 0
    pushToast(`Загружено запросов: ${q}`)
    await Promise.all([load(), loadSearchQueries()])
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    syncingQueries.value = false
    queriesProgress.value = ''
  }
}

function bulkQueryAction(action) {
  const ids = [...selectedQueries.value]
  if (!ids.length) return
  const payload = { query_ids: ids, action }
  if (action === 'add_keyword' && selectedGroup.value) payload.adgroup_row_id = Number(selectedGroup.value)
  return run(async () => {
    const res = await webApi.searchQueryActions(payload)
    pushToast(`Готово: ${res.applied || 0}${res.skipped ? `, пропущено: ${res.skipped}` : ''}`)
    await Promise.all([load(), loadSearchQueries()])
  })
}

function addQueryKeyword(q) {
  const adgroupId = q.adgroup_row_id || selectedGroup.value
  if (!adgroupId) {
    pushToast('Выберите группу — у запроса её нет', 'error')
    return
  }
  return run(async () => {
    await webApi.searchQueryAddKeyword(q.id, { adgroup_row_id: Number(adgroupId) })
    pushToast('Добавлено в ключевые фразы')
    await Promise.all([load(), loadSearchQueries()])
  })
}

function addQueryNegative(q, scope) {
  if (scope === 'adgroup' && !q.adgroup_row_id) {
    pushToast('У запроса нет группы — добавьте минус-фразу в кампанию', 'error')
    return
  }
  return run(async () => {
    await webApi.searchQueryAddNegative(q.id, { scope })
    pushToast(scope === 'campaign' ? 'Добавлено в минус-фразы кампании' : 'Добавлено в минус-фразы группы')
    await Promise.all([load(), loadSearchQueries()])
  })
}

function createGroup() {
  if (!groupForm.name) return
  return run(async () => {
    const hadGroups = adgroups.value.length > 0
    await webApi.createAdGroup(props.campaignId, { name: groupForm.name })
    groupForm.name = ''
    pushToast(
      hadGroups
        ? 'Группа создана с регионами первой группы кампании (поменять можно в Директе)'
        : 'Группа создана (регион — Россия, поменять можно в Директе)',
    )
    await load()
  })
}

async function renameGroup(g) {
  const name = await askText('Название группы', { title: 'Переименовать группу', value: g.name, required: true })
  if (!name || !name.trim() || name.trim() === g.name) return
  return run(async () => {
    await webApi.patchAdGroup(g.id, { name: name.trim() })
    await load()
  })
}

async function removeGroup(g) {
  if (!await askConfirm(`Удалить группу «${g.name}» в Директе вместе с объявлениями и фразами?`)) return
  return run(async () => {
    await webApi.deleteAdGroups([g.id])
    if (selectedGroup.value === g.id) selectedGroup.value = null
    pushToast('Группа удалена')
    await load()
  })
}

function saveNegatives() {
  if (!selectedGroup.value) return
  return run(async () => {
    await webApi.patchAdGroup(selectedGroup.value, { negative_text: negForm.text })
    pushToast('Минус-фразы группы сохранены')
    await load()
  })
}

function openAdForm(ad) {
  adModal.item = ad
  adForm.adgroup_row_id = ad ? String(ad.adgroup_row_id) : selectedGroup.value ? String(selectedGroup.value) : ''
  adForm.title = ad?.titles?.[0] || ad?.title || ''
  adForm.title2 = ad?.title2 || ''
  adForm.text = ad?.texts?.[0] || ad?.text || ''
  adForm.more_titles = (ad?.titles || []).slice(1).join('\n')
  adForm.more_texts = (ad?.texts || []).slice(1).join('\n')
  adForm.href = ad?.href || ''
  Object.assign(adExtras, emptyExtras())
  Object.assign(adExtrasState, { loading: !!ad, error: '', before: emptyExtras(), maxImages: adResponsive.value ? 5 : 1 })
  adModal.open = true
  if (ad) loadAdExtras(ad)
}

async function loadAdExtras(ad) {
  try {
    const data = await webApi.adExtras(ad.id)
    if (adModal.item !== ad) return
    Object.assign(adExtras, extrasFromApi(data))
    Object.assign(adExtrasState, { before: extrasFromApi(data), maxImages: data.max_images || 1 })
  } catch (err) {
    if (adModal.item === ad) adExtrasState.error = errorText(err)
  } finally {
    if (adModal.item === ad) adExtrasState.loading = false
  }
}

function saveAd() {
  const editable = !adExtrasState.loading && !adExtrasState.error
  // При правке расширения уходят, только если их меняли, — нетронутые из Директа не проверяем.
  const changed = editable && adModal.item ? extrasDiff(adExtrasState.before, adExtras) : null
  const checked = editable && (!adModal.item || changed)
  const problem = checked ? extrasProblem(adExtras, adExtrasState.maxImages) : ''
  if (problem) {
    pushToast(problem, 'error')
    return
  }
  return run(async () => {
    const fields = adResponsive.value
      ? {
          // Лишние варианты не отрезаем: Директ их не примет, и сервер скажет, сколько можно.
          titles: [adForm.title, ...variantLines(adForm.more_titles)],
          texts: [adForm.text, ...variantLines(adForm.more_texts)],
          href: adForm.href,
        }
      : { title: adForm.title, title2: adForm.title2, text: adForm.text, href: adForm.href }
    if (changed) fields.extras = changed
    else if (editable && !adModal.item && !extrasEmpty(adExtras)) fields.extras = extrasPayload(adExtras)
    if (adModal.item) await webApi.patchAd(adModal.item.id, fields)
    else await webApi.createAd({ adgroup_row_id: Number(adForm.adgroup_row_id), ...fields })
    pushToast(
      !adModal.item
        ? 'Объявление создано черновиком'
        : adModal.item.status === 'DRAFT'
          ? 'Черновик объявления обновлён'
          : 'Объявление обновлено — уйдёт на модерацию',
    )
    adModal.open = false
    await load()
  })
}

async function adAction(action) {
  const ids = [...selectedAds.value]
  if (!ids.length) return
  if (action === 'delete' && !await askConfirm(`Удалить объявлений: ${ids.length}?`)) return
  return run(async () => {
    const res = await webApi.adActions({ ad_ids: ids, action })
    pushToast(`Готово: ${res.applied}`)
    await load()
  })
}

function createKeywords() {
  if (!kwForm.adgroup_row_id || !kwForm.text.trim()) {
    pushToast('Выберите группу и впишите фразы', 'error')
    return
  }
  return run(async () => {
    const payload = { adgroup_row_id: Number(kwForm.adgroup_row_id), text: kwForm.text }
    if (kwForm.bid_rub !== '' && kwForm.bid_rub != null) payload.bid_rub = Number(kwForm.bid_rub)
    const res = await webApi.createKeywords(payload)
    kwForm.text = ''
    kwForm.bid_rub = ''
    pushToast(`Добавлено фраз: ${res.created || 0}`)
    await load()
  })
}

async function editKeywordBid(kw) {
  const raw = await askText('Ставка, ₽', {
    title: `Ставка фразы «${kw.keyword}»`,
    value: kw.bid_rub != null ? String(kw.bid_rub).replace('.', ',') : '',
    type: 'number',
    placeholder: 'например, 12,50',
    required: true,
  })
  if (raw == null || !String(raw).trim()) return
  const bid = Number(String(raw).replace(',', '.'))
  if (!(bid > 0)) {
    pushToast('Ставка должна быть больше нуля', 'error')
    return
  }
  return run(async () => {
    await webApi.patchKeyword(kw.id, { bid_rub: bid })
    pushToast('Ставка обновлена')
    await load()
  })
}

async function editKeywordText(kw) {
  const text = await askText('Ключевая фраза', { title: 'Изменить фразу', value: kw.keyword, required: true })
  if (text == null || !text.trim() || text.trim() === kw.keyword) return
  return run(async () => {
    await webApi.patchKeyword(kw.id, { keyword: text.trim() })
    pushToast('Фраза обновлена')
    await load()
  })
}

async function keywordAction(action) {
  const ids = [...selectedKeywords.value]
  if (!ids.length) return
  if (action === 'delete' && !await askConfirm(`Удалить фраз: ${ids.length}?`)) return
  return run(async () => {
    const res = await webApi.keywordActions({ keyword_ids: ids, action })
    pushToast(`Готово: ${res.applied}`)
    await load()
  })
}

function csvCell(value) {
  const text = String(value ?? '')
  return /[",\n;]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

function exportKeywordsCsv() {
  const rows = filteredKeywords.value
  if (!rows.length) return
  const lines = ['keyword;adgroup;state;status;bid_rub;clicks;cost_rub;conversions;keyword_id']
  for (const kw of rows) {
    lines.push(
      [
        kw.keyword,
        kw.adgroup_name,
        kw.state,
        kw.status,
        kw.bid_rub ?? '',
        kw.period_clicks ?? '',
        kw.period_cost_kopecks != null ? (kw.period_cost_kopecks / 100).toFixed(2) : '',
        kw.period_conversions ?? '',
        kw.keyword_id,
      ]
        .map(csvCell)
        .join(';'),
    )
  }
  const blob = new Blob([`﻿${lines.join('\n')}`], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `keywords_${campaign.value?.campaign_id || props.campaignId}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

defineExpose({ reload: load })

onMounted(() => {
  load()
  loadSearchQueries()
})
</script>
