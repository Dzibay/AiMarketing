<template>
  <div class="set">
    <nav class="snav" aria-label="Разделы настроек проекта">
      <button
        v-for="t in tabs"
        :key="t.id"
        type="button"
        :class="{ active: t.id === tab }"
        :aria-current="t.id === tab ? 'page' : undefined"
        @click="go(t.id)"
      >
        <component :is="t.icon" :size="16" aria-hidden="true" />
        <span>{{ t.label }}</span>
      </button>
    </nav>

    <div class="set__body">
      <template v-if="tab === 'main'">
        <section class="panel">
          <div class="panel__head">
            <div>
              <h2>Цель и бюджет</h2>
              <p v-if="expert || !ws.counter_name" class="hint">
                Счётчик Метрики № {{ ws.counter_id }}{{ ws.counter_name ? ` · ${ws.counter_name}` : '' }}{{
                  expert && ws.counter_login ? ` · доступ через ${ws.counter_login}` : ''
                }}
              </p>
              <p v-else class="hint">Счётчик Метрики «{{ ws.counter_name }}»</p>
            </div>
          </div>
          <p v-if="!ws.counter_ok" class="notice notice--warn">
            Счётчик не найден среди включённых в подключениях Яндекса — статистика сайта не обновляется. Проверьте доступы в
            <RouterLink to="/cabinet/connections">подключениях</RouterLink>.
          </p>
          <form class="form-grid" @submit.prevent="saveGeneral">
            <div v-field class="field">
              <label>Название</label>
              <input v-model.trim="general.name" type="text" maxlength="120" required />
            </div>
            <div v-field class="field">
              <label>Сайт</label>
              <input v-model.trim="general.site" type="text" maxlength="300" placeholder="example.ru" />
            </div>
            <div v-field class="field">
              <label>Бюджет на месяц</label>
              <UiNumber v-model="general.monthly_budget_rub" suffix="₽" placeholder="не задан" />
              <small class="field-hint">Сервис предупредит, если деньги уходят быстрее плана.</small>
            </div>
            <div v-field class="field">
              <label>{{ expert ? 'KPI' : 'Главный показатель' }}<TermHint term="kpi" /></label>
              <UiSelect v-model="general.kpi_type" :options="kpiOptions" />
            </div>
            <div v-field class="field">
              <label>Целевое значение</label>
              <UiNumber
                v-model="general.kpi_target"
                :digits="2"
                :suffix="general.kpi_type === 'drr' ? '%' : general.kpi_type === 'cpa' ? '₽' : ''"
                :disabled="!general.kpi_type"
                :placeholder="general.kpi_type ? '' : 'сначала выберите показатель'"
              />
            </div>
            <div v-field class="field field--wide">
              <label>Основная цель Метрики</label>
              <div class="filters filters--inline">
                <UiSelect v-model="general.goal_id" :options="goalOptions" />
                <button class="btn btn--ghost" type="button" :disabled="goalsBusy || !ws.counter_row_id" @click="syncGoals">
                  {{ goalsBusy ? 'Обновляем…' : 'Обновить цели' }}
                </button>
              </div>
              <small class="field-hint">
                {{
                  expert
                    ? 'Конверсии, CPA и ДРР в сводке и аналитике считаются по этой цели.'
                    : 'Заявки и их цена в сводке и отчётах считаются по этой цели.'
                }}
              </small>
            </div>
            <p v-if="generalError" class="form-error field--wide">{{ generalError }}</p>
            <div class="form-actions field--wide">
              <button class="btn btn--accent" type="submit" :disabled="generalBusy || !generalChanged">
                {{ generalBusy ? 'Сохраняем…' : 'Сохранить' }}
              </button>
            </div>
          </form>
        </section>

        <section class="panel">
          <div class="panel__head">
            <div>
              <h2>{{ ws.archived ? 'Проект в архиве' : ws.counter_id ? 'Удалить сайт' : 'Архив' }}</h2>
              <p class="hint">
                <template v-if="ws.archived">Проект скрыт из переключателя и обзора. Кампании и история сохранены.</template>
                <template v-else-if="ws.counter_id">
                  Сайт пропадёт из Толка: проект уйдёт в архив, данные перестанут загружаться. В Метрике и Директе ничего
                  не изменится.<template v-if="expert"> «В архив» только прячет проект — сайт останется в «Добавить сайт».</template>
                </template>
                <template v-else>Архивный проект скрыт из переключателя и обзора; кампании в Директе не меняются.</template>
              </p>
            </div>
            <div class="danger-acts">
              <button v-if="ws.archived" class="btn btn--ghost" type="button" :disabled="archiveBusy" @click="toggleArchive">
                Вернуть из архива
              </button>
              <template v-else>
                <button
                  v-if="expert || !ws.counter_id"
                  class="btn btn--ghost"
                  type="button"
                  :disabled="archiveBusy"
                  @click="toggleArchive"
                >
                  В архив
                </button>
                <button
                  v-if="ws.counter_id"
                  class="btn btn--ghost"
                  type="button"
                  :disabled="archiveBusy"
                  @click="removeThisSite"
                >
                  <Trash2 :size="15" aria-hidden="true" />Удалить сайт
                </button>
              </template>
            </div>
          </div>
        </section>
      </template>

      <template v-else-if="tab === 'biz'">
        <BriefPanel />
        <ContextFilesPanel />
      </template>

      <template v-else-if="tab === 'ai'">
        <form class="panel" @submit.prevent="saveHelper">
          <div class="panel__head">
            <div>
              <h2>Как ИИ пишет объявления</h2>
              <p class="hint">Так ИИ пишет тексты для клиентов — в чате проекта и когда переписывает объявления.</p>
            </div>
          </div>
          <div class="style-grid">
            <span class="style-grid__label">Тон</span>
            <div class="seg" role="group" aria-label="Тон">
              <button
                v-for="t in TONES"
                :key="t.id"
                type="button"
                :class="{ active: style.tone === t.id }"
                :aria-pressed="style.tone === t.id"
                @click="style.tone = t.id"
              >
                {{ t.label }}
              </button>
            </div>
            <span class="style-grid__label">Обращение</span>
            <div class="seg" role="group" aria-label="Обращение к клиентам">
              <button
                v-for="a in ADDRESS"
                :key="a.id"
                type="button"
                :class="{ active: style.address === a.id }"
                :aria-pressed="style.address === a.id"
                @click="style.address = a.id"
              >
                {{ a.label }}
              </button>
            </div>
          </div>

          <template v-if="models?.configured && models.items.length > 1">
            <h3 class="style-h">Какой моделью отвечать</h3>
            <p class="hint">
              Чем больше точек, тем умнее ответ и тем больше лимита он тратит. Модель ставится новым чатам проекта, в любом
              чате её можно сменить.
            </p>
            <div class="seg style-models" role="group" aria-label="Модель по умолчанию">
              <button
                v-for="m in models.items"
                :key="m.id"
                type="button"
                :class="{ active: styleModel === m.id }"
                :aria-pressed="styleModel === m.id"
                @click="style.model_class = m.id === models.default ? '' : m.id"
              >
                {{ m.name }} <span class="dots" aria-hidden="true">{{ dots(m.dots) }}</span>
              </button>
            </div>
          </template>

          <h3 class="style-h">Заметки для ИИ</h3>
          <p class="hint">ИИ читает это перед каждым ответом в чате проекта: что нельзя трогать, регионы, особенности.</p>
          <textarea
            v-model="notes"
            class="notes-area"
            rows="5"
            maxlength="8000"
            aria-label="Заметки для ИИ"
            placeholder="Например: без обещаний скидок; регион — только Москва; кампанию «Бренд» не трогать"
          />
          <p v-if="notesError" class="form-error">{{ notesError }}</p>
          <div class="form-actions">
            <button class="btn btn--accent" type="submit" :disabled="notesBusy || !helperChanged">
              {{ notesBusy ? 'Сохраняем…' : 'Сохранить' }}
            </button>
          </div>
        </form>

        <section class="ccard ai-simple">
          <div class="prow">
            <div class="prow__main">
              <b>{{ aiRights.length ? 'ИИ может менять без согласования' : 'ИИ ничего не меняет сам' }}</b>
              <span class="meta-note">
                <template v-if="aiRights.length">{{ aiRights.join(', ') }}. Остальное ждёт вашего «Выполнить».</template>
                <template v-else>Каждое изменение ждёт вашего «Выполнить». Всё выполненное можно вернуть в «Истории».</template>
              </span>
            </div>
            <div class="prow__acts">
              <button v-if="expert" class="btn btn--ghost" type="button" @click="go('access')">Настроить доступ</button>
              <RouterLink class="btn btn--ghost" :to="`/w/${ws.id}/history`">Открыть историю</RouterLink>
            </div>
          </div>
        </section>
      </template>

      <section v-else-if="tab === 'access'" class="panel">
        <div class="panel__head">
          <div>
            <h2>Доступ ИИ</h2>
            <p class="hint">
              Что ИИ-помощник может менять в Директе сам. Всё остальное он предлагает в чате, и действие выполняется только
              после вашего «Выполнить».
            </p>
          </div>
        </div>
        <form class="ai-access" @submit.prevent="saveAi">
          <UiCheckbox v-model="ai.full_access" class="check-row ai-access__full">
            <strong>Полный доступ</strong>
            <small>ИИ выполняет любые действия в рамках проекта без согласования. Удаление — тоже.</small>
          </UiCheckbox>
          <fieldset class="ai-access__cats" :disabled="ai.full_access">
            <legend>Без согласования разрешено</legend>
            <UiCheckbox v-for="c in ws.ai_categories" :key="c.key" v-model="ai.categories[c.key]" class="check-row">
              {{ c.label }}
            </UiCheckbox>
          </fieldset>
          <div v-field class="field">
            <label>Предел разового изменения бюджета или ставки, %</label>
            <div class="range-row">
              <input v-model.number="ai.max_change_pct" type="range" min="0" max="200" step="5" :disabled="ai.full_access" />
              <input
                v-model.number="ai.max_change_pct"
                type="number"
                min="0"
                max="200"
                step="1"
                required
                inputmode="numeric"
                aria-label="Предел изменения, %"
                :disabled="ai.full_access"
              />
            </div>
            <small class="field-hint">
              Изменение больше предела ИИ всё равно отправит на согласование, даже если категория разрешена.
            </small>
          </div>
          <p v-if="aiError" class="form-error">{{ aiError }}</p>
          <div class="form-actions">
            <button class="btn btn--accent" type="submit" :disabled="aiBusy || !aiChanged">
              {{ aiBusy ? 'Сохраняем…' : 'Сохранить' }}
            </button>
          </div>
        </form>
      </section>

      <section v-else-if="tab === 'mem'" class="panel">
        <div class="panel__head">
          <div>
            <h2>Что ИИ запомнил</h2>
            <p class="hint">
              Ваши ответы из чата — например, как устроена схема конверсий за 0 ₽. ИИ учитывает их в каждом чате проекта
              и не переспрашивает. Устаревший ответ забудьте — ИИ спросит снова.
            </p>
          </div>
        </div>
        <p v-if="!ws.ai_facts?.length" class="empty">Пока ничего: ИИ запоминает только то, что вы сами ответили или подтвердили.</p>
        <ul v-else class="ai-facts__list">
          <li v-for="f in ws.ai_facts" :key="f.key" class="ai-facts__item">
            <div class="ai-facts__text">
              <strong>{{ f.question || f.key }}</strong>
              <span>{{ f.answer }}</span>
              <small v-if="f.at">{{ formatDateTime(f.at) }}</small>
            </div>
            <button class="btn btn--ghost" type="button" :disabled="!!forgetting" @click="forgetFact(f)">
              {{ forgetting === f.key ? 'Забываем…' : 'Забыть' }}
            </button>
          </li>
        </ul>
      </section>

      <template v-else-if="tab === 'cabs'">
        <section class="panel">
          <div class="panel__head">
            <div>
              <h2>Кабинеты Директа</h2>
              <p class="hint">
                Кабинеты, из которых проект берёт кампании и в которых создаёт новые. Новые логины Яндекса добавляются в
                <RouterLink to="/cabinet/connections">подключениях</RouterLink>.
              </p>
            </div>
          </div>
          <p v-if="accountsError" class="empty">{{ accountsError }}</p>
          <p v-else-if="!linked.length" class="empty">Кабинеты не подключены.</p>
          <div v-else class="list">
            <div v-for="a in linked" :key="a.id" class="list-row" :class="{ 'is-off': !a.enabled }">
              <div>
                <strong>
                  {{ a.name || a.client_login }}
                  <span v-if="!a.enabled" class="chip">выключен</span>
                </strong>
                <small>
                  {{ a.client_login }}<template v-if="a.connection_login"> · вход {{ a.connection_login }}</template> ·
                  кампаний в проекте {{ a.campaigns_in_workspace }} из {{ a.campaigns_total }}
                  <template v-if="a.balance_kopecks != null"> · баланс {{ rub(a.balance_kopecks) }}</template>
                </small>
                <small v-if="a.counter_access?.hint" :class="COUNTER_TONES[a.counter_access.status]">
                  {{ a.counter_access.hint }}
                </small>
              </div>
              <div class="row-actions">
                <UiMenu size="sm" :label="`Действия с кабинетом ${a.client_login}`" :disabled="accountBusy === a.id">
                  <UiMenuItem :icon="Unplug" danger @click="removeAccount(a)">Отключить кабинет</UiMenuItem>
                </UiMenu>
              </div>
            </div>
          </div>

          <div v-if="available.length" class="add-account">
            <h3>Подключить кабинет</h3>
            <form class="filters filters--inline" @submit.prevent="addAccount">
              <UiSelect
                v-model="addForm.account_id"
                :options="availableOptions"
                placeholder="Кабинет…"
                aria-label="Кабинет"
                required
              />
              <UiSelect v-model="addForm.campaigns" :options="TAKE_CAMPAIGNS" aria-label="Какие кампании забрать" />
              <button class="btn btn--accent" type="submit" :disabled="!addForm.account_id || accountBusy === 'add'">
                {{ accountBusy === 'add' ? 'Подключаем…' : 'Подключить' }}
              </button>
            </form>
          </div>
        </section>

        <section class="panel">
          <div class="panel__head">
            <div>
              <h2>Кампании проекта</h2>
              <p class="hint">
                Отметьте кампании, с которыми работает проект. Кампания живёт в одном проекте — отметка здесь
                перенесёт её из другого.
              </p>
            </div>
            <div class="row-actions">
              <button class="btn btn--ghost" type="button" :disabled="syncBusy" @click="syncCampaigns">
                {{ syncBusy ? syncText || 'Обновляем…' : 'Обновить из Директа' }}
              </button>
            </div>
          </div>

          <div class="filters filters--inline">
            <input v-model.trim="campaignSearch" class="search-input" type="search" placeholder="Название или № кампании" aria-label="Поиск кампании" />
            <UiSwitch v-model="onlyMatching">Только со счётчиком № {{ ws.counter_id }}</UiSwitch>
            <UiSwitch v-model="showArchived">Показывать архивные</UiSwitch>
          </div>

          <p v-if="candidatesLoading && !groups.length" class="empty">Загружаем кампании…</p>
          <p v-else-if="candidatesError" class="empty">{{ candidatesError }}</p>
          <p v-else-if="!groups.length" class="empty">
            В подключённых логинах нет кампаний Директа. Обновите кабинеты в
            <RouterLink to="/cabinet/connections">подключениях</RouterLink>.
          </p>
          <template v-else>
            <div v-for="g in visibleGroups" :key="g.account_id" class="campaign-picker">
              <div class="campaign-picker__head">
                <UiCheckbox
                  :checked="g.items.length > 0 && g.items.every((c) => selected.has(c.id))"
                  :indeterminate="g.items.some((c) => selected.has(c.id)) && !g.items.every((c) => selected.has(c.id))"
                  @change="toggleGroup(g, $event.target.checked)"
                >
                  <strong>{{ g.account_name }}</strong>
                </UiCheckbox>
                <small>
                  {{ g.client_login }}
                  <template v-if="!g.linked"> · кабинет подключится автоматически</template>
                </small>
              </div>
              <UiCheckbox
                v-for="c in g.items"
                :key="c.id"
                class="check-row campaign-picker__row"
                :class="{ 'is-off': c.state === 'ARCHIVED' }"
                :checked="selected.has(c.id)"
                @change="toggle(c.id, $event.target.checked)"
              >
                  {{ c.name }}
                  <small>
                    № {{ c.campaign_id }} · {{ CAMPAIGN_STATES[c.state] || c.state || '—' }} ·
                    {{ CAMPAIGN_TYPES[c.type] || c.type }}
                    <template v-if="!c.matches_counter">
                      · <span class="tone-warn">{{ c.counter_ids.length ? `счётчик ${c.counter_ids.join(', ')}` : 'без счётчика' }}</span>
                    </template>
                    <template v-if="c.workspace_id && !c.in_this"> · сейчас в «{{ niceQuotes(c.workspace_name) }}»</template>
                  </small>
              </UiCheckbox>
            </div>
            <p v-if="!visibleGroups.length" class="empty">Под фильтр ничего не подходит.</p>
          </template>

          <div v-if="diff.add.length || diff.remove.length" class="form-actions form-actions--sticky">
            <span>
              <template v-if="diff.add.length">Добавить {{ diff.add.length }}</template>
              <template v-if="diff.add.length && diff.remove.length">, </template>
              <template v-if="diff.remove.length">убрать {{ diff.remove.length }}</template>
              <template v-if="diff.moving"> (перенос из других проектов: {{ diff.moving }})</template>
            </span>
            <button class="btn btn--ghost" type="button" :disabled="campaignsBusy" @click="resetSelection">Сбросить</button>
            <button class="btn btn--accent" type="button" :disabled="campaignsBusy" @click="saveCampaigns">
              {{ campaignsBusy ? 'Сохраняем…' : 'Сохранить состав' }}
            </button>
          </div>
        </section>
      </template>

      <LinkTemplatePanel v-else-if="tab === 'utm'" />

      <section v-else-if="tab === 'client'" class="panel">
        <div class="panel__head">
          <div>
            <h2>Клиент</h2>
            <p class="hint">
              Клиент — папка над проектами: объединяет их в переключателе и сводке. Общий бюджет клиента на месяц
              сверяется с суммой расходов всех его проектов.
            </p>
          </div>
        </div>

        <div v-field class="field client-pick">
          <label>Папка клиента</label>
          <UiSelect
            :model-value="ws.client_id ?? null"
            :options="clientChoices"
            :disabled="clientBusy"
            @change="pickClient"
          />
        </div>

        <template v-if="currentClient">
          <form class="form-grid" @submit.prevent="saveClient">
            <div v-field class="field">
              <label>Название клиента</label>
              <input v-model.trim="clientEdit.name" type="text" required maxlength="200" />
            </div>
            <div v-field class="field">
              <label>Общий бюджет в месяц</label>
              <UiNumber v-model="clientEdit.monthly_budget_rub" suffix="₽" placeholder="не задан" />
            </div>
            <div v-field class="field field--wide">
              <label>Заметки</label>
              <textarea v-model="clientEdit.notes" rows="3" maxlength="5000" placeholder="Контакты, договорённости, особенности"></textarea>
            </div>
            <div class="form-actions">
              <button class="btn btn--accent" type="submit" :disabled="clientBusy || !clientChanged">Сохранить клиента</button>
              <UiMenu :label="`Ещё действия с клиентом «${niceQuotes(currentClient.name)}»`" :disabled="clientBusy">
                <UiMenuItem :icon="Unlink" @click="detachClient">Убрать проект от клиента</UiMenuItem>
                <UiMenuItem
                  :icon="currentClient.archived ? ArchiveRestore : Archive"
                  @click="archiveClient(currentClient, !currentClient.archived)"
                >
                  {{ currentClient.archived ? 'Вернуть из архива' : 'В архив' }}
                </UiMenuItem>
                <UiMenuItem :icon="Trash2" danger @click="removeClient(currentClient)">Удалить клиента</UiMenuItem>
              </UiMenu>
            </div>
          </form>
          <p class="hint">
            Проекты клиента:
            <template v-for="(w, i) in clientProjects" :key="w.id">
              <template v-if="i">, </template>
              <strong v-if="w.id === ws.id">{{ niceQuotes(w.name) }}</strong>
              <RouterLink v-else :to="`/w/${w.id}/settings`">{{ niceQuotes(w.name) }}</RouterLink>
            </template>
          </p>
        </template>

        <template v-else>
          <p class="hint">Проект без клиента. Выберите папку выше или создайте новую — проект сразу войдёт в неё.</p>
          <form class="filters" @submit.prevent="createClient">
            <div v-field class="field field--wide">
              <label>Новый клиент</label>
              <input v-model.trim="clientDraft.name" type="text" required maxlength="200" placeholder="ООО «Ромашка»" />
            </div>
            <div v-field class="field">
              <label>Общий бюджет в месяц</label>
              <UiNumber v-model="clientDraft.monthly_budget_rub" suffix="₽" placeholder="не задан" />
            </div>
            <button class="btn btn--accent" type="submit" :disabled="clientBusy">Создать и привязать</button>
          </form>
        </template>

        <template v-if="archivedClients.length">
          <h3 class="subhead">Клиенты в архиве</h3>
          <div class="list">
            <div v-for="c in archivedClients" :key="c.id" class="list-row is-off">
              <div>
                <strong>{{ niceQuotes(c.name) }}</strong>
              </div>
              <div class="row-actions">
                <button class="btn btn--ghost" type="button" :disabled="clientBusy" @click="archiveClient(c, false)">Вернуть</button>
                <UiMenu size="sm" :label="`Действия с клиентом «${niceQuotes(c.name)}»`" :disabled="clientBusy">
                  <UiMenuItem :icon="Trash2" danger @click="removeClient(c)">Удалить клиента</UiMenuItem>
                </UiMenu>
              </div>
            </div>
          </div>
        </template>
      </section>

      <ExpertHint>Доступ ИИ, память помощника, кабинеты и кампании, шаблон ссылки, клиент и архив</ExpertHint>
    </div>
  </div>
