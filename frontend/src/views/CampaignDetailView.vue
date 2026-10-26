<template>
  <div class="site">
    <SiteHeader solid />
    <main class="cabinet">
      <div class="wrap">
        <div class="cabinet__head">
          <div>
            <p class="hint"><RouterLink to="/cabinet/campaigns">← Кампании</RouterLink></p>
            <h1>{{ campaign?.name || 'Кампания' }}</h1>
            <p v-if="campaign">
              ID {{ campaign.campaign_id }} · {{ campaign.client_login }} ·
              {{ campaign.state }} / {{ campaign.status }}
            </p>
          </div>
          <button class="btn btn--accent" type="button" :disabled="syncing" @click="syncStructure">
            {{ syncing ? `Синк: ${structureProgress || '…'}` : 'Синк структуры' }}
          </button>
        </div>
        <CabinetNav />

        <section v-if="campaign" class="panel">
          <h2>Минус-фразы кампании</h2>
          <p class="hint">Общие минус-фразы кампании (по одной на строку).</p>
          <div class="filters">
            <div class="field field--wide">
              <label>Минус-фразы</label>
              <textarea v-model="campNegForm.text" rows="4" placeholder="-бесплатно&#10;-скачать" />
            </div>
            <button class="btn btn--accent" type="button" :disabled="busy" @click="saveCampaignNegatives">
              Сохранить
            </button>
          </div>
        </section>

        <section class="panel">
          <h2>Группы объявлений</h2>
          <div class="filters">
            <div class="field">
              <label>Название группы</label>
              <input v-model="groupForm.name" type="text" placeholder="Группа 1" />
            </div>
            <button class="btn btn--accent" type="button" :disabled="busy" @click="createGroup">Создать группу</button>
          </div>
          <div v-if="adgroups.length" class="list">
            <div
              v-for="g in adgroups"
              :key="g.id"
              class="list-row"
              :class="{ 'is-selected': selectedGroup === g.id }"
              @click="selectedGroup = g.id"
            >
              <div>
                <strong>{{ g.name }}</strong>
                <small>
                  {{ g.type || 'TEXT' }} · {{ g.status || '—' }} · ID {{ g.adgroup_id }}
                  <template v-if="g.period_clicks || g.period_impressions">
                    · клики {{ g.period_clicks }} · {{ formatMoneyKopecks(g.period_cost_kopecks) }}
                    <template v-if="g.period_conversions"> · конв. {{ g.period_conversions }}</template>
                  </template>
                </small>
              </div>
              <div class="row-actions">
                <button class="btn btn--ghost" type="button" @click.stop="renameGroup(g)">Имя</button>
                <button class="btn btn--ghost" type="button" @click.stop="removeGroup(g)">Удалить</button>
              </div>
            </div>
          </div>
          <p v-else class="empty">Групп нет — создайте или синхронизируйте.</p>
        </section>

        <section v-if="selectedGroupRow" class="panel">
          <h2>Минус-фразы группы «{{ selectedGroupRow.name }}»</h2>
          <p class="hint">По одной фразе на строку. Пустой список очищает минус-фразы в Директе.</p>
          <div class="filters">
            <div class="field field--wide">
              <label>Минус-фразы</label>
              <textarea v-model="negForm.text" rows="5" placeholder="-бесплатно&#10;-скачать" />
            </div>
            <button class="btn btn--accent" type="button" :disabled="busy" @click="saveNegatives">
              Сохранить минус-фразы
            </button>
          </div>
        </section>

        <section class="panel">
          <h2>Объявления</h2>
          <div class="filters">
            <div class="field">
              <label>Группа</label>
              <select v-model="adForm.adgroup_row_id">
                <option value="">Выберите</option>
                <option v-for="g in adgroups" :key="g.id" :value="String(g.id)">{{ g.name }}</option>
              </select>
            </div>
            <div class="field">
              <label>Заголовок</label>
              <input v-model="adForm.title" type="text" maxlength="56" />
            </div>
            <div class="field">
              <label>Заголовок 2</label>
              <input v-model="adForm.title2" type="text" maxlength="30" />
            </div>
            <div class="field">
              <label>Текст</label>
              <input v-model="adForm.text" type="text" maxlength="81" />
            </div>
            <div class="field">
              <label>Ссылка</label>
              <input v-model="adForm.href" type="url" placeholder="https://…" />
            </div>
            <button class="btn btn--accent" type="button" :disabled="busy" @click="createAd">Создать объявление</button>
          </div>

          <div v-if="filteredAds.length" class="list">
            <div v-for="ad in filteredAds" :key="ad.id" class="list-row">
              <div class="check-row">
                <input
                  type="checkbox"
                  :checked="selectedAds.has(ad.id)"
                  @change="toggleAd(ad.id, $event.target.checked)"
                />
                <div>
                  <strong>{{ ad.title }}</strong>
                  <small>
                    {{ ad.text }} · {{ ad.state }} / {{ ad.status }} · {{ ad.adgroup_name }}
                    <br />
                    {{ ad.href }}
                  </small>
                </div>
              </div>
              <button class="btn btn--ghost" type="button" @click="editAd(ad)">Изменить</button>
            </div>
          </div>
          <p v-else class="empty">Объявлений нет.</p>

          <div v-if="selectedAds.size" class="bulk-bar">
            <span>Выбрано: {{ selectedAds.size }}</span>
            <button class="btn btn--ghost" type="button" @click="adAction('suspend')">Пауза</button>
            <button class="btn btn--ghost" type="button" @click="adAction('resume')">Запуск</button>
            <button class="btn btn--ghost" type="button" @click="adAction('archive')">Архив</button>
            <button class="btn btn--ghost" type="button" @click="adAction('delete')">Удалить</button>
          </div>
        </section>

        <section v-if="editAdItem" class="panel">
          <h2>Редактирование объявления</h2>
          <div class="filters">
            <div class="field">
              <label>Заголовок</label>
              <input v-model="editAdForm.title" type="text" maxlength="56" />
            </div>
            <div class="field">
              <label>Заголовок 2</label>
              <input v-model="editAdForm.title2" type="text" maxlength="30" />
            </div>
            <div class="field">
              <label>Текст</label>
              <input v-model="editAdForm.text" type="text" maxlength="81" />
            </div>
            <div class="field">
              <label>Ссылка</label>
              <input v-model="editAdForm.href" type="url" />
            </div>
            <button class="btn btn--accent" type="button" :disabled="busy" @click="saveAd">Сохранить</button>
            <button class="btn btn--ghost" type="button" @click="editAdItem = null">Отмена</button>
          </div>
        </section>

        <section class="panel">
          <h2>Ключевые фразы</h2>
          <div class="filters">
            <button
              class="btn btn--ghost"
              type="button"
              :disabled="!filteredKeywords.length"
              @click="exportKeywordsCsv"
            >
              CSV
            </button>
            <div class="field">
              <label>Группа</label>
              <select v-model="kwForm.adgroup_row_id">
                <option value="">Выберите</option>
                <option v-for="g in adgroups" :key="g.id" :value="String(g.id)">{{ g.name }}</option>
              </select>
            </div>
            <div class="field field--wide">
              <label>Фразы (по одной на строку)</label>
              <textarea v-model="kwForm.text" rows="4" placeholder="купить слона&#10;слоны москва" />
            </div>
            <div class="field">
              <label>Ставка, ₽</label>
              <input v-model="kwForm.bid_rub" type="number" min="0" step="0.01" placeholder="опционально" />
            </div>
            <button class="btn btn--accent" type="button" :disabled="busy" @click="createKeywords">
              Добавить фразы
            </button>
          </div>

          <div v-if="filteredKeywords.length" class="list">
            <div v-for="kw in filteredKeywords" :key="kw.id" class="list-row">
              <div class="check-row">
                <input
                  type="checkbox"
                  :checked="selectedKeywords.has(kw.id)"
                  @change="toggleKeyword(kw.id, $event.target.checked)"
                />
                <div>
                  <strong>{{ kw.keyword }}</strong>
                  <small>
                    {{ kw.state || '—' }} / {{ kw.status || '—' }} · {{ kw.adgroup_name }}
                    · ставка {{ kw.bid_rub != null ? `${kw.bid_rub} ₽` : '—' }}
                    <template v-if="kw.period_clicks || kw.period_impressions">
                      · клики {{ kw.period_clicks }} · {{ formatMoneyKopecks(kw.period_cost_kopecks) }}
                      <template v-if="kw.period_cpa_rub != null"> · CPA {{ kw.period_cpa_rub }} ₽</template>
                    </template>
                  </small>
                </div>
              </div>
              <div class="row-actions">
                <button class="btn btn--ghost" type="button" @click="editKeywordBid(kw)">Ставка</button>
                <button class="btn btn--ghost" type="button" @click="editKeywordText(kw)">Фраза</button>
              </div>
            </div>
          </div>
          <p v-else class="empty">Ключевых фраз нет.</p>

          <div v-if="selectedKeywords.size" class="bulk-bar">
            <span>Выбрано: {{ selectedKeywords.size }}</span>
            <button class="btn btn--ghost" type="button" @click="keywordAction('suspend')">Пауза</button>
            <button class="btn btn--ghost" type="button" @click="keywordAction('resume')">Запуск</button>
            <button class="btn btn--ghost" type="button" @click="keywordAction('delete')">Удалить</button>
          </div>
        </section>

        <section class="panel">
          <h2>Поисковые запросы</h2>
          <p class="hint">
            Агрегат из отчёта Директа
            <template v-if="searchMeta.date_from">
              · {{ searchMeta.date_from }} — {{ searchMeta.date_to }}
            </template>
            <template v-if="searchMeta.total != null"> · всего {{ searchMeta.total }}</template>
          </p>
          <div class="filters">
            <div class="field">
              <label>Дней</label>
              <input v-model.number="searchForm.days" type="number" min="1" max="90" />
            </div>
            <div class="field">
              <label>Поиск</label>
              <input v-model="searchForm.search" type="search" placeholder="фраза…" @keyup.enter="loadSearchQueries" />
            </div>
            <div class="field">
              <label>Мин. клики</label>
              <input v-model="searchForm.min_clicks" type="number" min="0" placeholder="0" />
            </div>
            <div class="field">
              <label>Мин. расход ₽</label>
              <input v-model="searchForm.min_cost_rub" type="number" min="0" step="1" placeholder="0" />
            </div>
            <label class="check-row">
              <input v-model="searchForm.no_conversions" type="checkbox" />
              Без конверсий
            </label>
            <button class="btn btn--ghost" type="button" :disabled="busy" @click="loadSearchQueries">Найти</button>
            <button class="btn btn--accent" type="button" :disabled="busy || syncingQueries" @click="syncSearchQueries">
              {{ syncingQueries ? `Синк: ${queriesProgress || '…'}` : 'Синк статистики' }}
            </button>
          </div>

          <div v-if="selectedQueries.size" class="bulk-bar">
            <span>Выбрано: {{ selectedQueries.size }}</span>
            <button class="btn btn--ghost" type="button" @click="bulkQueryAction('add_keyword')">В ключи</button>
            <button class="btn btn--ghost" type="button" @click="bulkQueryAction('negative_adgroup')">− группа</button>
            <button class="btn btn--ghost" type="button" @click="bulkQueryAction('negative_campaign')">− кампания</button>
            <button class="btn btn--ghost" type="button" @click="selectedQueries = new Set()">Снять</button>
          </div>

          <div v-if="searchQueries.length" class="list">
            <div class="list-row">
              <div class="check-row">
                <input type="checkbox" :checked="allQueriesSelected" @change="toggleAllQueries($event.target.checked)" />
                <small>Выбрать все на странице</small>
              </div>
            </div>
            <div v-for="q in searchQueries" :key="q.id" class="list-row">
              <div class="check-row">
                <input
                  type="checkbox"
                  :checked="selectedQueries.has(q.id)"
                  @change="toggleQuery(q.id, $event.target.checked)"
                />
                <div>
                  <strong>{{ q.query }}</strong>
                  <small>
                    {{ q.adgroup_name || 'без группы' }}
                    <template v-if="q.matched_keyword"> · ключ «{{ q.matched_keyword }}»</template>
                    · клики {{ q.clicks }} · показы {{ q.impressions }}
                    · {{ formatMoneyKopecks(q.cost_kopecks) }}
                    <template v-if="q.cpc_rub != null"> · CPC {{ q.cpc_rub }} ₽</template>
                    <template v-if="q.conversions"> · конв. {{ q.conversions }}</template>
                    <template v-else> · без конв.</template>
                  </small>
                </div>
              </div>
              <div class="row-actions">
                <button class="btn btn--ghost" type="button" @click="addQueryKeyword(q)">В ключи</button>
                <button class="btn btn--ghost" type="button" @click="addQueryNegative(q, 'adgroup')">− группа</button>
                <button class="btn btn--ghost" type="button" @click="addQueryNegative(q, 'campaign')">− кампания</button>
              </div>
            </div>
          </div>
          <p v-else class="empty">Запросов нет — синхронизируйте отчёт или ослабьте фильтры.</p>
        </section>
      </div>
    </main>
    <SiteFooter />
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import CabinetNav from '../components/CabinetNav.vue'
import SiteFooter from '../components/SiteFooter.vue'
import SiteHeader from '../components/SiteHeader.vue'
import { errorText, formatMoneyKopecks, jobProgressText, webApi } from '../api/web'
import { getSession } from '../composables/useSession'
import { pushToast } from '../composables/useToasts'
import { useHead } from '../utils/useHead'

