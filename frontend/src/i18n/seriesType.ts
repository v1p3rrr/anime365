import { useT, type TranslationKey } from './index'

/**
 * Подпись типа тайтла на языке интерфейса.
 *
 * `type_title` приходит из API Anime365 **по-русски** («ТВ сериал», «Фильм»),
 * и в английском интерфейсе торчал как есть. Переводим по коду типа (`tv`,
 * `movie`, …), а исходную строку держим запасным вариантом: типов в каталоге
 * девять, но API может однажды прислать десятый — показать русское название
 * лучше, чем пустоту.
 */
export function useSeriesTypeLabel() {
  const { t } = useT()
  return (type: string | null | undefined, fallback = '') => {
    if (!type) return fallback
    const key = `seriesType.${type}` as TranslationKey
    const label = t(key)
    // t() возвращает сам ключ, когда перевода нет — значит тип нам незнаком.
    return label === key ? fallback || t('seriesType.unknown') : label
  }
}
