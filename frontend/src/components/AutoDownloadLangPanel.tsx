import { Alert, Divider, Select, Stack, Switch, Text } from '@mantine/core'
import { IconInfoCircle } from '@tabler/icons-react'

import type { AutoLang } from '../api/autodownload'
import { useT } from '../i18n'
import type { TranslationKey } from '../i18n'
import { AuthorPriorityList } from './AuthorPriorityList'

/** Состояние одного языка. Совпадает и для глобальной настройки, и для тайтла. */
export interface LangDraft {
  mode: string
  priorities: string[]
  /** `null` — наследовать общее. У глобальной настройки `null` не бывает. */
  allow_any_fallback: boolean | null
  allow_any: boolean | null
  ignore_embedded: boolean | null
}

interface Props {
  lang: AutoLang
  value: LangDraft
  onChange: (next: LangDraft) => void
  /** Какие значения показывать в выпадающем списке режимов. */
  modes: string[]
  /**
   * Префикс ключей перевода: `mode` или `titleMode`. Ключ собирается как
   * `autodownload.{префикс}{Режим}` — плоско, потому что так устроены все
   * остальные подписи в файлах локалей.
   */
  modePrefix: 'mode' | 'titleMode' 
  /**
   * Настройка тайтла: переключатели трёхзначные и умеют «как общее».
   * У глобальной настройки третьего состояния нет.
   */
  inheritable?: boolean
  /** Что получится в итоге — показываем, когда своё расходится с общим. */
  hint?: string
}

/**
 * Настройка автоскачивания одного языка.
 *
 * Два переключателя «любая группа» рядом — это не дублирование, они отвечают
 * на разные вопросы. Верхний гасит список целиком: годится кто угодно.
 * Нижний оставляет список главным и подключает остальных, только если из
 * списка не нашлось никого.
 *
 * Список при этом **не стирается**: погашенный переключателем, он ждёт своего
 * часа. Собирать его заново после каждого эксперимента было бы обидно.
 */
export function AutoDownloadLangPanel({
  lang,
  value,
  onChange,
  modes,
  modePrefix,
  inheritable,
  hint,
}: Props) {
  const { t } = useT()
  const patch = (next: Partial<LangDraft>) => onChange({ ...value, ...next })

  // Пока язык выключен, остальные поля ни на что не влияют. Прятать их не
  // стоит — иначе непонятно, что там вообще настраивается, — но погасить надо.
  const off = value.mode === 'off'
  const anyGroup = Boolean(value.allow_any)

  const tri = (
    field: 'allow_any_fallback' | 'allow_any' | 'ignore_embedded',
    labelKey: string,
    hintKey: string,
    disabled?: boolean,
  ) => (
    <Stack gap={2}>
      <Switch
        label={t(labelKey as TranslationKey)}
        description={t(hintKey as TranslationKey)}
        disabled={off || disabled}
        checked={Boolean(value[field])}
        onChange={(event) => patch({ [field]: event.currentTarget.checked } as Partial<LangDraft>)}
      />
      {inheritable && value[field] !== null && (
        <Text
          size="xs"
          c="dimmed"
          style={{ cursor: 'pointer', textDecoration: 'underline dotted' }}
          onClick={() => patch({ [field]: null } as Partial<LangDraft>)}
        >
          {t('autodownload.backToGlobal')}
        </Text>
      )}
      {inheritable && value[field] === null && (
        <Text size="xs" c="dimmed">
          {t('autodownload.inherited')}
        </Text>
      )}
    </Stack>
  )

  return (
    <Stack gap="sm">
      <Select
        label={t('autodownload.mode')}
        data={modes.map((mode) => ({
          value: mode,
          label: t(
            `autodownload.${modePrefix}${mode[0].toUpperCase()}${mode.slice(1)}` as TranslationKey,
          ),
        }))}
        value={value.mode}
        allowDeselect={false}
        onChange={(next) => next && patch({ mode: next })}
      />

      {hint && (
        <Alert color="blue" icon={<IconInfoCircle size={16} />} p="xs">
          <Text size="xs">{hint}</Text>
        </Alert>
      )}

      <Divider />

      {tri(
        'allow_any',
        'autodownload.allowAny',
        'autodownload.allowAnyHint',
      )}

      <AuthorPriorityList
        lang={lang}
        value={value.priorities}
        onChange={(priorities) => patch({ priorities })}
        disabled={off || anyGroup}
      />

      {tri(
        'allow_any_fallback',
        'autodownload.allowAnyFallback',
        'autodownload.allowAnyFallbackHint',
        anyGroup,
      )}

      <Divider />

      {tri(
        'ignore_embedded',
        'autodownload.ignoreEmbedded',
        'autodownload.ignoreEmbeddedHint',
      )}
    </Stack>
  )
}