useHead('Кампания — AiMarketing', true)

const route = useRoute()
const router = useRouter()
const campaign = ref(null)
const adgroups = ref([])
const ads = ref([])
const keywords = ref([])
const searchQueries = ref([])
const searchMeta = reactive({ date_from: null, date_to: null, total: null })
const searchForm = reactive({
  days: 14,
  search: '',
  min_clicks: '',
  min_cost_rub: '',
  no_conversions: false,
})
const syncing = ref(false)
const syncingQueries = ref(false)
const structureProgress = ref('')
const queriesProgress = ref('')
const busy = ref(false)
const selectedGroup = ref(null)
const selectedAds = ref(new Set())
const selectedKeywords = ref(new Set())
const selectedQueries = ref(new Set())

const allQueriesSelected = computed(
  () => searchQueries.value.length > 0 && searchQueries.value.every((q) => selectedQueries.value.has(q.id)),
)
const editAdItem = ref(null)
const groupForm = reactive({ name: '' })
const adForm = reactive({
  adgroup_row_id: '',
  title: '',
  title2: '',
  text: '',
  href: '',
})
const editAdForm = reactive({ title: '', title2: '', text: '', href: '' })
const kwForm = reactive({
  adgroup_row_id: '',
  text: '',
  bid_rub: '',
})
const negForm = reactive({ text: '' })
const campNegForm = reactive({ text: '' })

