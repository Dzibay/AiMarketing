<template>
  <section id="links" class="panel">
    <div class="panel__head">
      <div>
        <h2>Шаблон ссылки</h2>
        <p class="hint">Эталонная ссылка сайта: по ней проверяем кампании и создаём новые объявления.</p>
        <details class="hint link-howto">
          <summary>Как это работает</summary>
          Адрес и параметры без меток (например, ref=Yandex) идут в ссылку объявления и быстрые ссылки, utm-метки и
          параметры с подстановками {…} — в параметры кампании: так Директ добавит их ко всем ссылкам, а правка меток
          не отправит объявления на модерацию. Если у группы свои метки, Директ берёт их вместо меток кампании —
          поэтому проверяем и их. Новые кампании, копии и объявления создаются по шаблону.
        </details>
      </div>
    </div>

    <form class="link-form" @submit.prevent="save">
      <input
        v-model.trim="template"
        type="text"
        inputmode="url"
        maxlength="1024"
        aria-label="Шаблон ссылки"
        placeholder="https://site.ru/?ref=Yandex&utm_source=yandex&utm_medium={source_type}&utm_campaign={campaign_id}"
      />
      <button class="btn btn--accent" type="submit" :disabled="saving || template === (ws.url_template || '')">
        {{ saving ? 'Сохраняем…' : 'Сохранить' }}
      </button>
    </form>
    <p v-if="saveError" class="form-error">{{ saveError }}</p>
    <dl v-if="split" class="link-split">
      <dt>В объявлении</dt>
      <dd><code>{{ split.ad }}</code></dd>
      <dt>В параметрах кампании</dt>
      <dd><code>{{ split.tracking || '—' }}</code></dd>
    </dl>

    <template v-if="ws.url_template">
      <div class="link-issues__head">
        <h3>Проверка кампаний</h3>
        <div class="row-actions">
          <button class="btn btn--ghost" type="button" :disabled="loading" @click="load">
            {{ loading ? 'Проверяем…' : 'Проверить' }}
          </button>
          <button
            v-if="fixable.length > 1"
            class="btn btn--accent"
            type="button"
            :disabled="!!fixing"
            @click="fix(fixable.map((c) => c.campaign_row_id))"
          >
            Исправить все ({{ fixable.length }})
          </button>
        </div>
      </div>
      <p v-if="error" class="form-error">{{ error }}</p>
      <p v-else-if="issues && !issues.campaigns.length" class="empty">
        Все ссылки по шаблону.<template v-if="issues.unknown">
          Метки {{ issues.unknown }} {{ plural(issues.unknown, 'кампании', 'кампаний', 'кампаний') }} ещё не загружены —
          обновите кампании из Директа.</template
        >
      </p>
      <p v-if="fixing" class="hint">{{ fixing }}</p>
      <div v-if="issues?.reasons?.length" class="link-reasons">
        <p>
          <strong>Не по шаблону: {{ issues.bad }} {{ plural(issues.bad, 'кампания', 'кампании', 'кампаний') }}.</strong>
          Чем ссылки отличаются от шаблона:
        </p>
        <ul>
          <li v-for="r in issues.reasons" :key="r.where + r.text">
            {{ r.where }}: {{ r.text }}
            <span class="hint">
              — {{ r.campaigns === issues.bad ? 'во всех' : `в ${r.campaigns} ${plural(r.campaigns, 'кампании', 'кампаниях', 'кампаниях')}` }}
            </span>
          </li>
        </ul>
        <p class="hint">Если в кампаниях так и задумано, поправьте шаблон выше — проверка сравнивает ссылки с ним.</p>
      </div>
      <div v-for="c in shownCampaigns" :key="c.campaign_row_id" class="link-issue">
        <div class="link-issue__head">
          <strong>{{ c.name }}</strong>
          <small>№ {{ c.campaign_id }}</small>
          <button class="btn btn--ghost" type="button" :disabled="!!fixing" @click="fix([c.campaign_row_id])">Исправить</button>
        </div>
        <ul>
          <li v-if="c.campaign_problems.length">
            Параметры кампании: {{ c.campaign_problems.join('; ') }}
            <dl class="link-diff">
              <dt>Сейчас</dt>
              <dd><LinkCode :value="c.tracking" :marked="c.campaign_bad_params" /></dd>
              <dt>Станет</dt>
              <dd><LinkCode :value="c.fixed_tracking" :marked="c.campaign_bad_params" good /></dd>
            </dl>
          </li>
          <li v-for="g in (c.groups || []).slice(0, 3)" :key="'g' + g.adgroup_row_id">
            Метки группы «{{ g.name || g.adgroup_id }}»: {{ g.problems.join('; ') }}
            <dl class="link-diff">
              <dt>Сейчас</dt>
              <dd><LinkCode :value="g.tracking" :marked="g.bad_params" /></dd>
              <dt>Станет</dt>
              <dd><LinkCode :value="g.fixed_tracking" :marked="g.bad_params" good /></dd>
            </dl>
          </li>
          <li v-if="c.groups_total > 3" class="hint">
            и ещё {{ c.groups_total - 3 }} {{ plural(c.groups_total - 3, 'группа', 'группы', 'групп') }} со своими метками
          </li>
          <li v-for="(link, i) in (c.ad_links || []).slice(0, 3)" :key="'a' + i">
            {{ link.count > 1 ? `Объявления (${link.count})` : 'Объявление' }}:
            {{ [...link.problems, ...link.sitelink_problems.map((p) => `быстрые ссылки — ${p}`)].join('; ') }}
            <small class="link-titles">{{ titlesText(link) }}</small>
            <dl class="link-diff">
              <template v-if="link.problems.length">
                <dt>Сейчас</dt>
                <dd><LinkCode :value="link.href" :marked="link.bad_params" /></dd>
                <dt>Станет</dt>
                <dd v-if="link.fixable"><LinkCode :value="link.fixed_href" :marked="link.bad_params" good /></dd>
                <dd v-else class="hint">такой тип объявления правьте в Директе</dd>
              </template>
              <template v-if="link.sitelink_href">
                <dt>Быстрая ссылка</dt>
                <dd><LinkCode :value="link.sitelink_href" /></dd>
                <dt>Станет</dt>
                <dd class="hint">{{ link.fixable ? 'новый набор быстрых ссылок по шаблону' : 'такой тип объявления правьте в Директе' }}</dd>
              </template>
            </dl>
          </li>
          <li v-if="c.ad_links_total > 3" class="hint">
            и ещё {{ c.ad_links_total - 3 }} {{ plural(c.ad_links_total - 3, 'ссылка', 'ссылки', 'ссылок') }} не по шаблону
          </li>
        </ul>
      </div>
      <button
        v-if="hiddenCampaigns > 0"
        class="btn btn--ghost link-more"
        type="button"
        @click="showAll = true"
      >
        Показать ещё {{ hiddenCampaigns }} {{ plural(hiddenCampaigns, 'кампанию', 'кампании', 'кампаний') }}
      </button>
      <p v-if="issues?.campaigns.length" class="hint">
        Исправленные объявления и быстрые ссылки Директ заново отправит на модерацию; метки кампаний и групп модерацию
        не требуют. Откат — в журнале действий.
      </p>
    </template>
  </section>
