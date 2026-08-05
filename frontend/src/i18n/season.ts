import { useT } from './index'
import type { TranslationKey } from './index'

/**
 * Сезон выхода приходит с Anime365 **готовой русской строкой** — «Весна 2020»,
 * «Осень 2015». Отдельных полей «сезон» и «год» в API нет, поэтому строку
 * приходится разбирать.
 *
 * Разбор, а не словарь целиком: год в строке произвольный, и перечислить все
 * сочетания нельзя. Ищем известное название сезона, остальное считаем годом.
 *
 * Не разобралось — отдаём как пришло. Показать исходную строку лучше, чем
 * пустоту: сайт может однажды написать «Весна 2020 (2 кур)», и терять такое
 * молча не стоит.
 */
const SEASONS: Array<[RegExp, string]> = [
  [/зим/i, 'winter'],
  [/весн/i, 'spring'],
  [/лет/i, 'summer'],
  [/осен/i, 'autumn'],
]

export function useSeasonLabel() {
  const { t } = useT()

  return (season: string | null | undefined): string => {
    if (!season) return ''
    const found = SEASONS.find(([pattern]) => pattern.test(season))
    if (!found) return season

    const year = season.match(/\d{4}/)?.[0]
    const name = t(`season.${found[1]}` as TranslationKey)
    return year ? `${name} ${year}` : name
  }
}
