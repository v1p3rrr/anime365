import { Alert, Button, Code, CopyButton, Group, Modal, Stack, Text } from '@mantine/core'
import { IconAlertTriangle, IconCheck, IconCopy } from '@tabler/icons-react'

import { useT } from '../i18n'

interface Props {
  /** Открытый пароль. `null` — показывать нечего, окно закрыто. */
  password: string | null
  /** Кому он выдан — чтобы не перепутать при нескольких учётных записях. */
  name: string
  onClose: () => void
}

/**
 * Окно «вот выданный пароль».
 *
 * ⚠️ Значение приходит с сервера **единственный раз**: в базе лежит только
 * argon2-хеш, и второй раз узнать пароль нельзя — можно лишь сбросить снова.
 * Поэтому окно закрывается явной кнопкой, а не кликом мимо и не Escape: у
 * случайного промаха здесь слишком высокая цена.
 *
 * Кнопка копирования обязательна. Пароль из 15 символов со спецсимволами
 * переписывают с ошибками, а понять, что ошибся, можно будет только на входе.
 */
export function IssuedPasswordModal({ password, name, onClose }: Props) {
  const { t } = useT()

  return (
    <Modal
      opened={password !== null}
      onClose={onClose}
      title={t('users.issuedTitle')}
      centered
      closeOnClickOutside={false}
      closeOnEscape={false}
      withCloseButton={false}
    >
      <Stack gap="sm">
        <Text size="sm">{t('users.issuedFor', { name })}</Text>

        <Group gap="xs" wrap="nowrap">
          <Code style={{ fontSize: 16, padding: '8px 12px', flex: 1, wordBreak: 'break-all' }}>
            {password}
          </Code>
          <CopyButton value={password ?? ''}>
            {({ copied, copy }) => (
              <Button
                variant={copied ? 'filled' : 'default'}
                color={copied ? 'teal' : undefined}
                onClick={copy}
                leftSection={copied ? <IconCheck size={16} /> : <IconCopy size={16} />}
              >
                {copied ? t('common.copied') : t('common.copy')}
              </Button>
            )}
          </CopyButton>
        </Group>

        <Alert color="orange" icon={<IconAlertTriangle size={16} />}>
          {t('users.issuedWarning')}
        </Alert>

        <Group justify="flex-end">
          <Button onClick={onClose}>{t('users.issuedSaved')}</Button>
        </Group>
      </Stack>
    </Modal>
  )
}