</template>

<script setup>
import { askConfirm } from '../../composables/useConfirm'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import {
  Archive,
  ArchiveRestore,
  FileText,
  Folder,
  Link2,
  Lock,
  Megaphone,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  Unlink,
  Unplug,
} from '@lucide/vue'
import { aiApi, errorText, jobProgressText, webApi, wsApi } from '../../api/web'
import { removeSite } from '../../composables/useSiteRemoval'
import { pushToast } from '../../composables/useToasts'
import { loadWorkspaces, useWorkspaces } from '../../composables/useWorkspaces'
import { CAMPAIGN_STATES, CAMPAIGN_TYPES, formatDateTime, niceQuotes, plural, rub } from '../../utils/format'
import BriefPanel from './BriefPanel.vue'
import ContextFilesPanel from './ContextFilesPanel.vue'
import LinkTemplatePanel from './LinkTemplatePanel.vue'
import { useWs } from './context'
import TermHint from '../../components/TermHint.vue'
import UiCheckbox from '../../components/ui/UiCheckbox.vue'
import UiMenu from '../../components/ui/UiMenu.vue'
import UiMenuItem from '../../components/ui/UiMenuItem.vue'
import UiNumber from '../../components/ui/UiNumber.vue'
import UiSelect from '../../components/ui/UiSelect.vue'
import UiSwitch from '../../components/ui/UiSwitch.vue'
import ExpertHint from '../../components/ExpertHint.vue'
import { useExpert } from '../../composables/useExpert'