const selectedGroupRow = computed(() => adgroups.value.find((g) => g.id === selectedGroup.value) || null)

const filteredAds = computed(() => {
  if (!selectedGroup.value) return ads.value
  return ads.value.filter((a) => a.adgroup_row_id === selectedGroup.value)
})

const filteredKeywords = computed(() => {
  if (!selectedGroup.value) return keywords.value
  return keywords.value.filter((k) => k.adgroup_row_id === selectedGroup.value)
})

watch(selectedGroup, (id) => {
  if (id) {
    adForm.adgroup_row_id = String(id)
    kwForm.adgroup_row_id = String(id)
  }
  const group = adgroups.value.find((g) => g.id === id)
  negForm.text = (group?.negative_keywords || []).join('\n')
})

async function load() {
  if (!getSession()) {
    router.replace('/login')
    return
  }
  try {
    const data = await webApi.campaignDetail(route.params.id)
    campaign.value = data.campaign
    adgroups.value = data.adgroups || []
    ads.value = data.ads || []
    keywords.value = data.keywords || []
    campNegForm.text = (data.campaign?.negative_keywords || []).join('\n')
    selectedAds.value = new Set()
    selectedKeywords.value = new Set()
    if (selectedGroup.value) {
      const group = adgroups.value.find((g) => g.id === selectedGroup.value)
      negForm.text = (group?.negative_keywords || []).join('\n')
      if (!group) selectedGroup.value = null
    }
    await loadSearchQueries()
  } catch (err) {
    pushToast(errorText(err), 'error')
    router.replace('/cabinet/campaigns')
  }
}

