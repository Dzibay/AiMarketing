<template>
  <template v-if="!expert">
    <UiNotice v-if="!briefFilled" tone="warn">
      Бриф клиента не заполнен — ИИ хуже понимает, что вы продаёте, и чаще ошибается.
      <template #action>
        <RouterLink class="btn btn--ghost btn--sm" :to="`/w/${ws.id}/settings#brief`">Заполнить бриф</RouterLink>
      </template>
    </UiNotice>

    <UiSkeleton v-if="loading && !data" variant="rows" :count="3" actions label="Загружаем запросы" />
    <div v-else-if="!data" class="ccard ccard--note">
      {{ loadError || 'Не удалось загрузить запросы.' }}
      <button class="btn btn--ghost" type="button" @click="load()">Повторить</button>
    </div>
    <div v-else-if="!campaigns.length" class="ccard ccard--note">
      В проекте нет поисковых кампаний — чистить нечего. Добавьте кампании в
      <RouterLink :to="`/w/${ws.id}/settings`">настройках</RouterLink>.
    </div>
    <template v-else>
      <div v-if="result" class="ccard tri-done">
        <div class="prow">
          <div class="prow__main">
            <strong>{{ resultTitle }}</strong>
            <span class="meta-note">
              Решения запомнили — эти запросы больше не попадут в разбор.
              <template v-if="result.problems.length">
                Не получилось с {{ result.problems.length }} — подробности в экспертном режиме.
              </template>
            </span>
          </div>
          <div class="prow__acts">
            <button v-if="forceable.length" class="btn btn--accent" type="button" :disabled="!!busy" @click="resolveNext">
              Как обойти ключи
            </button>
            <button v-if="forceable.length" class="btn btn--ghost" type="button" :disabled="!!busy" @click="applyForce">
              Всё равно добавить {{ forceable.length }}
            </button>
            <button v-if="result.log_id" class="btn btn--ghost" type="button" @click="undoId = result.log_id">
              Вернуть
            </button>
            <button class="btn btn--ghost" type="button" @click="result = null">Скрыть</button>
          </div>
        </div>
      </div>

      <section class="ccard tri-sum">
        <div class="tri-sum__text">
          <h2>{{ summary.title }}</h2>
          <p class="meta-note">{{ summary.note }}</p>
          <p v-if="busyText" class="sync-status">{{ busyText }}</p>
        </div>
        <div class="tri-sum__acts">
          <button
            v-if="summary.state === 'junk'"
            class="btn btn--accent"
            type="button"
            :disabled="!!busy"
            @click="minusAll"
          >
            {{ busy === 'apply' ? 'Применяем…' : summary.allLabel }}
          </button>
          <button
            v-if="summary.state === 'new' || (summary.state === 'junk' && counts.new)"
            class="btn"
            :class="summary.state === 'new' ? 'btn--accent' : 'btn--ghost'"
            type="button"
            :disabled="!!busy"
            @click="classify"
          >
            {{ busy === 'classify' ? 'Размечаем…' : summary.state === 'new' ? 'Разметить с ИИ' : 'Разметить новые' }}
          </button>
          <button
            class="btn"
            :class="summary.state === 'empty' ? 'btn--accent' : 'btn--ghost'"
            type="button"
            :disabled="!!busy"
            @click="sync"
          >
            {{ busy === 'sync' ? 'Загружаем…' : summary.state === 'empty' ? 'Загрузить из Директа' : 'Обновить из Директа' }}
          </button>
        </div>
      </section>

      <template v-if="junkGroups.length">
        <h2 class="section-h">
          Мусор по минус-фразам<TermHint term="minus" /> <span class="cgroup__n">{{ junkGroups.length }}</span>
          <small class="tri-note">одна минус-фраза отсекает всю группу</small>
        </h2>
        <div class="rgrid">
          <article v-for="g in junkGroups" :key="g.key" class="rcard">
            <header class="rcard__h">
              <h3>{{ g.title }}</h3>
              <span class="meta-note">
                {{ g.items.length }} {{ plural(g.items.length, 'запрос', 'запроса', 'запросов') }} ·
                {{ rubles(g.cost) }}
              </span>
            </header>
            <ul class="rcard__list">
              <li v-for="q in openAll[g.key] ? g.items : g.items.slice(0, GROUP_SHOWN)" :key="q.query_norm">
                <span class="rcard__q">
                  <span v-tip="openWhy[g.key] ? '' : reasonOf(q)">{{ q.query }}</span>
                  <button
                    v-if="g.misc || g.user"
                    v-tip="'Выбрать слова для минус-фразы'"
                    class="link-btn rcard__phrase"
                    type="button"
                    :disabled="!!busy"
                    @click="phraseRow = q"
                  >
                    {{ phraseLabel(q) }}
                  </button>
                  <small v-if="openWhy[g.key] && reasonOf(q)" class="rcard__reason">{{ reasonOf(q) }}</small>
                </span>
                <span class="rcard__cost">{{ rubles(q.cost_rub) }}</span>
                <button
                  v-tip="'Это не мусор — перенести в полезные'"
                  class="rcard__x"
                  type="button"
                  aria-label="Это не мусор"
                  :disabled="!!busy"
                  @click="setSimpleVerdict(q, 'target')"
                >
                  <X :size="14" aria-hidden="true" />
                </button>
              </li>
            </ul>
            <button
              v-if="g.items.length > GROUP_SHOWN"
              class="link-btn rcard__more"
              type="button"
              @click="openAll[g.key] = !openAll[g.key]"
            >
              {{ openAll[g.key] ? 'Свернуть' : `Показать ещё ${g.items.length - GROUP_SHOWN}` }}
            </button>
            <p v-if="openWhy[g.key] && !g.hasReasons" class="meta-note">ИИ не оставил пояснения.</p>
            <p v-if="g.conflicts" class="rcard__warn">
              {{ g.conflicts }} {{ plural(g.conflicts, 'запрос заденет', 'запроса заденут', 'запросов заденут') }} ваши
              ключевые фразы — их пропустим и предложим, как обойти.
            </p>
            <footer class="rcard__acts">
              <button class="btn btn--accent" type="button" :disabled="!!busy" @click="minusGroup(g)">
                Минусовать группу
              </button>
              <button class="btn btn--ghost" type="button" @click="openWhy[g.key] = !openWhy[g.key]">
                {{ openWhy[g.key] ? 'Скрыть причину' : 'Почему это мусор?' }}
              </button>
            </footer>
          </article>
        </div>
      </template>

      <div v-if="useful.length" class="ccard tri-ok">
        <div class="prow">
          <div class="prow__main">
            <span class="st st--ok">
              <CircleCheck :size="15" aria-hidden="true" />
              {{ num(local.target) }} {{ plural(local.target, 'полезный запрос', 'полезных запроса', 'полезных запросов') }}
            </span>
            <span class="meta-note">
              {{
                local.rescued
                  ? 'Отметки «не мусор» запомним, когда примените решения или нажмёте «Всё верно».'
                  : 'С ними всё в порядке — ИИ считает их целевыми.'
              }}
            </span>
          </div>
          <div class="prow__acts">
            <button class="btn btn--ghost" type="button" :disabled="!!busy" @click="keepTargets">Всё верно, запомнить</button>
            <button class="btn btn--ghost" type="button" @click="showTargets = !showTargets">
              {{ showTargets ? 'Скрыть' : 'Показать' }}
            </button>
          </div>
        </div>
        <ul v-if="showTargets" class="rcard__list tri-ok__list">
          <li v-for="q in useful.slice(0, 50)" :key="q.query_norm">
            <span class="rcard__q">
              {{ q.query }}
              <small v-if="reasonOf(q)" class="meta-note">{{ reasonOf(q) }}</small>
            </span>
            <span class="rcard__cost">
              {{ rubles(q.cost_rub) }}<template v-if="q.conversions"> · {{ num(q.conversions) }} конв.</template>
            </span>
            <button class="link-btn rcard__junk" type="button" :disabled="!!busy" @click="setSimpleVerdict(q, 'negative')">
              это мусор
            </button>
          </li>
        </ul>
        <p v-if="showTargets && useful.length > 50" class="meta-note">Показаны 50 самых дорогих.</p>
      </div>

      <QueryHighlights
        v-if="highlights && counts.all"
        :costly="highlights.costly"
        :removed="highlights.removed_30d"
        :workspace-id="ws.id"
        @changed="load()"
      />

      <QueryMinusDialog
        :row="phraseRow"
        :workspace-id="ws.id"
        :initial-phrase="phraseRow ? decisions[phraseRow.query_norm]?.negative_phrase || '' : null"
        :initial-level="phraseRow ? decisions[phraseRow.query_norm]?.level || '' : ''"
        pick
        @close="phraseRow = null"
        @pick="onPhrasePicked"
      />

      <ExpertHint>Фильтры, таблица всех запросов, ручная правка минус-фраз и память решений</ExpertHint>
    </template>
  </template>

  <template v-else>
  <div class="ws-toolbar">
    <div v-if="!phone || tab === 'memory'" class="seg" role="group" aria-label="Период загрузки из Директа">
      <button
        v-for="d in PERIODS"
        :key="d"
        type="button"
        :class="{ active: days === d }"
        :disabled="!!busy"
        @click="pickDays(d)"
      >
        {{ d }} дней
      </button>
    </div>
    <p class="sync-status">
      <template v-if="busyText">{{ busyText }}</template>
      <template v-else-if="data?.synced_at">
        Запросы за {{ formatDate(data.date_from) }} — {{ formatDate(data.date_to) }} · загружены
        {{ formatDateTime(data.synced_at) }}
      </template>
      <template v-else-if="data">Запросы ещё не загружали</template>
    </p>
    <div class="row-actions">
      <button
        class="btn btn--ghost"
        type="button"
        :disabled="!!busy"
        v-tip="`Загрузить поисковые запросы за последние ${days} дней`"
        @click="sync"
      >
        {{ busy === 'sync' ? 'Загружаем…' : 'Обновить из Директа' }}
      </button>
      <button
        class="btn btn--accent"
        type="button"
        :disabled="!!busy || !canClassify"
        v-tip="counts.new ? 'ИИ разберёт новые запросы и предложит минус-фразы' : 'Разметить заново неподтверждённые'"
        @click="classify"
      >
        {{ busy === 'classify' ? 'Размечаем…' : counts.new || !canClassify ? 'Разметить с ИИ' : 'Переразметить' }}
      </button>
    </div>
  </div>

  <UiNotice v-if="!briefFilled" tone="warn">
    Бриф клиента не заполнен — ИИ хуже понимает, что вы продаёте, и чаще ошибается. Черновик можно собрать по сайту.
    <template #action>
      <RouterLink class="btn btn--ghost btn--sm" :to="`/w/${ws.id}/settings#brief`">Заполнить бриф</RouterLink>
    </template>
  </UiNotice>

  <div v-if="data" class="stats-grid">
    <div class="stat-card">
      <small>Новые запросы</small>
      <strong>{{ num(counts.new) }}</strong>
      <span class="cell-note">расход {{ rubles(data.new_cost_rub) }}</span>
    </div>
    <div class="stat-card">
      <small>Предложено в минус<TermHint term="minus" /></small>
      <strong :class="{ 'tone-bad': counts.negative }">{{ num(counts.negative) }}</strong>
      <span class="cell-note">слили {{ rubles(data.negative_cost_rub) }} за период</span>
    </div>
    <div class="stat-card">
      <small>Предложено целевыми</small>
      <strong>{{ num(counts.target) }}</strong>
      <span class="cell-note">ждут подтверждения</span>
    </div>
    <div class="stat-card">
      <small>Решено</small>
      <strong>{{ num(counts.done) }}</strong>
      <span class="cell-note">в памяти — ИИ их не спрашивает</span>
    </div>
  </div>

  <nav class="seg seg--tabs" aria-label="Разделы запросов">
    <button
      type="button"
      :class="{ active: tab !== 'memory' }"
      :aria-current="tab !== 'memory' ? 'page' : undefined"
      @click="setTab(lastQueue)"
    >
      Разбор запросов
    </button>
    <button
      type="button"
      :class="{ active: tab === 'memory' }"
      :aria-current="tab === 'memory' ? 'page' : undefined"
      @click="setTab('memory')"
    >
      Память решений
    </button>
  </nav>

  <div v-if="result" class="notice queries-result" :class="{ 'notice--warn': result.status !== 'ok' }">
    <div class="queries-result__head">
      <strong>{{ resultTitle }}</strong>
      <div class="row-actions">
        <button v-if="result.log_id" class="btn btn--ghost" type="button" @click="undoId = result.log_id">
          Откатить
        </button>
        <button class="btn btn--ghost" type="button" @click="result = null">Скрыть</button>
      </div>
    </div>
    <ul v-if="result.problems.length">
      <li v-for="p in result.problems.slice(0, 20)" :key="p.query_norm">
        «{{ p.query }}»: {{ p.message || STATUS_TEXT[p.status] }}
        <template v-if="p.conflicts?.length">
          — {{ p.conflicts.map((k) => `«${k.keyword}»`).join(', ') }}
          <button class="link-btn" type="button" :disabled="!!busy" @click="openResolve(p)">Как обойти</button>
        </template>
      </li>
    </ul>
    <button v-if="forceable.length" class="btn btn--ghost" type="button" :disabled="!!busy" @click="applyForce">
      Всё равно добавить {{ forceable.length }}
      {{ plural(forceable.length, 'минус-фразу', 'минус-фразы', 'минус-фраз') }}
    </button>
  </div>

  <section v-if="tab !== 'memory'" class="panel">
    <UiListTools :active="sheetFilters" title="Фильтры и период" @reset="resetSheet">
      <template #search>
        <label class="ltools__search">
          <Search aria-hidden="true" />
          <input v-model.trim="filters.search" type="search" placeholder="Слово из запроса" aria-label="Поиск по запросам" />
        </label>
      </template>
      <template #tabs>
        <div class="seg" role="group" aria-label="Очередь запросов">
          <button
            v-for="t in QUEUE_TABS"
            :key="t.key"
            type="button"
            :class="{ active: tab === t.key }"
            :aria-pressed="tab === t.key"
            @click="setTab(t.key)"
          >
            {{ t.label }}<span v-if="data" class="seg__n">{{ num(counts[t.key]) }}</span>
          </button>
        </div>
      </template>
      <template #phone>
        <p class="ltools__cap">Период загрузки из Директа</p>
        <div class="seg" role="group" aria-label="Период загрузки из Директа">
          <button
            v-for="d in PERIODS"
            :key="d"
            type="button"
            :class="{ active: days === d }"
            :disabled="!!busy"
            @click="pickDays(d)"
          >
            {{ d }} дней
          </button>
        </div>
      </template>
      <UiSelect
        v-if="campaigns.length > 1"
        v-model="filters.campaign"
        size="sm"
        :options="campaignOptions"
        aria-label="Кампания"
      />
      <label class="ltools__field">
        Расход от
        <input v-model="filters.minCost" type="number" min="0" step="10" placeholder="0" aria-label="Расход от, ₽" />
        ₽
      </label>
      <UiSwitch v-model="filters.noConv" class="ltools__switch">Без конверсий</UiSwitch>
    </UiListTools>

    <UiSkeleton v-if="loading && !data" variant="table" :count="5" label="Загружаем запросы" />
    <div v-else-if="!data" class="empty">
      {{ loadError || 'Не удалось загрузить запросы.' }}
      <button class="btn btn--ghost" type="button" @click="load()">Повторить</button>
    </div>
    <EmptyState
      v-else-if="!campaigns.length"
      art="campaigns"
      text="В проекте нет поисковых кампаний — чистить нечего. Добавьте их в настройках проекта."
      plain
    >
      <template #action><RouterLink class="btn btn--ghost" :to="`/w/${ws.id}/settings`">Открыть настройки</RouterLink></template>
    </EmptyState>
    <EmptyState
      v-else-if="!counts.all && !hasFilters"
      art="queries"
      :text="`Запросов пока нет. Загрузим из Директа отчёт по поисковым запросам за ${days} дней — ИИ разметит мусор.`"
      plain
    >
      <template #action>
        <button class="btn btn--accent" type="button" :disabled="!!busy" @click="sync">
          {{ busy === 'sync' ? 'Загружаем…' : 'Загрузить из Директа' }}
        </button>
      </template>
    </EmptyState>
    <EmptyState v-else-if="!items.length" :art="hasFilters ? 'search' : 'done'" :text="emptyText" plain>
      <template v-if="hasFilters" #action>
        <button class="btn btn--ghost" type="button" @click="resetFilters">Сбросить фильтры</button>
      </template>
    </EmptyState>
    <template v-else>
      <div v-scroll-x class="table-scroll" :class="{ 'is-loading': loading }">
        <table v-cards class="table queries-table tcards">
          <thead>
            <tr>
              <th class="check-col">
                <UiCheckbox
                  :checked="allPicked"
                  :indeterminate="!allPicked && items.some((q) => selected.has(q.query_norm))"
                  aria-label="Выбрать все"
                  @change="toggleAll($event.target.checked)"
                />
              </th>
              <th>Запрос</th>
              <th class="num">Показы</th>
              <th class="num">Клики</th>
              <th class="num">Расход</th>
              <th class="num">Конв.</th>
              <th>Решение</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="q in items"
              :key="q.query_norm"
              :class="{ 'is-selected': selected.has(q.query_norm) }"
            >
              <td class="check-col">
                <UiCheckbox
                  :checked="selected.has(q.query_norm)"
                  :aria-label="`Выбрать «${q.query}»`"
                  @change="toggle(q.query_norm, $event.target.checked)"
                />
              </td>
              <td class="query-cell tc-name">
                <strong>{{ q.query }}</strong>
                <small class="cell-note">{{ placeText(q) }}</small>
                <small v-if="q.matched_keywords.length" class="cell-note">
                  по фразе {{ q.matched_keywords.map((k) => `«${k}»`).join(', ') }}
                </small>
                <small v-if="q.blocked_by" class="cell-note tone-ok">Уже отсекается: {{ q.blocked_by }}</small>
              </td>
              <td class="num" data-label="Показы">{{ num(q.impressions) }}</td>
              <td class="num tc-main" data-label="Клики">{{ num(q.clicks) }}</td>
              <td class="num tc-main" data-label="Расход">{{ rubles(q.cost_rub) }}</td>
              <td
                class="num tc-main"
                :class="{ 'tone-bad': !q.conversions && q.cost_rub > 0 }"
                data-label="Конв."
              >
                {{ num(q.conversions) }}
              </td>
              <td class="decision tc-wide">
                <div class="decision__top">
                  <div class="seg seg--sm" role="group" aria-label="Решение">
                    <button
                      type="button"
                      :class="{ active: decisions[q.query_norm].verdict === 'negative' }"
                      @click="setVerdict(q, 'negative')"
                    >
                      Минус
                    </button>
                    <button
                      type="button"
                      :class="{ active: decisions[q.query_norm].verdict === 'target' }"
                      @click="setVerdict(q, 'target')"
                    >
                      Целевой
                    </button>
                  </div>
                  <span v-if="q.label" class="chip" :class="SOURCE_CHIPS[q.label.source] || ''">
                    {{ SOURCES[q.label.source] || q.label.source }}
                  </span>
                  <span v-if="q.label?.confirmed" class="chip chip--ok">
                    {{ q.label.applied_at ? 'применено' : 'решено' }}
                  </span>
                </div>
                <p v-if="q.label?.reason" class="decision__reason">{{ q.label.reason }}</p>
                <template v-if="decisions[q.query_norm].verdict === 'negative'">
                  <div class="decision__neg">
                    <input
                      v-model="decisions[q.query_norm].negative_phrase"
                      type="text"
                      maxlength="4096"
                      aria-label="Минус-фраза"
                      placeholder="весь запрос целиком"
                      @input="touch(q.query_norm)"
                      @change="scheduleCheck([q.query_norm])"
                    />
                    <UiSelect
                      v-model="decisions[q.query_norm].level"
                      size="sm"
                      :options="levelOptions(q)"
                      aria-label="Куда добавить"
                      @change="touch(q.query_norm); scheduleCheck([q.query_norm])"
                    />
                  </div>
                  <QueryWords
                    class="decision__words"
                    small
                    :query="q.query"
                    :model-value="decisions[q.query_norm].negative_phrase"
                    @update:model-value="setPhrase(q, $event)"
                  />
                  <p v-if="checkOf(q)?.problems.length" class="decision__warn tone-bad">
                    {{ checkOf(q).problems.join('; ') }}
                  </p>
                  <p v-if="checkOf(q)?.total" class="decision__warn tone-warn">
                    Заденет ваши ключи: {{ conflictText(checkOf(q)) }}
                    <button class="link-btn" type="button" :disabled="!!busy" @click="openResolveRow(q)">
                      Как обойти
                    </button>
                  </p>
                  <p v-if="checkOf(q)?.targetsTotal" class="decision__warn tone-warn">
                    Отсечёт и нужные запросы — целевые или с заявками: {{ keptText(checkOf(q)) }}
                  </p>
                </template>
                <div v-else-if="decisions[q.query_norm].verdict === 'target'" class="check-row decision__kw">
                  <UiCheckbox
                    v-model="decisions[q.query_norm].add_keyword"
                    :disabled="!q.adgroups.length"
                    @change="touch(q.query_norm)"
                  >
                    {{ q.adgroups.length ? 'Добавить в ключевые фразы' : 'Нет группы — в ключи не добавить' }}
                  </UiCheckbox>
                  <UiSelect
                    v-if="decisions[q.query_norm].add_keyword && q.adgroups.length > 1"
                    v-model="decisions[q.query_norm].adgroup_row_id"
                    size="sm"
                    :options="q.adgroups.map((g) => ({ value: g.id, label: groupLabel(q, g) }))"
                    aria-label="Группа"
                    @change="touch(q.query_norm)"
                  />
                </div>
                <p
                  v-if="rowResults[q.query_norm] && rowResults[q.query_norm].status !== 'ok'"
                  class="decision__warn"
                  :class="rowResults[q.query_norm].status === 'error' ? 'tone-bad' : 'tone-warn'"
                >
                  {{ STATUS_TEXT[rowResults[q.query_norm].status] }}:
                  {{ rowResults[q.query_norm].message || 'без подробностей' }}
                </p>
              </td>
              <UiRowMore />
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="items.length < (data.total || 0)" class="pager">
        <span class="sync-status">Показано {{ items.length }} из {{ data.total }}</span>
        <button class="btn btn--ghost" type="button" :disabled="loading" @click="load({ more: true })">
          {{ loading ? 'Загружаем…' : 'Показать ещё' }}
        </button>
      </div>
    </template>

    <div v-if="selected.size" class="bulk-bar queries-applybar">
      <span>Выбрано: {{ selected.size }}</span>
      <div class="bulk-dock__acts">
        <button class="btn btn--ghost" type="button" :disabled="!!busy" @click="markSelected('negative')">В минус</button>
        <button class="btn btn--ghost" type="button" :disabled="!!busy" @click="markSelected('target')">Целевые</button>
        <UiSelect
          :model-value="null"
          :options="BULK_LEVELS"
          placeholder="Уровень минус-фраз…"
          aria-label="Уровень минус-фраз"
          :disabled="!!busy"
          @change="setLevelSelected"
        />
        <button class="btn btn--accent" type="button" :disabled="!!busy" @click="openApply">
          {{ busy === 'apply' ? 'Применяем…' : `Применить (${readyCount})` }}
        </button>
      </div>
      <button class="btn btn--ghost" type="button" @click="selected = new Set()">Снять выбор</button>
    </div>
  </section>

  <section v-else class="panel">
    <div class="panel__head">
      <div>
        <h2>Память решений</h2>
        <p class="hint">
          Запросы, по которым вы уже решили. Повторно их не размечаем, а ИИ берёт их как примеры. «Забыть» вернёт
          запрос в разбор; минус-фразы в Директе останутся — их можно откатить в журнале.
        </p>
      </div>
    </div>
    <UiListTools :active="mem.verdict ? 1 : 0" @reset="mem.verdict = ''">
      <template #search>
        <label class="ltools__search">
          <Search aria-hidden="true" />
          <input v-model.trim="mem.search" type="search" placeholder="Запрос или минус-фраза" aria-label="Поиск по памяти" />
        </label>
      </template>
      <UiSelect v-model="mem.verdict" size="sm" :options="VERDICTS" aria-label="Решение" />
    </UiListTools>
    <div v-if="mem.selected.size && !phone" class="bulk-bar">
      <span>Выбрано: {{ mem.selected.size }}</span>
      <button class="btn btn--ghost" type="button" :disabled="mem.busy" @click="forget">Забыть</button>
      <button class="btn btn--ghost" type="button" @click="mem.selected = new Set()">Снять выбор</button>
    </div>
    <UiSkeleton v-if="mem.loading && !mem.items.length" variant="text" :count="3" label="Загружаем" />
    <p v-else-if="mem.error" class="form-error">{{ mem.error }}</p>
    <p v-else-if="!mem.items.length" class="empty">
      {{ mem.search || mem.verdict ? 'Ничего не нашлось.' : 'Пока пусто: решения появятся после «Применить».' }}
    </p>
    <div v-else v-scroll-x class="table-scroll" :class="{ 'is-loading': mem.loading }">
      <table v-cards class="table tcards">
        <thead>
          <tr>
            <th class="check-col">
              <UiCheckbox
                :checked="memAllPicked"
                :indeterminate="!memAllPicked && mem.items.some((m) => mem.selected.has(m.query_norm))"
                aria-label="Выбрать все"
                @change="memToggleAll($event.target.checked)"
              />
            </th>
            <th>Запрос</th>
            <th>Решение</th>
            <th>Минус-фраза</th>
            <th>Когда</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in mem.items" :key="m.query_norm">
            <td class="check-col">
              <UiCheckbox
                :aria-label="`Выбрать «${m.query}»`"
                :checked="mem.selected.has(m.query_norm)"
                @change="memToggle(m.query_norm, $event.target.checked)"
              />
            </td>
            <td class="tc-name">
              {{ m.query }}
              <small v-if="m.reason" class="cell-note">{{ m.reason }}</small>
            </td>
            <td class="tc-tag">
              <span class="chip" :class="m.verdict === 'negative' ? 'chip--bad' : 'chip--ok'">
                {{ m.verdict === 'negative' ? 'минус' : 'целевой' }}
              </span>
            </td>
            <td data-label="Минус-фраза">
              {{ m.verdict === 'negative' && m.negative_phrase ? `${m.negative_phrase} · ${LEVELS[m.level] || m.level}` : '—' }}
            </td>
            <td data-label="Когда">
              {{ formatDateTime(m.updated_at) }}
              <small v-if="m.applied_at" class="cell-note">
                {{ m.verdict === 'negative' ? 'есть в Директе' : 'добавлено в ключи' }}
              </small>
            </td>
            <UiRowMore />
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="mem.items.length < mem.total" class="pager">
      <span class="sync-status">Показано {{ mem.items.length }} из {{ mem.total }}</span>
      <button class="btn btn--ghost" type="button" :disabled="mem.loading" @click="loadMemory(true)">Показать ещё</button>
    </div>
    <!-- Телефон: выбор начинается долгим нажатием на карточку, поэтому действия — внизу, над нижней панелью. -->
    <div v-if="mem.selected.size && phone" class="bulk-bar queries-applybar">
      <span>Выбрано: {{ mem.selected.size }}</span>
      <button class="btn btn--ghost btn--sm" type="button" :disabled="mem.busy" @click="forget">Забыть</button>
      <button class="btn btn--ghost btn--sm" type="button" @click="mem.selected = new Set()">Снять выбор</button>
    </div>
  </section>
  </template>

  <Modal :open="applyModal.open" title="Применить решения" :locked="busy === 'apply'" @close="applyModal.open = false">
    <ul class="apply-summary">
      <li v-if="plan.negative">
        Запросов в минус: <strong>{{ plan.negative }}</strong>
        <span class="cell-note">
          <template v-if="plan.negative > plan.known">на группы — {{ plan.groups }}, на кампании — {{ plan.campaigns }}</template
          ><template v-if="plan.known"
            >{{ plan.negative > plan.known ? '; ещё ' : '' }}{{ plan.known }} уже отсекаются в Директе — их только
            запомним</template
          >
        </span>
      </li>
      <li v-if="plan.target">
        Отметить целевыми: <strong>{{ plan.target }}</strong>
        <span v-if="plan.keywords" class="cell-note">из них в ключевые фразы — {{ plan.keywords }}</span>
      </li>
    </ul>
    <p v-if="applyModal.skipped" class="field-hint">
      Без решения {{ applyModal.skipped }} {{ plural(applyModal.skipped, 'запрос', 'запроса', 'запросов') }} — их
      пропустим.
    </p>
    <p v-if="plan.conflicts" class="notice notice--warn">
      {{ plan.conflicts }} {{ plural(plan.conflicts, 'минус-фраза пересекается', 'минус-фразы пересекаются', 'минус-фраз пересекаются') }}
      с вашими ключевыми фразами — такие пропустим, добавить их можно будет отдельно.
    </p>
    <div v-if="plan.keywords" v-field class="field">
      <label>Ставка для новых ключевых фраз, ₽</label>
      <input v-model="applyModal.bid" type="number" min="0.3" step="0.1" placeholder="по умолчанию Директа" />
      <small class="field-hint">Пусто — подойдёт для автостратегий: ставку назначит Директ.</small>
    </div>
    <p class="field-hint">
      Решения запомним: эти запросы больше не попадут в разбор, а ИИ будет на них учиться. Действие появится в журнале
      — его можно откатить.
    </p>
    <p v-if="busy === 'apply'" class="sync-status">{{ busyText }}</p>
    <template #footer>
      <button class="btn btn--ghost" type="button" :disabled="busy === 'apply'" @click="applyModal.open = false">
        Отмена
      </button>
      <button class="btn btn--accent" type="button" :disabled="busy === 'apply'" @click="confirmApply">
        {{ busy === 'apply' ? 'Применяем…' : 'Применить' }}
      </button>
    </template>
  </Modal>

  <Modal :open="estimate.open" title="Разметка с ИИ" @close="estimate.open = false">
    <template v-if="estimate.est">
      <ul class="apply-summary">
        <li>
          Запросов к разметке: <strong>{{ num(estimate.est.queries) }}</strong>
          <span v-if="estimate.est.rules" class="cell-note">
            {{ num(estimate.est.rules) }} закроют память решений, бриф и Директ — без ИИ
          </span>
        </li>
        <li>
          Уйдёт в ИИ: <strong>{{ num(estimate.est.ai) }}</strong>
          <span class="cell-note">
            {{ estimate.est.batches }} {{ plural(estimate.est.batches, 'вызов', 'вызова', 'вызовов') }} модели
          </span>
        </li>
        <li v-if="estimate.est.percent !== null">
          Расход ≈ <strong>{{ limitPct(estimate.est.percent) }}</strong> месячного лимита
          <span v-if="estimate.est.left_percent !== null" class="cell-note">
            осталось {{ limitPct(estimate.est.left_percent) }}
          </span>
        </li>
      </ul>
      <p v-if="estimateShort" class="notice notice--warn">
        <template v-if="estimate.est.fits">
          В остаток лимита влезет ≈ {{ num(estimate.est.fits) }} из {{ num(estimate.est.ai) }} — остальные подождут
          следующего месяца или повышения лимита.
        </template>
        <template v-else>
          Остатка лимита не хватит даже на один вызов — ИИ не запустится.
          <template v-if="estimate.est.rules">Разметим только то, что закрывают правила.</template>
        </template>
      </p>
      <p class="field-hint">
        {{
          estimate.est.calibrated
            ? 'Оценка — по прошлым вызовам этой модели.'
            : 'Оценка приблизительная: после первых разметок станет точнее.'
        }}
        Остаток лимита — в настройках.
      </p>
    </template>
    <template #footer>
      <button class="btn btn--ghost" type="button" @click="estimate.open = false">Отмена</button>
      <button
        class="btn btn--accent"
        type="button"
        :disabled="estimateShort && !estimate.est.fits && !estimate.est.rules"
        @click="confirmClassify"
      >
        {{ estimateShort && !estimate.est.fits ? 'Разметить правилами' : 'Разметить' }}
      </button>
    </template>
  </Modal>

  <QueryConflictDialog
    :row="conflict.row"
    :workspace-id="ws.id"
    :phrase="conflict.phrase"
    :level="conflict.level"
    @close="conflict.row = null"
    @done="onResolved"
  />

  <UndoDialog :log-id="undoId" @close="undoId = null" @done="onUndone" />