const COUNTER_TONES = { ok: 'tone-ok', unknown: 'tone-warn' }

const { ws, reload, patch } = useWs()
const { expert } = useExpert()
const { clients, workspaces } = useWorkspaces()
const route = useRoute()
const router = useRouter()

// --- Вкладки (макет «Диалог»): три в простом режиме, остальные — в экспертном. Вкладка — в #адресе. ----

const TABS = [
  { id: 'main', label: 'Основное', icon: SlidersHorizontal },
  { id: 'biz', label: 'О бизнесе', icon: FileText },
  { id: 'ai', label: 'Помощник', icon: Sparkles },
  { id: 'access', label: 'Доступ ИИ', icon: Lock, expert: true },
  { id: 'mem', label: 'Что ИИ запомнил', icon: Archive, expert: true },
  { id: 'cabs', label: 'Кабинеты и кампании', icon: Megaphone, expert: true },
  { id: 'utm', label: 'Шаблон ссылки', icon: Link2, expert: true },
  { id: 'client', label: 'Клиент', icon: Folder, expert: true },
]
// Старые якоря секций и вкладки, скрытые в простом режиме, ведут на ближайшую понятную вкладку.
const ALIASES = { brief: 'biz', 'context-files': 'biz', links: 'utm' }
const SIMPLE_FALLBACK = { access: 'ai', mem: 'ai' }

