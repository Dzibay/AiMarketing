// Тексты сайта в Markdown: словарь, справка и блог (docs/landing-plan.md, 7.5).
// Статьи блога — ИИ-черновики: draft: true не попадает в продакшен-сборку и sitemap, пока владелец не отредактирует.
import { defineCollection } from 'astro:content'
import { glob } from 'astro/loaders'
import { z } from 'astro/zod'

const slovar = defineCollection({
  loader: glob({ pattern: '*.md', base: './src/content/slovar' }),
  schema: z.object({
    term: z.string(),
    title: z.string(),
    description: z.string(),
    short: z.string(),
    related: z.array(z.string()).default([]),
    feature: z.string().optional(),
  }),
})

const spravka = defineCollection({
  loader: glob({ pattern: '*.md', base: './src/content/spravka' }),
  schema: z.object({
    title: z.string(),
    description: z.string(),
    order: z.number(),
    updated: z.coerce.date(),
  }),
})

const blog = defineCollection({
  loader: glob({ pattern: '*.md', base: './src/content/blog' }),
  schema: z.object({
    title: z.string(),
    description: z.string(),
    published: z.coerce.date(),
    updated: z.coerce.date().optional(),
    tema: z.string(),
    draft: z.boolean().default(true),
    features: z.array(z.string()).default([]),
  }),
})

export const collections = { slovar, spravka, blog }