</template>

<script setup>
import { usePageContext } from '../../composables/usePageContext'
import { lastDays as ctxDays } from '../../utils/format'
import UiSkeleton from '../../components/ui/UiSkeleton.vue'
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import EmptyState from '../../components/ui/EmptyState.vue'
import QueryHighlights from '../../components/QueryHighlights.vue'
import QueryConflictDialog from '../../components/QueryConflictDialog.vue'
import QueryMinusDialog from '../../components/QueryMinusDialog.vue'
import QueryWords from '../../components/QueryWords.vue'
import UiCheckbox from '../../components/ui/UiCheckbox.vue'
import UiListTools from '../../components/ui/UiListTools.vue'
import UiRowMore from '../../components/ui/UiRowMore.vue'
import UiSelect from '../../components/ui/UiSelect.vue'
import UiNotice from '../../components/ui/UiNotice.vue'
import UiSwitch from '../../components/ui/UiSwitch.vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { CircleCheck, Search, X } from '@lucide/vue'
import ExpertHint from '../../components/ExpertHint.vue'
import Modal from '../../components/ui/Modal.vue'
import { useExpert } from '../../composables/useExpert'
import { useViewport } from '../../composables/useViewport'
import TermHint from '../../components/TermHint.vue'
import UndoDialog from '../../components/UndoDialog.vue'
import { errorText, jobProgressText, wsApi } from '../../api/web'
import { askConfirm } from '../../composables/useConfirm'
import { pushToast } from '../../composables/useToasts'
import { undoAction } from '../../composables/useUndo'
import { campaignLabel, repeatedNames } from '../../utils/campaignNames'
import { aiUsageText, formatDate, formatDateTime, limitPct, num, plural, rubles } from '../../utils/format'
import { commonWords, phraseCovers } from '../../utils/queryWords'
import { useWs } from './context'