// Пока к проекту не привязано ни одной кампании, кабинеты нужны и в простом режиме — иначе их не подключить.
const tabs = computed(() =>
  TABS.filter((t) => expert.value || !t.expert || (t.id === 'cabs' && !ws.value.campaigns_count)),
)
const tab = computed(() => {
  const raw = route.hash.slice(1)
  const id = ALIASES[raw] || raw
  if (tabs.value.some((t) => t.id === id)) return id
  const fallback = SIMPLE_FALLBACK[id]
  return fallback && tabs.value.some((t) => t.id === fallback) ? fallback : tabs.value[0].id
})

function go(id) {
  router.replace({ query: route.query, hash: id === tabs.value[0].id ? '' : `#${id}` })
}

// --- Основное ---------------------------------------------------------------------------------

function generalFromWs(w) {
  return {
    name: w.name || '',
    site: w.site || '',
    kpi_type: w.kpi_type || '',
    kpi_target: w.kpi_target ?? '',
    monthly_budget_rub: w.monthly_budget_rub ?? '',
    goal_id: w.goal_id ?? null,
  }
}

const general = reactive(generalFromWs(ws.value))

// В обычном режиме — без жаргона, термин в скобках подсказки.
const kpiOptions = computed(() =>
  expert.value
    ? [
        { value: '', label: 'Не задан' },
        { value: 'cpa', label: 'CPA — цена конверсии', hint: 'Сколько стоит одна заявка или покупка, ₽' },
        { value: 'drr', label: 'ДРР — доля рекламных расходов', hint: 'Какая часть выручки уходит на рекламу, %' },
      ]
    : [
        { value: '', label: 'Не задан' },
        { value: 'cpa', label: 'Цена заявки', hint: 'Сколько стоит одна заявка или покупка, ₽ (CPA)' },
        { value: 'drr', label: 'Доля расходов на рекламу', hint: 'Какая часть выручки уходит на рекламу, % (ДРР)' },
      ],
)
const goalOptions = computed(() => [
  { value: null, label: 'Все цели (сумма конверсий)' },
  ...(ws.value.goals || []).map((g) => ({ value: g.goal_id, label: g.name, hint: `№ ${g.goal_id}` })),
])
const generalBusy = ref(false)
const generalError = ref('')
const goalsBusy = ref(false)

