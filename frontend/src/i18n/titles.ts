/**
 * Выбор названия тайтла под язык интерфейса.
 *
 * Покрытие названий в каталоге неравномерное (замерено на 29 193 тайтлах):
 *   romaji 99.9%, ja 92.5%, ru 84.9%, en 45.9%.
 *
 * Отсюда два следствия. Первое: фолбек — romaji, он есть практически везде.
 * Второе: для английского интерфейса фолбек не редкость, а норма — своё
 * название есть меньше чем у половины тайтлов, и подставлять вместо него
 * русское было бы хуже, чем romaji, который читается на латинице.
 */

import { useRomajiPreference } from '../stores/preferences'
import { useT } from './index'

type Variant = 'ru' | 'romaji' | 'en' | 'ja'

/**
 * Названия приходят в двух формах: страница тайтла отдаёт словарь `{ru, romaji…}`,
 * карточка каталога — плоские поля `title_ru`, `title_romaji`… Принимаем обе,
 * чтобы на местах вызова не заводить переходники.
 */
export type TitleVariants = Partial<Record<Variant, string | null>> &
  Partial<Record<`title_${Variant}`, string | null>>

/** Порядок предпочтения для каждого языка интерфейса. */
const ORDER: Record<string, Variant[]> = {
  ru: ['ru', 'romaji', 'en', 'ja'],
  en: ['en', 'romaji', 'ru', 'ja'],
  // Японцу romaji ближе английского перевода: это то же японское название,
  // только записанное латиницей.
  ja: ['ja', 'romaji', 'en', 'ru'],
}

function variant(titles: TitleVariants, key: Variant): string | null {
  const value = titles[key] ?? titles[`title_${key}`]
  const trimmed = value?.trim()
  return trimmed ? trimmed : null
}

export function pickTitle(titles: TitleVariants, locale: string, fallback = ''): string {
  for (const key of ORDER[locale] ?? ORDER.ru) {
    const value = variant(titles, key)
    if (value) return value
  }
  return fallback
}

/**
 * Название под текущий язык и второе название под ним.
 *
 * Второе показывается только если отличается от первого — иначе под заголовком
 * повторялась бы та же строка.
 */
export function useTitleName() {
  const { locale } = useT()
  const [preferRomaji] = useRomajiPreference()

  return {
    primary: (titles: TitleVariants, fallback = '') => {
      // Ромадзи главным — по личной настройке. Она отдельно от языка
      // интерфейса намеренно: язык интерфейса про подписи и кнопки, а это про
      // то, под каким названием человек привык узнавать тайтл. Многие читают
      // интерфейс по-русски, но ищут аниме по ромадзи.
      if (preferRomaji) {
        const romaji = variant(titles, 'romaji')
        if (romaji) return romaji
      }
      return pickTitle(titles, locale, fallback)
    },
    secondary: (titles: TitleVariants) => {
      const translated = pickTitle(titles, locale)
      if (preferRomaji) {
        // Местами меняется только пара «ромадзи ↔ перевод». Если ромадзи нет,
        // главным осталось название на языке интерфейса, и повторять его
        // вторым не надо.
        const romaji = variant(titles, 'romaji')
        return romaji && translated !== romaji ? translated : null
      }
      // Подзаголовком всегда romaji: он есть почти всегда и помогает опознать
      // тайтл по названию, под которым его знают в сообществе.
      const romaji = variant(titles, 'romaji')
      return romaji && romaji !== translated ? romaji : null
    },
  }
}