const TABS = [
  { key: 'new', label: 'Новые' },
  { key: 'negative', label: 'К минусу' },
  { key: 'target', label: 'Целевые' },
  { key: 'done', label: 'Решено' },
  { key: 'all', label: 'Все' },
  { key: 'memory', label: 'Память' },
]
// Очередь — сегменты в строке фильтров; «Память решений» — отдельная вкладка рядом с разбором.
const QUEUE_TABS = TABS.filter((t) => t.key !== 'memory')
const SOURCES = { ai: 'ИИ', memory: 'по памяти', brief: 'по брифу', direct: 'уже в Директе', rule: 'автоматика', user: 'вы' }
const SOURCE_CHIPS = { ai: 'chip--src-ai', memory: 'chip--src-rule', brief: 'chip--src-rule', direct: 'chip--src-rule', rule: 'chip--src-rule' }
const LEVELS = { adgroup: 'на группу', campaign: 'на кампанию' }
const STATUS_TEXT = { ok: 'Готово', partial: 'Частично', skipped: 'Пропущено', error: 'Ошибка' }
const PAGE = 100
const EMPTY_COUNTS = { new: 0, negative: 0, target: 0, done: 0, all: 0 }

const route = useRoute()
const router = useRouter()
const { ws } = useWs()
const { expert } = useExpert()

