<template>
  <section id="links" class="panel">
    <div class="panel__head">
      <div>
        <h2>Шаблон ссылки</h2>
        <p class="hint">
          Эталонная ссылка сайта. Адрес и параметры без меток (например, ref=Yandex) идут в ссылку объявления,
          utm-метки и параметры с подстановками {…} — в параметры кампании: так Директ добавит их ко всем ссылкам,
          а правка меток не отправит объявления на модерацию. Новые кампании, копии и объявления создаются по шаблону.
        </p>
      </div>
    </div>

    <form class="link-form" @submit.prevent="save">
      <input
        v-model.trim="template"
        type="text"
        inputmode="url"
        maxlength="1024"
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
      <div v-for="c in issues?.campaigns || []" :key="c.campaign_row_id" class="link-issue">
        <div class="link-issue__head">
          <strong>{{ c.name }}</strong>
          <small>№ {{ c.campaign_id }}</small>
          <button class="btn btn--ghost" type="button" :disabled="!!fixing" @click="fix([c.campaign_row_id])">Исправить</button>
        </div>
        <ul>
          <li v-if="c.campaign_problems.length">
            Параметры кампании: {{ c.campaign_problems.join('; ') }}
            <small class="hint">→ <code>{{ c.fixed_tracking }}</code></small>
          </li>
          <li v-for="ad in c.ads.slice(0, 5)" :key="ad.ad_row_id">
            Объявление «{{ ad.title || ad.ad_id }}»: {{ ad.problems.join('; ') }}
            <small class="hint">
              <template v-if="ad.fixable">→ <code>{{ ad.fixed_href }}</code></template>
              <template v-else>— такой тип объявления правьте в Директе</template>
            </small>
          </li>
          <li v-if="c.ads_total > 5" class="hint">и ещё {{ c.ads_total - 5 }} {{ plural(c.ads_total - 5, 'объявление', 'объявления', 'объявлений') }}</li>
        </ul>
      </div>
      <p v-if="issues?.campaigns.length" class="hint">
        Исправленные объявления Директ заново отправит на модерацию; метки кампании модерацию не требуют. Откат — в
        журнале действий.
      </p>
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
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

const fixable = computed(() => (issues.value?.campaigns || []).filter((c) => c.campaign_problems.length || c.ads.some((a) => a.fixable)))

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
    .reduce((n, c) => n + c.ads.filter((a) => a.fixable).length, 0)
  const ok = await askConfirm(
    `Кампаний: ${ids.length}. Метки кампаний обновятся без модерации.` +
      (ads ? ` Объявлений с новым адресом: ${ads} — Директ отправит их на модерацию заново.` : ''),
    { title: 'Исправить ссылки по шаблону?', confirmText: 'Исправить' },
  )
  if (!ok) return
  fixing.value = 'Исправляем…'
  try {
    const res = await wsApi.fixLinks(ws.value.id, ids, (job) => (fixing.value = jobProgressText(job)))
    const errors = (res.items || []).flatMap((i) => i.errors || [])
    pushToast(
      `Метки: ${res.tracking_fixed}, объявления: ${res.ads_fixed}` + (errors.length ? `. Не вышло: ${errors[0]}` : ''),
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
.link-issue code {
  overflow-wrap: anywhere;
}
</style>