function numOrNull(value) {
  if (value === '' || value == null) return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

const generalPayload = computed(() => {
  const base = generalFromWs(ws.value)
  const out = {}
  for (const key of ['name', 'site', 'kpi_type', 'goal_id']) {
    if ((general[key] ?? null) !== (base[key] ?? null)) out[key] = general[key]
  }
  const kpiTarget = general.kpi_type ? numOrNull(general.kpi_target) : null
  if (kpiTarget !== numOrNull(base.kpi_target)) out.kpi_target = kpiTarget
  const budget = numOrNull(general.monthly_budget_rub)
  if (budget !== numOrNull(base.monthly_budget_rub)) out.monthly_budget_rub = budget
  return out
})
const generalChanged = computed(() => Object.keys(generalPayload.value).length > 0)

async function saveGeneral() {
  generalError.value = ''
  if (general.kpi_type && !(numOrNull(general.kpi_target) > 0)) {
    generalError.value = 'Укажите целевое значение больше нуля.'
    return
  }
  generalBusy.value = true
  try {
    const updated = await wsApi.update(ws.value.id, generalPayload.value)
    patch(updated)
    Object.assign(general, generalFromWs(updated))
    pushToast('Сохранено', 'ok')
  } catch (err) {
    generalError.value = errorText(err)
  } finally {
    generalBusy.value = false
  }
}

// --- Клиент (бывшая страница «Клиенты») ---------------------------------------------------------

const clientOptions = computed(() => clients.value.filter((c) => !c.archived || c.id === ws.value.client_id))
const clientChoices = computed(() => [
  { value: null, label: 'Без клиента' },
  ...clientOptions.value.map((c) => ({ value: c.id, label: `${niceQuotes(c.name)}${c.archived ? ' (в архиве)' : ''}` })),
])
const archivedClients = computed(() => clients.value.filter((c) => c.archived && c.id !== ws.value.client_id))
const currentClient = computed(() => clients.value.find((c) => c.id === ws.value.client_id) || null)
const clientProjects = computed(() => workspaces.value.filter((w) => w.client_id === ws.value.client_id))
const clientBusy = ref(false)
const clientDraft = reactive({ name: '', monthly_budget_rub: '' })
const clientEdit = reactive({ name: '', monthly_budget_rub: '', notes: '' })

function clientFields(c) {
  return { name: c?.name || '', monthly_budget_rub: c?.monthly_budget_rub ?? '', notes: c?.notes || '' }
}

watch(currentClient, (c) => Object.assign(clientEdit, clientFields(c)), { immediate: true })

const clientChanged = computed(() => {
  const base = clientFields(currentClient.value)
  return (
    clientEdit.name !== base.name ||
    clientEdit.notes !== base.notes ||
    numOrNull(clientEdit.monthly_budget_rub) !== numOrNull(base.monthly_budget_rub)
  )
})

async function clientRun(fn, done) {
  clientBusy.value = true
  try {
    await fn()
    if (done) pushToast(done, 'ok')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    clientBusy.value = false
  }
}

async function setProjectClient(clientId) {
  const updated = await wsApi.update(ws.value.id, { client_id: clientId })
  patch(updated)
}

function pickClient(value) {
  const id = value ? Number(value) : null
  if (id === (ws.value.client_id ?? null)) return
  return clientRun(async () => {
    await setProjectClient(id)
    await loadWorkspaces(true)
  }, id ? 'Проект в папке клиента' : 'Проект без клиента')
}

function createClient() {
  return clientRun(async () => {
    const created = await webApi.createClient({
      name: clientDraft.name,
      monthly_budget_rub: numOrNull(clientDraft.monthly_budget_rub),
    })
    await setProjectClient(created.id)
    clientDraft.name = ''
    clientDraft.monthly_budget_rub = ''
    await loadWorkspaces(true)
  }, 'Клиент создан, проект в нём')
}

function saveClient() {
  return clientRun(async () => {
    await webApi.patchClient(currentClient.value.id, {
      name: clientEdit.name,
      notes: clientEdit.notes,
      monthly_budget_rub: numOrNull(clientEdit.monthly_budget_rub),
    })
    await loadWorkspaces(true)
  }, 'Клиент сохранён')
}

function detachClient() {
  return clientRun(async () => {
    await setProjectClient(null)
    await loadWorkspaces(true)
  }, 'Проект без клиента')
}

function archiveClient(client, archived) {
  return clientRun(async () => {
    await webApi.patchClient(client.id, { archived })
    await loadWorkspaces(true)
  }, archived ? 'Клиент в архиве' : 'Клиент возвращён')
}

async function removeClient(client) {
  const left = workspaces.value.filter((w) => w.client_id === client.id).length
  const tail = left ? ` Его проекты (${left}) останутся, просто без клиента.` : ''
  if (!(await askConfirm(`Удалить клиента «${niceQuotes(client.name)}»?${tail}`))) return
  return clientRun(async () => {
    await webApi.deleteClient(client.id)
    if (ws.value.client_id === client.id) await reload()
    await loadWorkspaces(true)
  }, 'Клиент удалён')
}

async function syncGoals() {
  goalsBusy.value = true
  try {
    await webApi.syncGoals(ws.value.counter_row_id)
    await reload()
    pushToast(`Цели обновлены: ${ws.value.goals?.length || 0}`, 'ok')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    goalsBusy.value = false
  }
}

// --- Кабинеты ---------------------------------------------------------------------------------

const linked = ref(ws.value.accounts || [])
const candidates = ref([])
const accountsError = ref('')
const accountBusy = ref(null)
const addForm = reactive({ account_id: null, campaigns: 'matching' })
const available = computed(() => candidates.value.filter((a) => !a.linked))
const availableOptions = computed(() =>
  available.value.map((a) => ({
    value: a.id,
    label: a.name || a.client_login,
    hint: `кампаний ${a.campaigns_total}${a.campaigns_matching ? `, со счётчиком ${a.campaigns_matching}` : ''}`,
    disabled: !a.enabled,
  })),
)
const TAKE_CAMPAIGNS = [
  { value: 'matching', label: 'Забрать кампании с этим счётчиком' },
  { value: 'all', label: 'Забрать все свободные кампании' },
  { value: 'none', label: 'Без кампаний' },
]

async function loadAccounts() {
  accountsError.value = ''
  try {
    const [own, all] = await Promise.all([wsApi.accounts(ws.value.id), wsApi.accountCandidates(ws.value.id)])
    linked.value = own.items || []
    candidates.value = all.items || []
    if (addForm.account_id && !available.value.some((a) => a.id === addForm.account_id)) addForm.account_id = null
  } catch (err) {
    accountsError.value = errorText(err)
  }
}

async function afterStructureChange() {
  await Promise.all([loadAccounts(), loadCandidates(), reload()])
  loadWorkspaces(true).catch(() => {})
}

async function addAccount() {
  accountBusy.value = 'add'
  try {
    const res = await wsApi.addAccount(ws.value.id, addForm.account_id, addForm.campaigns)
    pushToast(`Кабинет подключён, кампаний добавлено: ${res.campaigns_added ?? 0}`, 'ok')
    addForm.account_id = null
    await afterStructureChange()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    accountBusy.value = null
  }
}

async function removeAccount(a) {
  if (
    !await askConfirm(
      `Отключить кабинет ${a.client_login}? Его кампании уйдут из проекта (в Директе ничего не изменится).`,
    )
  )
    return
  accountBusy.value = a.id
  try {
    const res = await wsApi.removeAccount(ws.value.id, a.id)
    pushToast(`Кабинет отключён, кампаний убрано: ${res.campaigns_removed ?? 0}`, 'ok')
    await afterStructureChange()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    accountBusy.value = null
  }
}

// --- Кампании ---------------------------------------------------------------------------------

const groups = ref([])
const candidatesLoading = ref(false)
const candidatesError = ref('')
const selected = ref(new Set())
const initial = ref(new Set())
const campaignSearch = ref('')
const onlyMatching = ref(false)
const showArchived = ref(false)
const campaignsBusy = ref(false)
const syncBusy = ref(false)
const syncText = ref('')

async function loadCandidates() {
  candidatesLoading.value = true
  candidatesError.value = ''
  try {
    const data = await wsApi.campaignCandidates(ws.value.id)
    groups.value = data.items || []
    const inThis = new Set()
    for (const g of groups.value) for (const c of g.campaigns) if (c.in_this) inThis.add(c.id)
    initial.value = inThis
    selected.value = new Set(inThis)
  } catch (err) {
    candidatesError.value = errorText(err)
  } finally {
    candidatesLoading.value = false
  }
}

const visibleGroups = computed(() => {
  const q = campaignSearch.value.toLowerCase()
  return groups.value
    .map((g) => ({
      ...g,
      items: g.campaigns.filter((c) => {
        if (selected.value.has(c.id) || initial.value.has(c.id)) {
          // Отмеченные видны всегда, кроме поиска.
        } else {
          if (!showArchived.value && c.state === 'ARCHIVED') return false
          if (onlyMatching.value && !c.matches_counter) return false
        }
        if (q && !c.name.toLowerCase().includes(q) && !String(c.campaign_id).includes(q)) return false
        return true
      }),
    }))
    .filter((g) => g.items.length)
})

const diff = computed(() => {
  const add = [...selected.value].filter((id) => !initial.value.has(id))
  const remove = [...initial.value].filter((id) => !selected.value.has(id))
  const addSet = new Set(add)
  let moving = 0
  for (const g of groups.value) for (const c of g.campaigns) if (addSet.has(c.id) && c.workspace_id) moving += 1
  return { add, remove, moving }
})

function toggle(id, on) {
  const next = new Set(selected.value)
  if (on) next.add(id)
  else next.delete(id)
  selected.value = next
}

function toggleGroup(group, on) {
  const next = new Set(selected.value)
  for (const c of group.items) {
    if (on) next.add(c.id)
    else next.delete(c.id)
  }
  selected.value = next
}

function resetSelection() {
  selected.value = new Set(initial.value)
}

async function saveCampaigns() {
  const { add, remove, moving } = diff.value
  if (
    moving &&
    !(await askConfirm(
      `${moving} ${plural(moving, 'кампания', 'кампании', 'кампаний')} сейчас ${moving === 1 ? 'в другом проекте' : 'в других проектах'}. Перенести сюда? Там ${moving === 1 ? 'она пропадёт' : 'они пропадут'} из списка.`,
      { confirmText: 'Перенести', danger: false },
    ))
  )
    return
  campaignsBusy.value = true
  try {
    const res = await wsApi.setCampaigns(ws.value.id, add, remove)
    pushToast(
      `Добавлено ${res.added}${res.moved ? ` (перенесено ${res.moved})` : ''}, убрано ${res.removed}`,
      'ok',
    )
    await afterStructureChange()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    campaignsBusy.value = false
  }
}

async function syncCampaigns() {
  syncBusy.value = true
  syncText.value = ''
  try {
    await wsApi.syncCampaigns(ws.value.id, (job) => {
      syncText.value = jobProgressText(job)
    })
    await afterStructureChange()
    pushToast('Кампании обновлены', 'ok')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    syncBusy.value = false
  }
}

// --- Доступ ИИ --------------------------------------------------------------------------------

function aiFromWs(w) {
  const access = w.ai_access || {}
  return {
    full_access: !!access.full_access,
    categories: { ...(access.categories || {}) },
    max_change_pct: access.max_change_pct ?? 30,
  }
}

const ai = reactive(aiFromWs(ws.value))

/** Что ИИ разрешено делать самому — по сохранённым настройкам проекта, для простого режима. */
const aiRights = computed(() => {
  const access = ws.value.ai_access || {}
  if (access.full_access) return ['любые действия в проекте, включая удаление']
  return (ws.value.ai_categories || []).filter((c) => access.categories?.[c.key]).map((c) => c.label.toLowerCase())
})
const aiBusy = ref(false)
const aiError = ref('')

const aiChanged = computed(() => JSON.stringify(aiFromWs(ws.value)) !== JSON.stringify(ai))

async function saveAi() {
  if (ai.full_access && !ws.value.ai_access?.full_access) {
    const ok = await askConfirm(
      'Включить полный доступ? ИИ сможет менять бюджеты, ставки, создавать и удалять кампании без вашего подтверждения.',
    )
    if (!ok) return
  }
  aiBusy.value = true
  aiError.value = ''
  try {
    const updated = await wsApi.update(ws.value.id, {
      ai_access: {
        full_access: ai.full_access,
        categories: ai.categories,
        max_change_pct: ai.max_change_pct,
      },
    })
    patch(updated)
    Object.assign(ai, aiFromWs(updated))
    pushToast('Доступ ИИ сохранён', 'ok')
  } catch (err) {
    aiError.value = errorText(err)
  } finally {
    aiBusy.value = false
  }
}

// --- Вкладка «Помощник»: тон, обращение, модель по умолчанию и заметки ------------------------------

const TONES = [
  { id: 'calm', label: 'Спокойный' },
  { id: 'sell', label: 'Продающий' },
  { id: 'expert', label: 'Экспертный' },
]
const ADDRESS = [
  { id: 'vy', label: 'На «вы»' },
  { id: 'ty', label: 'На «ты»' },
]

function styleFromWs(w) {
  const st = w.ai_style || {}
  return { tone: st.tone || 'calm', address: st.address || 'vy', model_class: st.model_class || '' }
}

const style = reactive(styleFromWs(ws.value))
const notes = ref(ws.value.ai_notes || '')
const notesBusy = ref(false)
const notesError = ref('')
const models = ref(null)
// Модель, которую админ выключил, показываем как «по умолчанию» — новые чаты всё равно пойдут на ней.
const styleModel = computed(() => {
  const m = models.value
  if (!m) return ''
  return m.items.some((c) => c.id === style.model_class) ? style.model_class : m.default
})
const helperChanged = computed(
  () => notes.value !== (ws.value.ai_notes || '') || JSON.stringify(style) !== JSON.stringify(styleFromWs(ws.value)),
)

/** Относительный расход модели: ●○○, ●●○, ●●●. */
function dots(n) {
  const k = Math.max(1, Math.min(3, Number(n) || 1))
  return '●'.repeat(k) + '○'.repeat(3 - k)
}

async function loadModels() {
  try {
    models.value = await aiApi.models()
  } catch {
    models.value = null
  }
}

async function saveHelper() {
  notesBusy.value = true
  notesError.value = ''
  try {
    const updated = await wsApi.update(ws.value.id, { ai_style: { ...style }, ai_notes: notes.value })
    patch(updated)
    Object.assign(style, styleFromWs(updated))
    pushToast('Настройки помощника сохранены', 'ok')
  } catch (err) {
    notesError.value = errorText(err)
  } finally {
    notesBusy.value = false
  }
}

const forgetting = ref('')

async function forgetFact(fact) {
  if (!await askConfirm(`Забыть ответ «${fact.question || fact.key}»? ИИ спросит об этом снова.`)) return
  forgetting.value = fact.key
  try {
    patch(await aiApi.forgetFact(ws.value.id, fact.key))
    pushToast('Ответ забыт', 'ok')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    forgetting.value = ''
  }
}

// --- Архив ------------------------------------------------------------------------------------

const archiveBusy = ref(false)

async function toggleArchive() {
  const archived = !ws.value.archived
  if (archived && !await askConfirm(`Отправить «${niceQuotes(ws.value.name)}» в архив?`)) return
  archiveBusy.value = true
  try {
    const updated = await wsApi.update(ws.value.id, { archived })
    patch(updated)
    loadWorkspaces(true).catch(() => {})
    pushToast(archived ? 'Проект в архиве' : 'Проект восстановлен', 'ok')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    archiveBusy.value = false
  }
}

// Удалить сайт из Толка; проект уходит в архив или удаляется — уходим к списку сайтов.
async function removeThisSite() {
  archiveBusy.value = true
  try {
    const out = await removeSite({ counterId: ws.value.counter_id, name: ws.value.name })
    if (out) router.push('/cabinet/connections')
  } finally {
    archiveBusy.value = false
  }
}

// Проект перечитали (например, после синхронизации целей) — подтягиваем несохранённые формы,
// только если пользователь их не трогал.
watch(
  () => ws.value,
  (next, prev) => {
    if (!next || !prev) return
    if (JSON.stringify(generalFromWs(prev)) === JSON.stringify(general)) Object.assign(general, generalFromWs(next))
    if (JSON.stringify(aiFromWs(prev)) === JSON.stringify(ai)) Object.assign(ai, aiFromWs(next))
    if ((prev.ai_notes || '') === notes.value) notes.value = next.ai_notes || ''
    if (JSON.stringify(styleFromWs(prev)) === JSON.stringify(style)) Object.assign(style, styleFromWs(next))
  },
)

onMounted(() => {
  loadWorkspaces().catch(() => {})
  loadAccounts()
  loadCandidates()
  loadModels()
})
</script>

<style scoped>
.danger-acts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.danger-acts .btn {
  gap: 6px;
}
.set {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  align-items: start;
  gap: 24px;
}
.snav {
  position: sticky;
  top: 16px;
  display: grid;
  gap: 2px;
}
.snav button {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 36px;
  padding: 0 10px;
  border: 0;
  border-radius: 9px;
  background: none;
  color: var(--ink-2);
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  text-align: left;
  white-space: nowrap;
  cursor: pointer;
}
.snav button:hover {
  background: color-mix(in srgb, var(--ink) 6%, transparent);
  color: var(--ink);
}
.snav button.active {
  background: var(--surface);
  color: var(--ink);
  box-shadow: 0 0 0 1px var(--line);
}
.set__body {
  min-width: 0;
}
.set__body > :first-child {
  margin-top: 0;
}
.notes-area {
  width: 100%;
}
.style-grid {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  align-items: center;
  gap: 12px 18px;
}
.style-grid__label {
  color: var(--muted);
  font-size: 14px;
}
@media (max-width: 640px) {
  .style-grid {
    grid-template-columns: minmax(0, 1fr);
    gap: 6px;
  }
  .style-grid__label:not(:first-child) {
    margin-top: 8px;
  }
}
.style-grid .seg,
.style-models {
  justify-self: start;
  max-width: 100%;
  overflow-x: auto;
}
.style-h {
  margin: 24px 0 4px;
  font-size: 15px;
}
.dots {
  margin-left: 4px;
  font-size: 10px;
  letter-spacing: 1px;
  opacity: 0.7;
}
.client-pick {
  max-width: 360px;
  margin-bottom: 16px;
}
.ai-simple {
  margin-top: 16px;
}
@media (max-width: 1100px) {
  .set {
    grid-template-columns: 190px minmax(0, 1fr);
    gap: 18px;
  }
}
@media (max-width: 760px) {
  .set {
    grid-template-columns: minmax(0, 1fr);
  }
  .snav {
    position: static;
    display: flex;
    overflow-x: auto;
    padding: 1px;
    scrollbar-width: none;
  }
  .snav button {
    flex: none;
  }
}
.ai-simple .prow__main {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.ai-simple .meta-note {
  margin: 0;
}
</style>