const data = ref(null)
const items = ref([])
const loading = ref(false)
const loadError = ref('')
const tab = ref(TABS.some((t) => t.key === route.query.tab) ? route.query.tab : '')
const PERIODS = [7, 14, 30]
const days = ref(14)
// Пока период не выбирали руками, он повторяет уже загруженный — чип не спорит с подписью «Запросы за …».
const daysPicked = ref(false)
const busy = ref('') // sync | classify | apply | check (ждём проверку и ответ про общее слово)
const busyText = ref('')
const filters = reactive({ search: '', campaign: '', minCost: '', noConv: false })
const selected = ref(new Set())
// Решения по строкам живут на клиенте до «Применить»: query_norm → { verdict, negative_phrase, level, … }.
const decisions = reactive({})
const checks = reactive({})
const rowResults = reactive({})
const result = ref(null)
const undoId = ref(null)
const applyModal = reactive({ open: false, items: [], skipped: 0, bid: '' })
// Оценка перед массовой разметкой: объём, доля и остаток месячного лимита.
const estimate = reactive({ open: false, est: null, payload: null })
const estimateShort = computed(() => {
  const est = estimate.est
  return !!est && est.fits !== null && est.fits < est.ai
})
const mem = reactive({ items: [], total: 0, loading: false, busy: false, error: '', search: '', verdict: '', selected: new Set() })

const counts = computed(() => data.value?.counts || EMPTY_COUNTS)
const campaigns = computed(() => data.value?.campaigns || [])
const repeatedCampaigns = computed(() => repeatedNames(campaigns.value.map((c) => c.name)))
const campaignOptions = computed(() => [
  { value: '', label: 'Все кампании' },
  ...campaigns.value.map((c) => ({ value: String(c.id), label: campaignLabel(c, repeatedCampaigns.value) })),
])
const BULK_LEVELS = [
  { value: 'adgroup', label: 'Все на группу' },
  { value: 'campaign', label: 'Все на кампанию' },
]
const VERDICTS = [
  { value: '', label: 'Все решения' },
  { value: 'negative', label: 'В минус' },
  { value: 'target', label: 'Целевые' },
]

function levelOptions(q) {
  return [
    { value: 'adgroup', label: 'на группу', disabled: !q.adgroups.length },
    { value: 'campaign', label: 'на кампанию' },
  ]
}

// Группы с одинаковым названием (обычно из копии кампании) различаем кампанией.
function groupLabel(q, g) {
  const name = g.name || `группа ${g.id}`
  if (!repeatedNames(q.adgroups.map((x) => x.name || `группа ${x.id}`)).has(name)) return name
  const camp = campaigns.value.find((c) => c.id === g.campaign_row_id)
  return camp ? `${name} · ${campaignLabel(camp, repeatedCampaigns.value)}` : `${name} · №${g.id}`
}

const hasFilters = computed(() => !!(filters.search || filters.campaign || filters.minCost || filters.noConv))
const { phone } = useViewport()
// Телефон: сколько фильтров включено в шторке (поиск — не в ней) и их сброс.
const sheetFilters = computed(() => (filters.campaign ? 1 : 0) + (filters.minCost ? 1 : 0) + (filters.noConv ? 1 : 0))

function resetFilters() {
  filters.search = ''
  resetSheet()
}

