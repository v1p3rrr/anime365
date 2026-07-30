/**
 * Форматирование, зависящее от языка.
 *
 * Единицы размера пишутся по-разному (Б/KB/…), поэтому берутся из словаря,
 * а не зашиваются в утилиту.
 */

import { formatBytes as rawFormatBytes } from '../api/library'
import { useT } from './index'

export function useFormat() {
  const { t, locale } = useT()

  const units = [
    t('units.bytes'),
    t('units.kb'),
    t('units.mb'),
    t('units.gb'),
    t('units.tb'),
  ]

  return {
    /** Размер файла или папки с локализованными единицами. */
    size: (bytes: number) => rawFormatBytes(bytes, units),
    /** Число с разделителями разрядов по правилам языка. */
    number: (value: number) => value.toLocaleString(locale),
  }
}