</template>

<script setup>
import { computed, h, onMounted, ref, watch } from 'vue'
import { errorText, jobProgressText, wsApi } from '../../api/web'
import { askConfirm } from '../../composables/useConfirm'
import { pushToast } from '../../composables/useToasts'
import { plural } from '../../utils/format'
import { useWs } from './context'

const { ws, patch } = useWs()

const template = ref(ws.value.url_template || '')
const saving = ref(false)
const saveError = ref('')
const issues = ref(null)
const loading = ref(false)
const error = ref('')
const fixing = ref('')

watch(
  () => ws.value.url_template,
  (v) => (template.value = v || ''),
)

// Как шаблон разойдётся: то же деление, что на сервере (links.parse_template).
const split = computed(() => {
  const raw = template.value
  if (!raw) return null
  const [base, query = ''] = raw.split('?', 2)
  const ad = []
  const tracking = []
  for (const part of query.split('&').filter(Boolean)) {
    const key = part.split('=', 1)[0]
    ;(key.toLowerCase().startsWith('utm_') || part.includes('{') ? tracking : ad).push(part)
  }
  return { ad: base + (ad.length ? `?${ad.join('&')}` : ''), tracking: tracking.join('&') }
})

const fixable = computed(() =>
  (issues.value?.campaigns || []).filter((c) => c.campaign_problems.length || c.groups_total || c.ads.some((a) => a.fixable)),
)

// Длинный список кампаний показываем частями.
const CAMPAIGNS_SHOWN = 10
const showAll = ref(false)
const shownCampaigns = computed(() => {
  const all = issues.value?.campaigns || []
  return showAll.value ? all : all.slice(0, CAMPAIGNS_SHOWN)
})
const hiddenCampaigns = computed(() => (issues.value?.campaigns?.length || 0) - shownCampaigns.value.length)

function titlesText(link) {
  const shown = link.titles.map((t) => `«${t}»`).join(', ')
  const rest = link.count - link.titles.length
  return rest > 0 ? `${shown} и ещё ${rest}` : shown
}