async function loadSearchQueries() {
  try {
    const data = await webApi.searchQueries(route.params.id, {
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

function toggleQuery(id, on) {
  const next = new Set(selectedQueries.value)
  if (on) next.add(id)
  else next.delete(id)
  selectedQueries.value = next
}

function toggleAllQueries(on) {
  selectedQueries.value = on ? new Set(searchQueries.value.map((q) => q.id)) : new Set()
}

async function bulkQueryAction(action) {
  const ids = [...selectedQueries.value]
  if (!ids.length) return
  const payload = { query_ids: ids, action }
  if (action === 'add_keyword') {
    if (selectedGroup.value) payload.adgroup_row_id = Number(selectedGroup.value)
  }
  busy.value = true
  try {
    const res = await webApi.searchQueryActions(payload)
    pushToast(`Готово: ${res.applied || 0}` + (res.skipped ? `, пропуск: ${res.skipped}` : ''))
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function syncSearchQueries() {
  syncingQueries.value = true
  try {
    const res = await webApi.syncSearchQueries(
      route.params.id,
      { days: Number(searchForm.days) || 14 },
      (job) => {
        queriesProgress.value = jobProgressText(job)
      },
    )
    const q = res.queries?.queries ?? res.queries ?? 0
    const g = res.entities?.adgroups ?? 0
    const k = res.entities?.keywords ?? 0
    pushToast(`Запросов: ${q}, групп: ${g}, ключей: ${k}`)
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    syncingQueries.value = false
    queriesProgress.value = ''
  }
}

async function addQueryKeyword(q) {
  let adgroupId = q.adgroup_row_id || selectedGroup.value
  if (!adgroupId) {
    pushToast('Выберите группу или синкните структуру', 'error')
    return
  }
  busy.value = true
  try {
    await webApi.searchQueryAddKeyword(q.id, { adgroup_row_id: Number(adgroupId) })
    pushToast('Добавлено в ключи')
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function addQueryNegative(q, scope) {
  if (scope === 'adgroup' && !q.adgroup_row_id) {
    pushToast('У запроса нет группы — добавьте минус в кампанию', 'error')
    return
  }
  busy.value = true
  try {
    await webApi.searchQueryAddNegative(q.id, { scope })
    pushToast(scope === 'campaign' ? 'В минуса кампании' : 'В минуса группы')
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function syncStructure() {
  syncing.value = true
  try {
    const res = await webApi.syncCampaignStructure(route.params.id, (job) => {
      structureProgress.value = jobProgressText(job)
    })
    pushToast(
      `Групп: ${res.adgroups?.adgroups ?? 0}, объявлений: ${res.ads?.ads ?? 0}, ключей: ${res.keywords?.keywords ?? 0}`,
    )
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    syncing.value = false
    structureProgress.value = ''
  }
}

async function createGroup() {
  if (!groupForm.name.trim()) {
    pushToast('Укажите название группы', 'error')
    return
  }
  busy.value = true
  try {
    await webApi.createAdGroup(route.params.id, { name: groupForm.name.trim(), region_ids: [225] })
    groupForm.name = ''
    pushToast('Группа создана')
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function renameGroup(g) {
  const name = window.prompt('Новое название группы', g.name)
  if (!name || !name.trim()) return
  busy.value = true
  try {
    await webApi.patchAdGroup(g.id, { name: name.trim() })
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function saveNegatives() {
  if (!selectedGroup.value) {
    pushToast('Выберите группу', 'error')
    return
  }
  busy.value = true
  try {
    await webApi.patchAdGroup(selectedGroup.value, { negative_text: negForm.text })
    pushToast('Минус-фразы сохранены')
    await load()
    const group = adgroups.value.find((g) => g.id === selectedGroup.value)
    negForm.text = (group?.negative_keywords || []).join('\n')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function saveCampaignNegatives() {
  busy.value = true
  try {
    await webApi.updateCampaign(route.params.id, { negative_text: campNegForm.text })
    pushToast('Минус-фразы кампании сохранены')
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function removeGroup(g) {
  if (!window.confirm(`Удалить группу «${g.name}» в Директе?`)) return
  busy.value = true
  try {
    await webApi.deleteAdGroups([g.id])
    pushToast('Группа удалена')
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function createAd() {
  if (!adForm.adgroup_row_id) {
    pushToast('Выберите группу', 'error')
    return
  }
  busy.value = true
  try {
    await webApi.createAd({
      adgroup_row_id: Number(adForm.adgroup_row_id),
      title: adForm.title,
      title2: adForm.title2,
      text: adForm.text,
      href: adForm.href,
    })
    adForm.title = ''
    adForm.title2 = ''
    adForm.text = ''
    adForm.href = ''
    pushToast('Объявление создано')
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

function editAd(ad) {
  editAdItem.value = ad
  editAdForm.title = ad.title
  editAdForm.title2 = ad.title2
  editAdForm.text = ad.text
  editAdForm.href = ad.href
}

async function saveAd() {
  if (!editAdItem.value) return
  busy.value = true
  try {
    await webApi.patchAd(editAdItem.value.id, { ...editAdForm })
    editAdItem.value = null
    pushToast('Объявление обновлено')
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

function toggleAd(id, on) {
  const next = new Set(selectedAds.value)
  if (on) next.add(id)
  else next.delete(id)
  selectedAds.value = next
}

async function adAction(action) {
  const ids = [...selectedAds.value]
  if (!ids.length) return
  if (action === 'delete' && !window.confirm(`Удалить ${ids.length} объявлений?`)) return
  busy.value = true
  try {
    const res = await webApi.adActions({ ad_ids: ids, action })
    pushToast(`Готово: ${res.applied}`)
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function createKeywords() {
  if (!kwForm.adgroup_row_id) {
    pushToast('Выберите группу', 'error')
    return
  }
  if (!kwForm.text.trim()) {
    pushToast('Укажите фразы', 'error')
    return
  }
  busy.value = true
  try {
    const payload = {
      adgroup_row_id: Number(kwForm.adgroup_row_id),
      text: kwForm.text,
    }
    if (kwForm.bid_rub !== '' && kwForm.bid_rub != null) {
      payload.bid_rub = Number(kwForm.bid_rub)
    }
    const res = await webApi.createKeywords(payload)
    kwForm.text = ''
    kwForm.bid_rub = ''
    pushToast(`Добавлено: ${res.created || 0}`)
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function editKeywordBid(kw) {
  const raw = window.prompt('Ставка, ₽', kw.bid_rub != null ? String(kw.bid_rub) : '')
  if (raw == null || !String(raw).trim()) return
  const bid = Number(raw)
  if (!(bid > 0)) {
    pushToast('Укажите ставку больше 0', 'error')
    return
  }
  busy.value = true
  try {
    await webApi.patchKeyword(kw.id, { bid_rub: bid })
    pushToast('Ставка обновлена')
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function editKeywordText(kw) {
  const text = window.prompt('Ключевая фраза', kw.keyword)
  if (text == null || !text.trim()) return
  busy.value = true
  try {
    await webApi.patchKeyword(kw.id, { keyword: text.trim() })
    pushToast('Фраза обновлена')
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

function toggleKeyword(id, on) {
  const next = new Set(selectedKeywords.value)
  if (on) next.add(id)
  else next.delete(id)
  selectedKeywords.value = next
}

async function keywordAction(action) {
  const ids = [...selectedKeywords.value]
  if (!ids.length) return
  if (action === 'delete' && !window.confirm(`Удалить ${ids.length} фраз?`)) return
  busy.value = true
  try {
    const res = await webApi.keywordActions({ keyword_ids: ids, action })
    pushToast(`Готово: ${res.applied}`)
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

function csvCell(value) {
  const text = String(value ?? '')
  if (/[",\n;]/.test(text)) return `"${text.replace(/"/g, '""')}"`
  return text
}

function exportKeywordsCsv() {
  const rows = filteredKeywords.value
  if (!rows.length) return
  const lines = ['keyword;adgroup;state;status;bid_rub;keyword_id']
  for (const kw of rows) {
    lines.push(
      [
        csvCell(kw.keyword),
        csvCell(kw.adgroup_name),
        csvCell(kw.state),
        csvCell(kw.status),
        csvCell(kw.bid_rub ?? ''),
        csvCell(kw.keyword_id),
      ].join(';'),
    )
  }
  const blob = new Blob(['\uFEFF' + lines.join('\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `keywords_${campaign.value?.campaign_id || route.params.id}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

onMounted(load)
</script>
