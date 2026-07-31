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
    /**
     * Скорость загрузки. Единицы тоже локализуемы: раньше «МБ/с» было зашито
     * прямо в `api/downloads.ts` и в английском интерфейсе торчало по-русски.
     */
    speed: (bps: number) => {
      if (!bps) return '—'
      const mb = bps / (1024 * 1024)
      return mb >= 1
        ? `${mb.toFixed(1)} ${t('units.mbPerSec')}`
        : `${(bps / 1024).toFixed(0)} ${t('units.kbPerSec')}`
    },
    /** Сколько осталось качать. Секунды/минуты/часы — с локализованными единицами. */
    eta: (seconds: number) => {
      if (seconds <= 0) return '—'
      if (seconds < 60) return `${seconds} ${t('units.sec')}`
      if (seconds < 3600) return `${Math.round(seconds / 60)} ${t('units.min')}`
      return `${(seconds / 3600).toFixed(1)} ${t('units.hour')}`
    },
    /**
     * Длительность до минуты. Секунды не показываем: значение и так медиана
     * по десяткам переводов, и точность до секунды была бы выдумкой.
     */
    duration: (seconds: number | null | undefined) => {
      if (!seconds || seconds <= 0) return null
      const minutes = Math.round(seconds / 60)
      if (minutes < 60) return t('units.minutes', { count: minutes })
      return t('units.hoursMinutes', { hours: Math.floor(minutes / 60), minutes: minutes % 60 })
    },
    /** Число с разделителями разрядов по правилам языка. */
    number: (value: number) => value.toLocaleString(locale),
  }
}