/** Ссылка или метки с подсвеченными параметрами: marked — ключи (ref, utm_source), good — зелёным, а не красным. */
function linkParts(value, marked) {
  const text = value || ''
  const keys = new Set(marked || [])
  if (!keys.size) return [{ text }]
  let base = ''
  let query = text
  if (text.includes('://')) {
    const q = text.indexOf('?')
    if (q < 0) return [{ text }]
    base = text.slice(0, q + 1)
    query = text.slice(q + 1)
  }
  const hash = query.indexOf('#')
  const tail = hash < 0 ? '' : query.slice(hash)
  if (hash >= 0) query = query.slice(0, hash)
  const out = base ? [{ text: base }] : []
  query.split('&').forEach((part, i) => {
    if (i) out.push({ text: '&' })
    out.push({ text: part, mark: keys.has(part.split('=', 1)[0]) })
  })
  if (tail) out.push({ text: tail })
  return out
}

function LinkCode(props) {
  if (!props.value) return h('span', { class: 'hint' }, 'пусто')
  const tone = props.good ? 'link-mark link-mark--good' : 'link-mark'
  return h(
    'code',
    linkParts(props.value, props.marked).map((p) => (p.mark ? h('mark', { class: tone }, p.text) : p.text)),
  )
}
LinkCode.props = { value: String, marked: Array, good: Boolean }

async function save() {
  saveError.value = ''
  saving.value = true
  try {
    const updated = await wsApi.update(ws.value.id, { url_template: template.value })
    patch(updated)
    pushToast('Шаблон сохранён', 'ok')
    await load()
  } catch (err) {
    saveError.value = errorText(err)
  } finally {
    saving.value = false
  }
}

async function load() {
  if (!ws.value.url_template) return
  loading.value = true
  error.value = ''
  try {
    issues.value = await wsApi.linkIssues(ws.value.id)
  } catch (err) {
    error.value = errorText(err)
  } finally {
    loading.value = false
  }
}

async function fix(ids) {
  const ads = (issues.value?.campaigns || [])
    .filter((c) => ids.includes(c.campaign_row_id))
    .reduce((n, c) => n + (c.ads_fixable ?? c.ads.filter((a) => a.fixable).length), 0)
  const ok = await askConfirm(
    `Кампаний: ${ids.length}. Метки кампаний и групп обновятся без модерации.` +
      (ads ? ` Объявлений с новой ссылкой или быстрыми ссылками: ${ads} — Директ отправит их на модерацию заново.` : ''),
    { title: 'Исправить ссылки по шаблону?', confirmText: 'Исправить' },
  )
  if (!ok) return
  fixing.value = 'Исправляем…'
  try {
    const res = await wsApi.fixLinks(ws.value.id, ids, (job) => (fixing.value = jobProgressText(job)))
    const errors = (res.items || []).flatMap((i) => i.errors || [])
    const parts = [`метки кампаний: ${res.tracking_fixed}`]
    if (res.groups_fixed) parts.push(`группы: ${res.groups_fixed}`)
    parts.push(`объявления: ${res.ads_fixed}`)
    if (res.sitelinks_fixed) parts.push(`быстрые ссылки: ${res.sitelinks_fixed}`)
    pushToast(
      `Исправлено — ${parts.join(', ')}` + (errors.length ? `. Не вышло: ${errors[0]}` : ''),
      errors.length ? 'error' : 'ok',
    )
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    fixing.value = ''
  }
}

onMounted(load)
</script>

<style scoped>
.link-howto {
  margin-top: 4px;
}
.link-howto summary {
  cursor: pointer;
  width: fit-content;
}
.link-form {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}
.link-form input {
  flex: 1;
  min-width: 0;
}
.link-split {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 4px 12px;
  margin: 0 0 12px;
  font-size: 0.9em;
}
.link-split dt {
  color: var(--muted);
}
.link-split dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.link-issues__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 12px;
}
.link-issue {
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 8px 12px;
  margin-top: 8px;
}
.link-issue__head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.link-issue__head button {
  margin-left: auto;
}
.link-issue ul {
  margin: 6px 0 0;
  padding-left: 18px;
}
.link-issue li + li {
  margin-top: 10px;
}
.link-issue code {
  overflow-wrap: anywhere;
}
.link-titles {
  display: block;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* «Сейчас → станет»: подписи слева, ссылки справа. */
.link-diff {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 2px 10px;
  margin: 4px 0 0;
  font-size: 0.9em;
}
.link-diff dt {
  color: var(--muted);
}
.link-diff dd {
  margin: 0;
}
.link-issue :deep(.link-mark) {
  padding: 0 2px;
  border-radius: 4px;
  color: var(--danger);
  background: var(--danger-soft);
}
.link-issue :deep(.link-mark--good) {
  color: var(--ok);
  background: var(--ok-soft);
}
.link-reasons {
  margin-top: 8px;
  padding: 10px 14px;
  border-radius: 12px;
  background: var(--warn-soft);
}
.link-reasons p {
  margin: 0;
}
.link-reasons ul {
  margin: 6px 0;
  padding-left: 18px;
}
.link-more {
  margin-top: 8px;
}
</style>
