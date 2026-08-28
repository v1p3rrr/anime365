import { Alert, Button, Loader, Modal, Stack, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconSearch } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { autodownloadApi, type AutoLang } from '../api/autodownload'
import { useT } from '../i18n'
import { ALL_AUTO_LANGS } from './AutoDownloadTab'
import { AutoDownloadLangTabs } from './AutoDownloadLangTabs'
import type { LangDraft } from './AutoDownloadLangPanel'

interface Props {
  seriesId: number
  title: string
  opened: boolean
  onClose: () => void
  /** Языки субтитров, включённые в настройках человека. */
  langs: AutoLang[]
}

const BLANK: LangDraft = {
  mode: 'default',
  priorities: [],
  blacklist: [],
  allow_any_fallback: null,
  allow_any: null,
  ignore_embedded: null,
}

/**
 * Настройка автоскачивания для одного тайтла.
 *
 * Те же вкладки языков, что и в общих настройках, — буквально тот же компонент.
 * Разница целиком укладывается в параметры: набор режимов и трёхзначность
 * переключателей, потому что у тайтла есть состояние «как в общих настройках»,
 * и без него его было бы не отличить от «выключено».
 *
 * Пустой список означает «взять общий», а не «никого»: убрал свой список —
 * вернулась общая логика.
 */
export function TitleAutoDownloadModal({ seriesId, title, opened, onClose, langs }: Props) {
  const { t } = useT()
  const queryClient = useQueryClient()

  const settings = useQuery({
    queryKey: ['autodownload-series', seriesId],
    queryFn: () => autodownloadApi.series(seriesId),
    enabled: opened,
  })

  // Та же оговорка, что и во вкладке библиотеки: личный фильтр показа не должен
  // прятать язык, у которого У ЭТОГО ТАЙТЛА уже что-то настроено. Иначе
  // настройку не увидеть и не снять.
  const withOwn = ALL_AUTO_LANGS.filter(
    (lang) => langs.includes(lang) || settings.data?.own?.[lang] !== undefined,
  )

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['autodownload-series', seriesId] })
    queryClient.invalidateQueries({ queryKey: ['autodownload-configured'] })
  }

  const save = useMutation({
    mutationFn: ({ lang, value }: { lang: AutoLang; value: LangDraft }) =>
      autodownloadApi.setSeries(seriesId, lang, {
        mode: value.mode as 'default' | 'on' | 'off',
        priorities: value.priorities,
        blacklist: value.blacklist,
        allow_any_fallback: value.allow_any_fallback,
        allow_any: value.allow_any,
        ignore_embedded: value.ignore_embedded,
      }),
    onSuccess: () => {
      notifications.show({ color: 'teal', message: t('autodownload.saved') })
      invalidate()
    },
  })

  const clear = useMutation({
    mutationFn: (lang: AutoLang) => autodownloadApi.clearSeries(seriesId, lang),
    onSuccess: invalidate,
  })

  const run = useMutation({
    mutationFn: () => autodownloadApi.runSeries(seriesId),
    onSuccess: (result) => {
      notifications.show({
        color: 'teal',
        message: t('autodownload.runDone', {
          planned: result.planned,
          queued: result.queued,
        }),
      })
      queryClient.invalidateQueries({ queryKey: ['autodownload-misses'] })
    },
  })

  const own = settings.data?.own ?? {}

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={t('autodownload.settingsFor', { title })}
      size="lg"
      centered
    >
      {settings.isLoading ? (
        <Loader size="sm" />
      ) : (
        <Stack gap="sm">
          <AutoDownloadLangTabs
            langs={withOwn}
            saved={(lang) => ({ ...BLANK, ...(own[lang] ?? {}) })}
            onSave={(lang, value) => save.mutate({ lang, value })}
            saving={save.isPending}
            modes={settings.data?.modes ?? ['default', 'on', 'off']}
            modePrefix="titleMode"
            inheritable
            // При режиме «как решено для всех» причина молчания лежит в общих
            // настройках, а не здесь — об этом стоит сказать прямо.
            hint={(lang) =>
              settings.data?.effective?.[lang]
                ? undefined
                : t('autodownload.effectiveNothing')
            }
            extraActions={(lang) => (
              <>
                <Button
                  variant="light"
                  leftSection={<IconSearch size={16} />}
                  loading={run.isPending}
                  onClick={() => run.mutate()}
                >
                  {t('autodownload.runSeries')}
                </Button>
                {own[lang] && (
                  <Button
                    variant="subtle"
                    color="red"
                    loading={clear.isPending}
                    onClick={() => clear.mutate(lang)}
                  >
                    {t('autodownload.clearOwn')}
                  </Button>
                )}
              </>
            )}
          />

          {langs.length > 0 && (
            <Alert color="gray" p="xs">
              <Text size="xs">{t('autodownload.titleHint')}</Text>
            </Alert>
          )}
        </Stack>
      )}
    </Modal>
  )
}