function resetSheet() {
  filters.campaign = ''
  filters.minCost = ''
  filters.noConv = false
}
const canClassify = computed(() => counts.value.new > 0 || counts.value.negative + counts.value.target > 0)
const briefFilled = computed(() => {
  const b = ws.value.brief || {}
  return !!(b.product || b.geo || b.notes || (b.not_ours || []).length)
})
const byNorm = computed(() => Object.fromEntries(items.value.map((q) => [q.query_norm, q])))
const readyCount = computed(() => [...selected.value].filter((n) => decisions[n]?.verdict).length)
const emptyText = computed(() => {
  if (hasFilters.value) return 'По фильтрам ничего не нашлось.'
  if (tab.value === 'new') return 'Новых запросов нет — всё разобрано.'
  if (tab.value === 'negative')
    return counts.value.new ? 'Предложений пока нет — нажмите «Разметить с ИИ».' : 'Предложений в минус нет.'
  if (tab.value === 'target') return 'Целевых предложений нет.'
  if (tab.value === 'done') return 'Решений пока нет.'
  return 'Запросов нет.'
})

const plan = computed(() => {
  const list = applyModal.items
  const neg = list.filter((i) => i.verdict === 'negative')
  // Та же фраза уже отсекает запрос везде — в Директ ничего не уйдёт, решение просто запомним.
  const known = neg.filter((i) => {
    const row = byNorm.value[i.query_norm]
    // Пустая фраза — значит, минусуем запрос целиком.
    const phrase = (i.negative_phrase || row?.query || '').trim().toLowerCase()
    return row?.blocked_by && row.blocked_by.toLowerCase() === phrase
  })
  const fresh = neg.filter((i) => !known.includes(i))
  return {
    negative: neg.length,
    known: known.length,
    groups: fresh.filter((i) => i.level === 'adgroup').length,
    campaigns: fresh.filter((i) => i.level === 'campaign').length,
    target: list.length - neg.length,
    keywords: list.filter((i) => i.add_keyword).length,
    conflicts: neg.filter((i) => byNorm.value[i.query_norm] && checkOf(byNorm.value[i.query_norm])?.total).length,
  }
})

const resultTitle = computed(() => {
  const r = result.value
  if (!r) return ''
  const parts = []
  if (r.negative) parts.push(`в минус ${r.negative}`)
  if (r.target) parts.push(`целевых ${r.target}`)
  if (r.keywords) parts.push(`новых ключевых фраз ${r.keywords}`)
  if (r.skipped) parts.push(`пропущено ${r.skipped}`)
  if (r.errors) parts.push(`ошибок ${r.errors}`)
  return parts.length ? `Применено: ${parts.join(', ')}` : 'Ничего не изменилось'
})
const forceable = computed(() => (result.value?.problems || []).filter((p) => p.status === 'skipped' && p.conflicts))

// --- Простой режим: мусор группами по минус-фразе, полезные свёрнуты ---

const SIMPLE_LIMIT = 300 // больше сервер за раз не отдаёт
const GROUP_SHOWN = 5
const targets = ref([])
const showTargets = ref(false)
const openWhy = reactive({})
// Группа мусора раскрыта целиком (по умолчанию видно GROUP_SHOWN строк).
const openAll = reactive({})
// Запрос, у которого выбираем слова минус-фразы (окно со словами-«таблетками»).
const phraseRow = ref(null)
const highlights = ref(null)

function phraseOf(q) {
  return (decisions[q.query_norm]?.negative_phrase || q.query).trim().toLowerCase()
}

function phraseLabel(q) {
  const own = (decisions[q.query_norm]?.negative_phrase || '').trim()
  return !own || phraseOf(q) === q.query_norm ? 'минус — весь запрос' : `минус «${phraseOf(q)}»`
}

/** Решение по строке поменяли вы: «не мусор» у мусора ИИ или «это мусор» у полезного. */
function movedByUser(q) {
  const d = decisions[q.query_norm]
  return !!d?.verdict && d.verdict !== (q.label?.verdict || null)
}

/** Причина у каждой строки: от ИИ, а если решение поменяли вы — об этом. */
function reasonOf(q) {
  if (movedByUser(q)) return decisions[q.query_norm].verdict === 'target' ? 'Вы отметили: не мусор' : 'Вы отметили как мусор'
  return q.label?.reason || ''
}

const sumCost = (list) => list.reduce((s, q) => s + (q.cost_rub || 0), 0)

// Простой режим: мусор и полезные — по решениям на экране (до применения они живут здесь, как и в таблице).
const junk = computed(() =>
  [...items.value, ...targets.value].filter((q) => decisions[q.query_norm]?.verdict === 'negative'),
)
const useful = computed(() => {
  const list = [...items.value, ...targets.value].filter((q) => decisions[q.query_norm]?.verdict === 'target')
  // Перенесённые вами — первыми: их видно сразу, и вернуть можно тут же.
  return [...list.filter(movedByUser), ...list.filter((q) => !movedByUser(q))]
})
// Счётчики сервера с поправкой на правки: загружено не больше SIMPLE_LIMIT, остальное — как пришло.
const local = computed(() => {
  const c = counts.value
  const d = data.value || {}
  return {
    negative: Math.max(0, c.negative - items.value.length) + junk.value.length,
    negativeCost: Math.max(0, (d.negative_cost_rub || 0) - sumCost(items.value)) + sumCost(junk.value),
    target: Math.max(0, c.target - targets.value.length) + useful.value.length,
    edited: [...items.value, ...targets.value].some(movedByUser),
    rescued: items.value.some((q) => decisions[q.query_norm]?.verdict === 'target'),
  }
})

function makeGroup(key, title, list, kind = '') {
  return {
    key,
    title,
    misc: kind === 'misc',
    user: kind === 'user',
    items: list,
    cost: sumCost(list),
    // Фразу поменяли — считаем по свежей проверке, иначе по метке ИИ.
    conflicts: list.filter((q) => (checkOf(q) || { total: q.conflicts_total }).total).length,
    hasReasons: list.some((q) => reasonOf(q)),
  }
}

// У ИИ нет категорий причин, зато общая минус-фраза — естественная группа: одна фраза отсекает их все.
const junkGroups = computed(() => {
  const by = new Map()
  for (const q of junk.value) {
    const phrase = phraseOf(q)
    if (!by.has(phrase)) by.set(phrase, [])
    by.get(phrase).push(q)
  }
  const groups = []
  const mine = []
  const rest = []
  for (const [phrase, list] of by) {
    if (list.length > 1) groups.push(makeGroup(phrase, `«${phrase}»`, list))
    else if (movedByUser(list[0])) mine.push(list[0])
    else rest.push(list[0])
  }
  groups.sort((a, b) => b.cost - a.cost)
  if (mine.length) groups.push(makeGroup('+', 'Отмечено вами', mine, 'user'))
  if (rest.length) groups.push(makeGroup('*', groups.length ? 'Разные запросы' : 'Мусорные запросы', rest, 'misc'))
  return groups
})

/**
 * «Это не мусор» / «это мусор» в простом режиме — только решение на экране, в Директ уходит по «Минусовать».
 * Полезный запрос, который уже отсекает фраза одной из групп ИИ, попадает в эту группу, иначе — в «Отмечено вами».
 */
function setSimpleVerdict(q, verdict) {
  const d = decisions[q.query_norm]
  if (!d) return
  d.verdict = verdict
  if (verdict === 'negative' && !d.negative_phrase.trim()) {
    const group = junkGroups.value
      .filter((g) => !g.misc && !g.user && phraseCovers(g.key, q.query))
      .sort((a, b) => b.items.length - a.items.length)[0]
    if (group) d.negative_phrase = group.key
  }
  if (verdict === 'target') showTargets.value = true
  touch(q.query_norm)
  if (verdict === 'negative') scheduleCheck([q.query_norm])
}

function onPhrasePicked({ phrase, level }) {
  const q = phraseRow.value
  phraseRow.value = null
  const d = q && decisions[q.query_norm]
  if (!d) return
  d.negative_phrase = phrase
  d.level = levelFor(q, level || d.level)
  touch(q.query_norm)
  scheduleCheck([q.query_norm])
}

function periodDays() {
  const d = data.value
  if (!d?.date_from || !d?.date_to) return 0
  return Math.round((Date.parse(d.date_to) - Date.parse(d.date_from)) / 86_400_000) + 1
}

const summary = computed(() => {
  const c = counts.value
  const d = data.value || {}
  if (!c.all)
    return {
      state: 'empty',
      title: 'Запросов пока нет',
      note: `Загрузим из Директа, по каким запросам показывалась реклама за ${days.value} дней, и ИИ разберёт их.`,
    }
  const own = local.value
  if (own.negative) {
    const span = periodDays()
    const weekly = span > 0 ? Math.round((own.negativeCost / span) * 7) : 0
    const n = junkGroups.value.length
    const what = `${num(own.negative)} ${plural(own.negative, 'мусорный запрос', 'мусорных запроса', 'мусорных запросов')} на ${rubles(own.negativeCost)}`
    let note = 'Разложили по минус-фразам — проверьте группы и минусуйте.'
    if (weekly > 0) note += ` Экономия — около ${rubles(weekly)} в неделю.`
    if (c.new) note += ` Ещё ${num(c.new)} ${plural(c.new, 'новый запрос', 'новых запроса', 'новых запросов')} без разметки.`
    return {
      state: 'junk',
      // После ваших правок это уже не только находка ИИ.
      title: own.edited ? `К минусу ${what}` : `ИИ нашёл ${what}`,
      note,
      allLabel: n > 1 ? `Минусовать все ${n} ${plural(n, 'группу', 'группы', 'групп')}` : 'Минусовать всё',
    }
  }
  if (c.new)
    return {
      state: 'new',
      title: `${num(c.new)} ${plural(c.new, 'новый запрос', 'новых запроса', 'новых запросов')} на ${rubles(d.new_cost_rub)}`,
      note: 'ИИ разберёт их и предложит, что отминусовать. Разметка тратит лимит ИИ — перед большой покажем, сколько.',
    }
  return {
    state: 'clean',
    title: 'Мусора не найдено',
    note: d.synced_at
      ? `Все запросы за ${formatDate(d.date_from)} — ${formatDate(d.date_to)} разобраны. Загружены ${formatDateTime(d.synced_at)}.`
      : 'Все загруженные запросы разобраны.',
  }
})

