import { Alert, Button, Group, Tabs } from '@mantine/core'
import { useState } from 'react'

import type { AutoLang } from '../api/autodownload'
import { useT } from '../i18n'
import { Flag } from './Flag'
import { AutoDownloadLangPanel, type LangDraft } from './AutoDownloadLangPanel'

interface Props {
  /** Языки, включённые у человека в настройках субтитров. */
  langs: AutoLang[]
  /** Сохранённое состояние языка. Пусто — язык ещё не настраивали. */
  saved: (lang: AutoLang) => LangDraft
  onSave: (lang: AutoLang, value: LangDraft) => void
  saving?: boolean
  modes: string[]
  modePrefix: 'mode' | 'titleMode'
  /** У тайтла переключатели трёхзначные: есть «как в общих настройках». */
  inheritable?: boolean
  /** Подсказка под выбором режима — своя на каждый язык. */
  hint?: (lang: AutoLang) => string | undefined
  /** Что показать справа от кнопки сохранения (например, «убрать своё»). */
  extraActions?: (lang: AutoLang) => React.ReactNode
}

/**
 * Вкладки языков с настройкой автоскачивания внутри.
 *
 * Общий компонент для двух мест — вкладки в библиотеке и окна на странице
 * тайтла. Разница между ними целиком укладывается в параметры: набор режимов,
 * трёхзначность переключателей и дополнительные кнопки. Держать две почти
 * одинаковые копии значило бы чинить каждую правку дважды и однажды забыть.
 *
 * Черновик живёт здесь же: кнопка сохранения появляется только когда есть что
 * сохранять, и правки одного языка не теряются при переходе на соседний.
 */
export function AutoDownloadLangTabs({
  langs,
  saved,
  onSave,
  saving,
  modes,
  modePrefix,
  inheritable,
  hint,
  extraActions,
}: Props) {
  const { t } = useT()
  const [active, setActive] = useState<AutoLang>(langs[0] ?? 'ru')
  const [draft, setDraft] = useState<Partial<Record<AutoLang, LangDraft>>>({})

  if (langs.length === 0) {
    return <Alert color="blue">{t('autodownload.noLanguages')}</Alert>
  }

  // Вкладка могла исчезнуть, пока человек правил настройки субтитров.
  const current = langs.includes(active) ? active : langs[0]
  const value = draft[current] ?? saved(current)

  return (
    <Tabs value={current} onChange={(next) => next && setActive(next as AutoLang)}>
      <Tabs.List>
        {langs.map((lang) => (
          <Tabs.Tab key={lang} value={lang} leftSection={<Flag lang={lang} />}>
            {lang.toUpperCase()}
          </Tabs.Tab>
        ))}
      </Tabs.List>

      <Tabs.Panel value={current} pt="sm">
        <AutoDownloadLangPanel
          lang={current}
          value={value}
          modes={modes}
          modePrefix={modePrefix}
          inheritable={inheritable}
          hint={hint?.(current)}
          onChange={(next) => setDraft({ ...draft, [current]: next })}
        />

        <Group justify="space-between" mt="sm" wrap="wrap" gap="xs">
          <Group gap="xs">{extraActions?.(current)}</Group>
          <Button
            loading={saving}
            disabled={!draft[current]}
            onClick={() => {
              onSave(current, value)
              // Черновик снимаем сразу: иначе после сохранения кнопка
              // остаётся активной и предлагает сохранить то же самое.
              setDraft((rest) => {
                const next = { ...rest }
                delete next[current]
                return next
              })
            }}
          >
            {t('common.save')}
          </Button>
        </Group>
      </Tabs.Panel>
    </Tabs>
  )
}
