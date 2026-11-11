# Один nginx отдаёт и сайт «Толк» (site/, Astro, статика), и кабинет (frontend/, Vue SPA).
# Контекст сборки — корень репозитория (docker-compose: context: .).

FROM node:22-alpine AS spa

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install

COPY frontend/ ./
# On small VPSes the build can exceed RAM; cap the V8 heap so it spills into swap instead of being OOM-killed.
ENV NODE_OPTIONS=--max-old-space-size=1536
# index.html кабинета → spa.html: корень отдаёт сайт, а nginx отправляет в spa.html только адреса кабинета.
RUN npm run build && mv dist/index.html dist/spa.html


FROM node:22-alpine AS site

WORKDIR /app/site

COPY site/package.json site/package-lock.json* ./
RUN npm install

# Сайт берёт дизайн-токены у кабинета (../frontend/src/styles/tokens.css).
COPY frontend/src/styles/tokens.css /app/frontend/src/styles/tokens.css
COPY site/ ./
ENV NODE_OPTIONS=--max-old-space-size=1536
# Счётчик Метрики и ключ IndexNow вшиваются при сборке. Тарифы и контакты (админка, вкладка «Сайт») берутся
# из API, если задан SITE_API_URL; без него сайт подгружает их в браузере из /api/site/plans и /api/site/public.
ARG PUBLIC_METRIKA_ID=""
ARG SITE_API_URL=""
ARG INDEXNOW_KEY=""
ENV PUBLIC_METRIKA_ID=$PUBLIC_METRIKA_ID \
    SITE_API_URL=$SITE_API_URL \
    INDEXNOW_KEY=$INDEXNOW_KEY
RUN npm run build


FROM nginx:1.27-alpine

# Сайт и кабинет в одном корне. Файлы не пересекаются: ассеты сайта — в /_astro/, кабинета — в /assets/,
# у кабинета spa.html, у сайта index.html, 404.html, robots.txt, sitemap. favicon.svg у обоих один и тот же.
COPY --from=site /app/site/dist/ /usr/share/nginx/html/
COPY --from=spa /app/frontend/dist/ /usr/share/nginx/html/
COPY nginx/default.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