function negativeItem(q) {
  const d = decisions[q.query_norm]
  return {
    query_norm: q.query_norm,
    verdict: 'negative',
    negative_phrase: (d?.negative_phrase || q.query).trim(),
    level: levelFor(q, d?.level || 'campaign'),
    add_keyword: false,
    adgroup_row_id: null,
  }
}

function targetItem(q) {
  return {
    query_norm: q.query_norm,
    verdict: 'target',
    negative_phrase: '',
    level: decisions[q.query_norm]?.level || 'campaign',
    add_keyword: false,
    adgroup_row_id: null,
  }
}

/** Вместе с минусом запоминаем и отмеченные «не мусор» — иначе после перезагрузки они вернутся в мусор. */
function withRescued(list) {
  const rescued = items.value.filter(
    (q) => decisions[q.query_norm]?.verdict === 'target' && q.label?.verdict === 'negative',
  )
  return [...list, ...rescued.map(targetItem)]
}

/**
 * Массовое «В минус»: если у нескольких запросов есть общее слово, предлагаем минусовать его одним словом.
 * Предлоги, слова ваших ключей и полезных запросов не предлагаем; слово проверяем на сервере — без конфликтов
 * с ключами и без запросов, которые вы отметили целевыми или по которым были заявки.
 * rows — запросы к минусу, avoid — запросы, которые резать нельзя.
 * → 'word' — фразы заменили словом, 'whole' — минусуем как было, 'cancel' — окно закрыли, ничего не делаем.
 */
async function offerCommonWord(rows, avoid = []) {
  if (rows.length < 2) return 'whole'
  const skip = rows.flatMap((q) => q.matched_keywords || [])
  const candidates = commonWords(
    rows.map((q) => q.query),
    { skip, avoid: avoid.map((q) => q.query) },
  ).slice(0, 3)
  for (const c of candidates) {
    const hit = c.indices.map((i) => rows[i])
    let res
    try {
      res = await wsApi.queriesCheck(
        ws.value.id,
        hit.map((q) => ({
          query_norm: q.query_norm,
          negative_phrase: c.word,
          level: decisions[q.query_norm]?.level || 'campaign',
        })),
      )
    } catch {
      return 'whole' // без проверки слово не предлагаем — минусуем как было
    }
    const bad = (it) => (it.problems || []).length || it.conflicts_total || it.targets_total
    if ((res.items || []).some(bad)) continue
    const n = hit.length
    // «Нет» — явный ответ: минусуем запросами целиком; крестик, Esc или клик мимо — передумали (null).
    const ok = await askConfirm(
      `У ${n} ${plural(n, 'запроса', 'запросов', 'запросов')} есть слово «${c.word}» — минусовать его? Одно минус-слово отсечёт эти запросы и похожие на них в будущем.`,
      { title: 'Общее слово', confirmText: 'Да, словом', cancelText: 'Нет, запросами целиком', dismissValue: null },
    )
    if (ok === null) return 'cancel'
    if (!ok) return 'whole'
    for (const q of hit) {
      decisions[q.query_norm].negative_phrase = c.word
      touch(q.query_norm)
    }
    scheduleCheck(hit.map((q) => q.query_norm))
    return 'word'
  }
  return 'whole'
}

async function minusGroup(g) {
  // Пока проверяем общее слово и ждём ответа, кнопки заняты: второй клик не запишет группу дважды.
  if (busy.value) return
  busy.value = 'check'
  let choice = 'whole'
  try {
    // У группы ИИ фраза уже общая; общее слово ищем в «Разных запросах» и «Отмечено вами».
    if ((g.misc || g.user) && g.items.length > 1) choice = await offerCommonWord(g.items, useful.value)
  } finally {
    busy.value = ''
  }
  if (choice === 'cancel') return
  return apply(withRescued(g.items.map(negativeItem)))
}

async function minusAll() {
  if (busy.value) return
  const list = junk.value
  const ok = await askConfirm(
    `Добавить в минус-фразы ${list.length} ${plural(list.length, 'запрос', 'запроса', 'запросов')}? Запросы, которые заденут ваши ключевые фразы, пропустим. Действие можно откатить.`,
    { title: 'Минусовать всё', confirmText: 'Минусовать' },
  )
  if (ok) await apply(withRescued(list.map(negativeItem)))
}

async function keepTargets() {
  if (busy.value) return
  const list = useful.value
  const ok = await askConfirm(
    `Запомнить ${list.length} ${plural(list.length, 'запрос', 'запроса', 'запросов')} как целевые? ИИ больше не будет их разбирать; в Директе ничего не изменится.`,
    { title: 'Полезные запросы', confirmText: 'Запомнить' },
  )
  if (ok) await apply(list.map(targetItem))
}

