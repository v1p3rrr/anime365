import { Alert, Group, Loader, Text } from '@mantine/core'
import { IconLock } from '@tabler/icons-react'
import type { ReactNode } from 'react'

import { usePermissions, type PermissionKey } from '../hooks/usePermissions'
import { useT, type TranslationKey } from '../i18n'

interface Props {
  need: PermissionKey
  children: ReactNode
}

/**
 * Целый раздел под правом.
 *
 * Пункт меню без права уже спрятан, но адрес страницы можно ввести руками или
 * прийти по старой закладке — и тогда страница отправила бы свои запросы и
 * собрала по 403 в каждом блоке. Здесь она не рисуется вовсе, а причина названа
 * один раз и понятным текстом.
 *
 * Это удобство, а не защита: настоящая проверка стоит на каждом эндпоинте.
 */
export function RequirePermission({ need, children }: Props) {
  const { can, ready } = usePermissions()
  const { t } = useT()

  // До ответа про права не рисуем ни страницу, ни отказ: `can()` пока отвечает
  // утвердительно, и отказ мигнул бы у того, у кого право есть.
  if (!ready) {
    return (
      <Group justify="center" p="xl">
        <Loader />
      </Group>
    )
  }

  if (can(need)) return <>{children}</>

  return (
    <Alert color="gray" icon={<IconLock size={18} />} title={t('common.noAccess')} m="md">
      <Text size="sm">
        {t('common.noAccessHint', {
          permission: t(`users.permission.${need}` as TranslationKey),
        })}
      </Text>
    </Alert>
  )
}
