<template>
  <section id="brief" class="panel">
    <div class="panel__head">
      <div>
        <h2>Бриф клиента</h2>
        <p class="hint">
          Что продаём и что точно не наше. ИИ читает бриф при разметке поисковых запросов и в чате, а «точно не наше»
          сразу уходит в минус без вопросов к ИИ.
        </p>
      </div>
      <div class="row-actions">
        <button class="btn btn--ghost" type="button" :disabled="draftBusy" @click="draft">
          {{ draftBusy ? draftText || 'Читаем сайт…' : 'Черновик по сайту' }}
        </button>
      </div>
    </div>
    <p v-if="drafted" class="notice">
      Черновик составлен по {{ drafted }}. Проверьте, поправьте и сохраните — без сохранения он не применится.
    </p>
    <form class="form-grid" @submit.prevent="save">
      <div v-field class="field field--wide">
        <label>Что продаём</label>
        <textarea
          v-model="form.product"
          rows="3"
          maxlength="2000"
          placeholder="Товары или услуги, для кого, чем отличаемся: «Кухни на заказ из массива, средний чек от 300 тыс.»"
        />
      </div>
      <div v-field class="field">
        <label>География</label>
        <input v-model="form.geo" type="text" maxlength="300" placeholder="Москва и область" />
      </div>
      <div v-field class="field">
        <label>Точно не наше</label>
        <textarea
          v-model="form.not_ours"
          rows="4"
          placeholder="По одному на строку: бесплатно, своими руками, вакансии, б/у"
        />
        <small class="field-hint">Слова и темы, запросы с которыми всегда нецелевые. До 100 строк.</small>
      </div>
      <div v-field class="field field--wide">
        <label>Заметки</label>
        <textarea
          v-model="form.notes"
          rows="3"
          maxlength="2000"
          placeholder="Сезонность, конкуренты, бренды, которые продаём и не продаём…"
        />
      </div>
      <p v-if="error" class="form-error field--wide">{{ error }}</p>
      <div class="form-actions field--wide">
        <button class="btn btn--accent" type="submit" :disabled="busy || !changed">
          {{ busy ? 'Сохраняем…' : 'Сохранить бриф' }}
        </button>
      </div>
    </form>
  </section>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { errorText, jobProgressText, wsApi } from '../../api/web'
import { askConfirm } from '../../composables/useConfirm'
import { pushToast } from '../../composables/useToasts'
import { useWs } from './context'

const { ws, patch } = useWs()

function fromBrief(b = {}) {
  return {
    product: b.product || '',
    geo: b.geo || '',
    not_ours: (b.not_ours || []).join('\n'),
    notes: b.notes || '',
  }
}

function toPayload(f) {
  return {
    product: f.product.trim(),
    geo: f.geo.trim(),
    not_ours: f.not_ours
      .split(/\n/)
      .map((s) => s.trim())
      .filter(Boolean),
    notes: f.notes.trim(),
  }
}

const form = reactive(fromBrief(ws.value.brief))
const busy = ref(false)
const error = ref('')
const draftBusy = ref(false)
const draftText = ref('')
const drafted = ref('')

const changed = computed(
  () => JSON.stringify(toPayload(form)) !== JSON.stringify(toPayload(fromBrief(ws.value.brief))),
)

async function save() {
  busy.value = true
  error.value = ''
  try {
    const updated = await wsApi.update(ws.value.id, { brief: toPayload(form) })
    patch(updated)
    Object.assign(form, fromBrief(updated.brief))
    drafted.value = ''
    pushToast('Бриф сохранён', 'ok')
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}

async function draft() {
  draftBusy.value = true
  draftText.value = ''
  error.value = ''
  try {
    const res = await wsApi.briefDraft(ws.value.id, ws.value.site || '', (job) => {
      draftText.value = jobProgressText(job)
    })
    const next = fromBrief(res.brief || {})
    const filled = Object.keys(next).some((key) => form[key].trim())
    // Написанное руками не затираем без спроса: по умолчанию черновик заполняет только пустые поля.
    const replace =
      !filled ||
      (await askConfirm('В брифе уже есть текст. Заменить его черновиком целиком?', {
        title: 'Черновик готов',
        confirmText: 'Заменить',
        cancelText: 'Только пустые поля',
      }))
    for (const key of Object.keys(next)) {
      if (replace || !form[key].trim()) form[key] = next[key]
    }
    drafted.value = res.title ? `«${res.title}» (${res.site})` : res.site || 'сайту'
  } catch (err) {
    error.value = errorText(err)
  } finally {
    draftBusy.value = false
  }
}

watch(
  () => ws.value?.brief,
  (next, prev) => {
    if (JSON.stringify(toPayload(form)) === JSON.stringify(toPayload(fromBrief(prev)))) Object.assign(form, fromBrief(next))
  },
)
</script>