async function loadSimple() {
  const seq = ++loadSeq
  loading.value = true
  loadError.value = ''
  try {
    const params = { tab: 'negative', limit: SIMPLE_LIMIT, offset: 0 }
    const [neg, tgt, hl] = await Promise.all([
      wsApi.queries(ws.value.id, params),
      wsApi.queries(ws.value.id, { ...params, tab: 'target' }),
      // Дополнение к разбору: без него страница всё равно рабочая.
      wsApi.queriesHighlights(ws.value.id).catch(() => null),
    ])
    if (seq !== loadSeq) return
    highlights.value = hl
    for (const q of [...neg.items, ...tgt.items]) initDecision(q)
    data.value = neg
    items.value = neg.items
    targets.value = tgt.items
    if (!daysPicked.value) days.value = loadedDays(neg) || days.value
  } catch (err) {
    if (seq === loadSeq) loadError.value = errorText(err)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function placeText(q) {
  const camps = q.campaigns.map((c) => c.name || `кампания ${c.id}`)
  let text = camps.slice(0, 2).join(', ') + (camps.length > 2 ? ` и ещё ${camps.length - 2}` : '')
  const groups = q.adgroups.map((g) => g.name).filter(Boolean)
  if (groups.length === 1) text += ` → ${groups[0]}`
  else if (groups.length > 1) text += ` · ${groups.length} ${plural(groups.length, 'группа', 'группы', 'групп')}`
  return text
}

function initDecision(q) {
  if (decisions[q.query_norm]?.touched) return
  const l = q.label
  decisions[q.query_norm] = {
    verdict: l?.verdict || null,
    // Пусто — минусуем запрос целиком; слова-«таблетки» тогда ждут выбора.
    negative_phrase: l?.negative_phrase || '',
    level: levelFor(q, l?.level || 'adgroup'),
    add_keyword: false,
    adgroup_row_id: q.adgroups[0]?.id || null,
    touched: false,
  }
}

/** Запрос без группы (например, из автотаргетинга) можно минусовать только в кампании. */
function levelFor(q, level) {
  return q?.adgroups?.length ? level : 'campaign'
}

function touch(norm) {
  if (!decisions[norm]) return
  decisions[norm].touched = true
  delete rowResults[norm]
}

function conflictText(c) {
  const list = c.conflicts.map((k) => `«${k.keyword}»`).join(', ')
  return c.total > c.conflicts.length ? `${list} и ещё ${c.total - c.conflicts.length}` : list
}

/** Нужные запросы (целевые или с заявками), которые фраза тоже отсечёт: «a», «b» и ещё N. */
function keptText(c) {
  const list = c.targets.map((k) => `«${k.query}»`).join(', ')
  return c.targetsTotal > c.targets.length ? `${list} и ещё ${c.targetsTotal - c.targets.length}` : list
}

function checkKey(d) {
  return `${d.level}|${(d.negative_phrase || '').trim()}`
}

/** Конфликты и ошибки формата для текущей минус-фразы строки: свежая проверка или то, что пришло с меткой. */
function checkOf(q) {
  const d = decisions[q.query_norm]
  if (!d || d.verdict !== 'negative') return null
  const c = checks[q.query_norm]
  if (c && c.key === checkKey(d)) return c
  const l = q.label
  // Пустая фраза (весь запрос) проверяется заново: у метки конфликты посчитаны по её фразе.
  if (l?.verdict === 'negative' && l.negative_phrase && l.negative_phrase === d.negative_phrase.trim() && l.level === d.level)
    return { problems: [], conflicts: q.conflicts, total: q.conflicts_total }
  return null
}

const checkQueue = new Set()
let checkTimer = null

function scheduleCheck(norms) {
  for (const n of norms) checkQueue.add(n)
  clearTimeout(checkTimer)
  checkTimer = setTimeout(runChecks, 250)
}

async function runChecks() {
  const list = [...checkQueue].filter((n) => decisions[n]?.verdict === 'negative').slice(0, 500)
  checkQueue.clear()
  if (!list.length) return
  const keys = Object.fromEntries(list.map((n) => [n, checkKey(decisions[n])]))
  try {
    const res = await wsApi.queriesCheck(
      ws.value.id,
      list.map((n) => ({ query_norm: n, negative_phrase: decisions[n].negative_phrase, level: decisions[n].level })),
    )
    for (const it of res.items || []) {
      checks[it.query_norm] = {
        key: keys[it.query_norm],
        problems: it.problems || [],
        conflicts: it.conflicts || [],
        total: it.conflicts_total || 0,
        targets: it.targets_hit || [],
        targetsTotal: it.targets_total || 0,
      }
    }
  } catch {
    // Проверка — подсказка; при применении фразы всё равно проверяются на сервере.
  }
}

function setVerdict(q, verdict) {
  const d = decisions[q.query_norm]
  d.verdict = d.verdict === verdict ? null : verdict
  touch(q.query_norm)
  if (d.verdict && !selected.value.has(q.query_norm)) toggle(q.query_norm, true)
  if (d.verdict === 'negative') scheduleCheck([q.query_norm])
}

async function markSelected(verdict) {
  const norms = [...selected.value].filter((n) => decisions[n])
  for (const n of norms) {
    decisions[n].verdict = verdict
    touch(n)
  }
  if (verdict !== 'negative') return
  scheduleCheck(norms)
  // Общее слово у выбранных — одной минус-фразой; целевые среди остальных не задеваем.
  const chosen = new Set(norms)
  const rows = items.value.filter((q) => chosen.has(q.query_norm))
  const avoid = items.value.filter((q) => !chosen.has(q.query_norm) && decisions[q.query_norm]?.verdict === 'target')
  await offerCommonWord(rows, avoid)
}

/** Фраза из слов-«таблеток» в строке таблицы. */
function setPhrase(q, phrase) {
  const d = decisions[q.query_norm]
  if (!d) return
  d.negative_phrase = phrase
  touch(q.query_norm)
  scheduleCheck([q.query_norm])
}

function setLevelSelected(level) {
  if (!level) return
  const norms = [...selected.value].filter((n) => decisions[n]?.verdict === 'negative')
  for (const n of norms) {
    decisions[n].level = levelFor(byNorm.value[n], level)
    touch(n)
  }
  scheduleCheck(norms)
}

function toggle(norm, on) {
  const next = new Set(selected.value)
  if (on) next.add(norm)
  else next.delete(norm)
  selected.value = next
}

const allPicked = computed(() => items.value.length > 0 && items.value.every((q) => selected.value.has(q.query_norm)))
const memAllPicked = computed(() => mem.items.length > 0 && mem.items.every((m) => mem.selected.has(m.query_norm)))

function toggleAll(on) {
  selected.value = on ? new Set(items.value.map((q) => q.query_norm)) : new Set()
}

function queueParams(offset = 0) {
  return {
    tab: tab.value || 'negative',
    search: filters.search,
    campaign_row_id: filters.campaign,
    min_cost_rub: filters.minCost,
    no_conversions: filters.noConv,
    limit: PAGE,
    offset,
  }
}

let loadSeq = 0

async function load({ more = false } = {}) {
  if (!expert.value) return loadSimple()
  const seq = ++loadSeq
  loading.value = true
  loadError.value = ''
  try {
    const res = await wsApi.queries(ws.value.id, queueParams(more ? items.value.length : 0))
    if (seq !== loadSeq) return
    // Первый заход: если предложений в минус нет, а новые есть — открываем «Новые».
    if (!tab.value) {
      if (!res.counts.negative && res.counts.new) {
        tab.value = 'new'
        return load()
      }
      tab.value = 'negative'
    }
    for (const q of res.items) initDecision(q)
    data.value = res
    if (!daysPicked.value) days.value = loadedDays(res) || days.value
    items.value = more ? [...items.value, ...res.items] : res.items
    if (!more) {
      const present = new Set(res.items.map((q) => q.query_norm))
      selected.value = new Set([...selected.value].filter((n) => present.has(n)))
    }
  } catch (err) {
    if (seq === loadSeq) loadError.value = errorText(err)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function pickDays(d) {
  days.value = d
  daysPicked.value = true
}

// Длина загруженного периода → ближайший из чипов.
function loadedDays(res) {
  if (!res?.date_from || !res?.date_to) return 0
  const span = Math.round((Date.parse(res.date_to) - Date.parse(res.date_from)) / 86_400_000) + 1
  if (!(span > 0)) return 0
  return PERIODS.reduce((best, d) => (Math.abs(d - span) < Math.abs(best - span) ? d : best), PERIODS[0])
}

async function refreshCounts() {
  try {
    const res = await wsApi.queries(ws.value.id, { ...queueParams(), tab: 'all', limit: 1 })
    data.value = { ...(data.value || {}), ...res, items: data.value?.items || [], tab: data.value?.tab }
  } catch {
    // Счётчики обновятся при следующем заходе во вкладку.
  }
}

// Из памяти «Разбор запросов» возвращает в ту очередь, где были (с первого захода — «К минусу»).
const lastQueue = ref(tab.value && tab.value !== 'memory' ? tab.value : 'negative')
watch(tab, (key) => {
  if (key && key !== 'memory') lastQueue.value = key
})

function setTab(key) {
  if (tab.value === key) return
  tab.value = key
  selected.value = new Set()
  router.replace({ query: { ...route.query, tab: key } })
  if (key === 'memory') loadMemory()
  else load()
}

async function sync() {
  busy.value = 'sync'
  busyText.value = 'Запрашиваем отчёт у Директа…'
  try {
    const res = await wsApi.queriesSync(ws.value.id, days.value, (job) => {
      busyText.value = `Загрузка: ${jobProgressText(job)}`
    })
    const errors = res.errors || []
    // Бесплатные правила после загрузки: память решений, бриф и минус-фразы Директа — без ИИ и без лимита.
    const rules = Number(res.rules) || 0
    pushToast(
      `Загружено запросов: ${num(res.queries)} из ${res.campaigns} ${plural(res.campaigns, 'кампании', 'кампаний', 'кампаний')}` +
        (rules ? `. Сразу разобрали по памяти, брифу и Директу: ${num(rules)}` : '') +
        (errors.length ? `. Не вышло: ${errors.map((e) => e.name).join(', ')}` : ''),
      errors.length ? 'warn' : 'ok',
    )
    if (expert.value && tab.value === 'memory') await refreshCounts()
    else await load()
    // Предложений в минус ещё нет, а новые появились — показываем их, а не пустую вкладку.
    if (expert.value && tab.value === 'negative' && !counts.value.negative && counts.value.new) setTab('new')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = ''
    busyText.value = ''
  }
}

async function classify() {
  const relabel = !counts.value.new
  if (
    relabel &&
    !(await askConfirm(
      'Новых запросов нет. Разметить заново неподтверждённые предложения? Учтём свежий бриф и память решений.',
      { title: 'Переразметка', confirmText: 'Разметить заново' },
    ))
  )
    return
  const payload = { limit: 300, relabel, campaign_row_id: filters.campaign ? Number(filters.campaign) : null }
  busy.value = 'classify'
  busyText.value = 'Считаем объём и расход…'
  let est
  try {
    est = await wsApi.queriesClassifyEstimate(ws.value.id, payload)
  } catch (err) {
    pushToast(errorText(err), 'error')
    busy.value = ''
    busyText.value = ''
    return
  }
  // Один вызов ИИ, который влезает в лимит, — без лишнего вопроса; массовая разметка — после оценки.
  if (!est.ai || est.ai_error || (est.batches <= 1 && !(est.fits !== null && est.fits < est.ai))) {
    return runClassify(payload)
  }
  busy.value = ''
  busyText.value = ''
  Object.assign(estimate, { open: true, est, payload })
}

function confirmClassify() {
  estimate.open = false
  return runClassify(estimate.payload)
}

async function runClassify(payload) {
  busy.value = 'classify'
  busyText.value = 'Размечаем запросы…'
  try {
    const res = await wsApi.queriesClassify(ws.value.id, payload, (job) => {
      busyText.value = `Разметка: ${jobProgressText(job)}`
    })
    if (!res.labeled) {
      pushToast(res.ai_error ? `ИИ не разметил запросы: ${res.ai_error}` : 'Размечать нечего', res.ai_error ? 'error' : 'info')
    } else {
      const parts = [`Размечено ${res.labeled}: в минус ${res.negative}, целевых ${res.target}`]
      if (res.rules) parts.push(`по памяти, брифу и Директу — ${res.rules}`)
      if (res.left) parts.push(`ещё без разметки ${res.left}`)
      if (res.usage) parts.push(aiUsageText(res.usage))
      pushToast(parts.join('; '), 'ok')
      if (res.ai_error) pushToast(`ИИ разобрал не всё: ${res.ai_error}`, 'warn')
    }
    if (!expert.value) await load()
    else if (res.negative && tab.value !== 'negative') setTab('negative')
    else if (tab.value === 'memory') await refreshCounts()
    else await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = ''
    busyText.value = ''
  }
}

function toItem(q) {
  const d = decisions[q.query_norm]
  const keyword = d.verdict === 'target' && d.add_keyword && q.adgroups.length > 0
  return {
    query_norm: q.query_norm,
    verdict: d.verdict,
    negative_phrase: d.verdict === 'negative' ? d.negative_phrase.trim() : '',
    level: d.level,
    add_keyword: keyword,
    adgroup_row_id: keyword ? d.adgroup_row_id : null,
  }
}

function openApply() {
  const chosen = items.value.filter((q) => selected.value.has(q.query_norm))
  const ready = chosen.filter((q) => decisions[q.query_norm]?.verdict)
  if (!ready.length) {
    pushToast('Отметьте выбранные запросы: «Минус» или «Целевой»', 'error')
    return
  }
  applyModal.items = ready.map(toItem)
  applyModal.skipped = chosen.length - ready.length
  applyModal.open = true
}

function confirmApply() {
  const bid = applyModal.bid === '' ? null : Number(applyModal.bid)
  return apply(applyModal.items, { bid: Number.isFinite(bid) && bid > 0 ? bid : null })
}

async function apply(list, { force = false, bid = null } = {}) {
  busy.value = 'apply'
  busyText.value = 'Применяем решения…'
  // Тексты запросов запоминаем до перезагрузки: применённые уйдут из вкладки.
  const names = Object.fromEntries([...items.value, ...targets.value].map((q) => [q.query_norm, q.query]))
  try {
    const res = await wsApi.queriesApply(ws.value.id, { items: list, force, bid_rub: bid }, (job) => {
      busyText.value = `Применяем: ${jobProgressText(job)}`
    })
    applyModal.open = false
    const done = new Set()
    for (const it of res.items || []) {
      rowResults[it.query_norm] = it
      if (it.status === 'ok' || it.status === 'partial') {
        done.add(it.query_norm)
        if (decisions[it.query_norm]) decisions[it.query_norm].touched = false
      }
    }
    selected.value = new Set([...selected.value].filter((n) => !done.has(n)))
    resolvedProblems.clear()
    result.value = {
      ...res,
      sent: list,
      problems: (res.items || [])
        .filter((i) => i.status !== 'ok' && (i.status !== 'partial' || i.message))
        .map((i) => ({ ...i, query: names[i.query_norm] || i.query_norm })),
    }
    pushToast(
      resultTitle.value,
      res.status === 'ok' ? 'ok' : res.status === 'partial' ? 'warn' : 'error',
      undoAction(res, onUndone),
    )
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = ''
    busyText.value = ''
  }
}

async function applyForce() {
  const norms = new Set(forceable.value.map((p) => p.query_norm))
  const list = (result.value?.sent || []).filter((i) => norms.has(i.query_norm))
  if (!list.length) return
  const ok = await askConfirm(
    'Эти минус-фразы пересекаются с вашими ключевыми фразами и могут отрезать целевой трафик. Добавить всё равно?',
    { title: 'Добавить с конфликтами', confirmText: 'Добавить', danger: true },
  )
  if (ok) await apply(list, { force: true })
}

// Минус-фраза задела ключи — готовые выходы (группы без ключа, уточнённая фраза, ключ в кавычки).
const conflict = reactive({ row: null, phrase: '', level: '' })

function openResolve(p) {
  Object.assign(conflict, { row: { query_norm: p.query_norm, query: p.query }, phrase: p.phrase || '', level: p.level || '' })
}

function openResolveRow(q) {
  const d = decisions[q.query_norm]
  Object.assign(conflict, { row: q, phrase: d?.negative_phrase.trim() || q.query, level: d?.level || '' })
}

// Простой режим: пересечения по очереди — после одного сразу следующее.
function resolveNext() {
  if (forceable.value.length) openResolve(forceable.value[0])
}

// Решённые пересечения из итога пакета: если одно решение вернут, строка вернётся в итог.
const resolvedProblems = new Map()

function onResolved({ row, undone }) {
  if (undone) {
    // Вернули одно решение — итог пакета (остальные пересечения, его «Вернуть») не трогаем.
    if (conflict.row?.query_norm === row.query_norm) conflict.row = null
    const p = resolvedProblems.get(row.query_norm)
    resolvedProblems.delete(row.query_norm)
    if (p && result.value && !result.value.problems.some((x) => x.query_norm === p.query_norm)) {
      result.value = { ...result.value, problems: [...result.value.problems, p] }
    }
    if (expert.value && tab.value === 'memory') loadMemory()
    else load()
    return
  }
  conflict.row = null
  if (result.value) {
    const p = result.value.problems.find((x) => x.query_norm === row.query_norm)
    if (p) resolvedProblems.set(row.query_norm, p)
    result.value = { ...result.value, problems: result.value.problems.filter((p) => p.query_norm !== row.query_norm) }
    if (!expert.value) resolveNext()
  }
  load()
}

function onUndone() {
  result.value = null
  for (const key of Object.keys(rowResults)) delete rowResults[key]
  if (expert.value && tab.value === 'memory') loadMemory()
  else load()
}

// --- Память решений ---

let memSeq = 0

async function loadMemory(more = false) {
  const seq = ++memSeq
  mem.loading = true
  mem.error = ''
  try {
    const res = await wsApi.queriesMemory(ws.value.id, {
      verdict: mem.verdict,
      search: mem.search,
      limit: PAGE,
      offset: more ? mem.items.length : 0,
    })
    if (seq !== memSeq) return
    mem.items = more ? [...mem.items, ...res.items] : res.items
    mem.total = res.total
    if (!more) {
      const present = new Set(res.items.map((m) => m.query_norm))
      mem.selected = new Set([...mem.selected].filter((n) => present.has(n)))
    }
  } catch (err) {
    if (seq === memSeq) mem.error = errorText(err)
  } finally {
    if (seq === memSeq) mem.loading = false
  }
}

function memToggle(norm, on) {
  const next = new Set(mem.selected)
  if (on) next.add(norm)
  else next.delete(norm)
  mem.selected = next
}

function memToggleAll(on) {
  mem.selected = on ? new Set(mem.items.map((m) => m.query_norm)) : new Set()
}

async function forget() {
  const norms = [...mem.selected]
  if (!norms.length) return
  const ok = await askConfirm(
    `Забыть ${norms.length} ${plural(norms.length, 'решение', 'решения', 'решений')}? Запросы вернутся в разбор, ИИ перестанет брать их как примеры. Минус-фразы в Директе останутся.`,
    { title: 'Забыть решения', confirmText: 'Забыть' },
  )
  if (!ok) return
  mem.busy = true
  try {
    const res = await wsApi.queriesForget(ws.value.id, norms)
    pushToast(`Забыто: ${res.forgotten}`, 'ok')
    mem.selected = new Set()
    await Promise.all([loadMemory(), refreshCounts()])
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    mem.busy = false
  }
}

let filterTimer = null
watch(
  () => [filters.search, filters.campaign, filters.minCost, filters.noConv],
  () => {
    clearTimeout(filterTimer)
    filterTimer = setTimeout(() => {
      if (tab.value !== 'memory') load()
    }, 300)
  },
)

let memTimer = null
watch(
  () => [mem.search, mem.verdict],
  () => {
    clearTimeout(memTimer)
    memTimer = setTimeout(() => loadMemory(), 300)
  },
)

// Смена режима — другой набор данных: простой грузит мусор и полезные целиком, экспертный — вкладку.
watch(expert, (on) => {
  if (on && tab.value === 'memory') {
    loadMemory()
    refreshCounts()
  } else load()
})

onMounted(() => {
  if (expert.value && tab.value === 'memory') {
    loadMemory()
    refreshCounts()
  } else {
    load()
  }
})

onBeforeUnmount(() => {
  clearTimeout(filterTimer)
  clearTimeout(memTimer)
  clearTimeout(checkTimer)
})

/** Фильтр запросов строкой для чипа «Про: …» и для ИИ. */
function queriesFilterText(sel, where) {
  return [
    filters.search && `поиск «${filters.search}»`,
    where,
    filters.minCost && `расход от ${filters.minCost} ₽`,
    filters.noConv && 'без конверсий',
    sel && `выбрано запросов: ${sel}`,
  ]
    .filter(Boolean)
    .join(', ')
}

// Панель ИИ знает, что открыто: запросы проекта, период и фильтр.
usePageContext(() => {
  const range = ctxDays(days.value)
  const campaign = filters.campaign ? campaignOptions.value.find((o) => o.value === filters.campaign)?.label : ''
  return {
    view: 'queries',
    period: { from: range.date_from, to: range.date_to },
    filter: queriesFilterText(selected.value.size, campaign && `кампания «${campaign}»`),
  }
})
</script>

<style scoped>
.tri-sum {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 20px 22px;
}
.tri-sum__text {
  flex: 1 1 320px;
  min-width: 0;
}
.tri-sum h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 800;
  line-height: 1.3;
}
.tri-sum .meta-note {
  display: block;
  margin: 6px 0 0;
}
.tri-sum__acts,
.rcard__acts {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.tri-note {
  margin-left: 6px;
  color: var(--faint);
  font-size: 12px;
  font-weight: 600;
}
.tri-done {
  border-color: var(--ok);
}
.tri-ok .prow__main,
.tri-done .prow__main {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.rgrid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}
.rcard {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px 18px;
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
}
.rcard__h {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.rcard__h h3 {
  margin: 0;
  overflow-wrap: anywhere;
  font-size: 15px;
  font-weight: 700;
}
.rcard__list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 13px;
}
.rcard__list li {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}
.rcard__q {
  min-width: 0;
  overflow-wrap: anywhere;
}
.rcard .meta-note,
.tri-ok .meta-note {
  margin: 0;
}
.rcard__q small {
  display: block;
  margin-top: 1px;
  font-size: 12px;
}
.rcard__cost {
  flex-shrink: 0;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.rcard__list li > .rcard__cost {
  margin-left: auto;
}
.rcard__reason {
  color: var(--muted);
}
/* «минус — весь запрос» / «минус «…»» под запросом: открывает выбор слов. */
.rcard__phrase {
  display: block;
  margin-top: 1px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 500;
  text-align: left;
  overflow-wrap: anywhere;
}
.rcard__phrase:hover:not(:disabled) {
  color: var(--ink);
}
/* × «Это не мусор» у строки мусора. */
.rcard__x {
  display: inline-grid;
  flex-shrink: 0;
  place-items: center;
  width: 24px;
  height: 24px;
  margin: -4px -6px;
  padding: 0;
  border: 0;
  border-radius: 999px;
  background: none;
  color: var(--faint);
  cursor: pointer;
  align-self: center;
}
.rcard__x:hover:not(:disabled),
.rcard__x:focus-visible {
  background: var(--line);
  color: var(--ink);
}
.rcard__x:disabled {
  cursor: default;
  opacity: 0.5;
}
.rcard__junk {
  flex-shrink: 0;
  color: var(--muted);
  font-size: 12px;
  white-space: nowrap;
}
.rcard__junk:hover:not(:disabled) {
  color: var(--danger);
}
.rcard__more {
  align-self: flex-start;
  font-size: 13px;
}
.rcard__warn {
  margin: 0;
  color: var(--warn);
  font-size: 13px;
}
.rcard__acts {
  margin-top: auto;
  padding-top: 4px;
}
.decision__words {
  margin-top: 6px;
}
.tri-ok__list {
  padding: 14px 18px 16px;
  border-top: 1px solid var(--line);
}
.tri-ok > .meta-note {
  padding: 0 18px 14px;
}
</style>
