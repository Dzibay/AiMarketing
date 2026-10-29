<template>
  <div class="ad-extras field--wide">
    <details class="ad-extras__part" :open="extras.sitelinks.length > 0">
      <summary>
        Быстрые ссылки <span class="field-hint">{{ extras.sitelinks.length }}/{{ MAX_SITELINKS }}</span>
      </summary>
      <p class="field-hint">
        Под объявлением — до 8 ссылок на разделы сайта. Заголовок до {{ SITELINK_TITLE_LEN }} символов, описание до
        {{ SITELINK_DESC_LEN }}.
      </p>
      <div v-for="(link, i) in extras.sitelinks" :key="i" class="ad-extras__link">
        <input v-model="link.title" type="text" :maxlength="SITELINK_TITLE_LEN" placeholder="Заголовок" :disabled="disabled" />
        <input v-model="link.href" type="text" inputmode="url" placeholder="site.ru/razdel" :disabled="disabled" />
        <input v-model="link.description" type="text" :maxlength="SITELINK_DESC_LEN" placeholder="Описание (необязательно)" :disabled="disabled" />
        <button class="btn btn--ghost" type="button" :disabled="disabled" title="Убрать ссылку" @click="extras.sitelinks.splice(i, 1)">✕</button>
      </div>
      <button
        v-if="extras.sitelinks.length < MAX_SITELINKS"
        class="btn btn--ghost"
        type="button"
        :disabled="disabled"
        @click="extras.sitelinks.push({ title: '', href: '', description: '' })"
      >
        + Ссылка
      </button>
    </details>

    <details class="ad-extras__part" :open="!!extras.callouts">
      <summary>Уточнения <span class="field-hint">{{ calloutCount }}/{{ MAX_CALLOUTS }}</span></summary>
      <p class="field-hint">По одному на строку, до {{ CALLOUT_LEN }} символов: «Доставка за 1 день», «Гарантия 2 года».</p>
      <textarea v-model="extras.callouts" rows="3" :disabled="disabled" placeholder="Доставка за 1 день&#10;Гарантия 2 года"></textarea>
    </details>

    <details class="ad-extras__part" :open="extras.images.length > 0">
      <summary>
        {{ maxImages > 1 ? 'Картинки' : 'Картинка' }} <span class="field-hint">{{ extras.images.length }}/{{ maxImages }}</span>
      </summary>
      <p class="field-hint">
        JPG, PNG или GIF до 5 МБ: от 450 px по меньшей стороне с пропорциями от 1:1 до 4:3 или широкая 16:9 от 1080×607.
        Картинка уходит в библиотеку кабинета Директа сразу при выборе.
      </p>
      <div class="ad-extras__images">
        <figure v-for="(img, i) in extras.images" :key="img.hash" class="ad-extras__img">
          <img v-if="img.preview_url" :src="img.preview_url" alt="" />
          <span v-else class="field-hint">{{ img.hash.slice(0, 8) }}…</span>
          <button class="btn btn--ghost" type="button" :disabled="disabled" title="Убрать" @click="extras.images.splice(i, 1)">✕</button>
        </figure>
        <label v-if="extras.images.length < maxImages && accountId" class="btn btn--ghost ad-extras__upload" :class="{ 'is-busy': uploading }">
          {{ uploading ? 'Загружаем…' : '+ Картинка' }}
          <input type="file" accept="image/png,image/jpeg,image/gif" hidden :disabled="disabled || uploading" @change="upload" />
        </label>
      </div>
      <p v-if="uploadError" class="form-error">{{ uploadError }}</p>
    </details>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { errorText, webApi } from '../api/web'
import {
  CALLOUT_LEN,
  IMAGE_MAX_BYTES,
  MAX_CALLOUTS,
  MAX_SITELINKS,
  SITELINK_DESC_LEN,
  SITELINK_TITLE_LEN,
  readFileBase64,
} from '../utils/adExtras'

// extras — реактивный объект из emptyExtras()/extrasFromApi(); компонент правит его на месте.
const props = defineProps({
  extras: { type: Object, required: true },
  accountId: { type: Number, default: null },
  maxImages: { type: Number, default: 1 },
  disabled: { type: Boolean, default: false },
})

const uploading = ref(false)
const uploadError = ref('')
const calloutCount = computed(() => String(props.extras.callouts || '').split('\n').filter((l) => l.trim()).length)

async function upload(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  uploadError.value = ''
  if (file.size > IMAGE_MAX_BYTES) {
    uploadError.value = 'Картинка больше 5 МБ'
    return
  }
  uploading.value = true
  try {
    const data = await readFileBase64(file)
    const res = await webApi.uploadAdImage(props.accountId, { name: file.name.slice(0, 255), data })
    if (!props.extras.images.some((i) => i.hash === res.hash)) props.extras.images.push(res)
  } catch (err) {
    uploadError.value = errorText(err)
  } finally {
    uploading.value = false
  }
}
</script>

<style scoped>
.ad-extras {
  display: grid;
  gap: 8px;
  margin-bottom: 1rem;
}
.ad-extras__part {
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 8px 12px;
}
.ad-extras__part summary {
  cursor: pointer;
  font-weight: 600;
}
.ad-extras__part[open] summary {
  margin-bottom: 8px;
}
.ad-extras__part > * + * {
  margin-top: 8px;
}
.ad-extras__part textarea {
  width: 100%;
}
.ad-extras__link {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.3fr) minmax(0, 1.5fr) auto;
  gap: 6px;
}
@media (max-width: 720px) {
  .ad-extras__link {
    grid-template-columns: minmax(0, 1fr) auto;
  }
  .ad-extras__link input:not(:first-child) {
    grid-column: 1;
  }
  .ad-extras__link button {
    grid-row: 1;
    grid-column: 2;
  }
}
.ad-extras__images {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.ad-extras__img {
  position: relative;
  margin: 0;
  width: 90px;
  height: 90px;
  border-radius: 10px;
  overflow: hidden;
  border: 1px solid var(--line);
  display: grid;
  place-items: center;
  background: var(--surface-2);
}
.ad-extras__img img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.ad-extras__img button {
  position: absolute;
  top: 4px;
  right: 4px;
  padding: 0 6px;
  min-height: 0;
  line-height: 1.4;
  background: var(--surface);
}
.ad-extras__upload {
  cursor: pointer;
}
.ad-extras__upload.is-busy {
  opacity: 0.6;
  pointer-events: none;
}
</style>
