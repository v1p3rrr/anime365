import {
  Alert,
  Card,
  Divider,
  Group,
  SegmentedControl,
  Stack,
  Switch,
  Text,
} from '@mantine/core'

import { useT, type TranslationKey } from '../i18n'
import { LangTag } from './Flag'
import {
  PAGE_SIZES,
  splitType,
  TRANSLATION_TYPES,
  useRomajiPreference,
  usePreferences,
  type PagingMode,
} from '../stores/preferences'

/**
 * Личные настройки отображения на общей странице настроек.
 *
 * Отделены рамкой и подписью намеренно: всё остальное на этой странице —
 * серверное и общее для всех, кто откроет сайт, а это хранится в браузере и
 * у каждого своё. Без явной пометки пользователь решил бы, что выключил
 * английскую озвучку всем сразу.
 */
export function DisplaySettings() {
  const { t } = useT()
  const { prefs, toggleType, setPaging, setPageSize } = usePreferences()
  const [preferRomaji, setPreferRomaji] = useRomajiPreference()

  // Последний включённый выключить нельзя — блокируем сам переключатель,
  // а не молча игнорируем клик: иначе выглядит как поломка.
  const isLast = prefs.types.length === 1

  return (
    <Card withBorder padding="sm">
      <Text fw={600} mb={4}>
        {t('display.title')}
      </Text>

      <Alert color="gray" p="xs" mb="sm">
        <Text size="xs">{t('display.personalHint')}</Text>
      </Alert>

      <Text size="sm" fw={500}>
        {t('display.types')}
      </Text>
      <Text size="xs" c="dimmed" mb="xs">
        {t('display.typesHint')}
      </Text>

      <Stack gap="xs">
        {TRANSLATION_TYPES.map((type) => {
          const [kind, lang] = splitType(type)
          const checked = prefs.types.includes(type)
          return (
            // Цвет тумблера намеренно один для всех: цвет здесь читается как
            // состояние, а состояние уже показано положением и тем, что
            // выключенный серый. Язык назван рядом дважды — флагом и кодом, —
            // так что разноцветные тумблеры добавляли только вопрос «а зелёный
            // от синего чем отличается». В сводке переводов цвет по языкам
            // остаётся: там он разделяет блоки, и это его работа.
            <Switch
              key={type}
              size="sm"
              checked={checked}
              disabled={checked && isLast}
              onChange={() => toggleType(type)}
              label={
                <Group gap={6} wrap="nowrap">
                  <Text size="sm">
                    {t(`kind.${kind}` as TranslationKey)} · <LangTag lang={lang} />
                  </Text>
                </Group>
              }
            />
          )
        })}
      </Stack>

      <Text size="xs" c="dimmed" mt="sm">
        {t('display.orderHint')}
      </Text>

      <Divider my="md" />

      {/* Отдельно от языка интерфейса намеренно: язык интерфейса — про подписи
          и кнопки, а это про то, под каким названием человек привык узнавать
          тайтл. Многие читают интерфейс по-русски, но ищут аниме по ромадзи. */}
      <Switch
        size="sm"
        checked={preferRomaji}
        onChange={(event) => setPreferRomaji(event.currentTarget.checked)}
        label={t('display.preferRomaji')}
        description={t('display.preferRomajiHint')}
      />

      <Divider my="md" />

      <Text size="sm" fw={500}>
        {t('display.paging')}
      </Text>
      <Text size="xs" c="dimmed" mb="xs">
        {t('display.pagingHint')}
      </Text>

      <SegmentedControl
        size="xs"
        value={prefs.paging}
        onChange={(value) => setPaging(value as PagingMode)}
        data={[
          { value: 'scroll', label: t('display.pagingScroll') },
          { value: 'pages', label: t('display.pagingPages') },
        ]}
      />

      <Text size="sm" fw={500} mt="md">
        {t('display.pageSize')}
      </Text>
      <Text size="xs" c="dimmed" mb="xs">
        {t('display.pageSizeHint')}
      </Text>

      <SegmentedControl
        size="xs"
        value={String(prefs.pageSize)}
        onChange={(value) => setPageSize(Number(value))}
        data={PAGE_SIZES.map((size) => ({ value: String(size), label: String(size) }))}
      />
    </Card>
  )
}
