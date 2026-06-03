import type { Meta, StoryObj } from '@storybook/react'
import GlossaryText from './GlossaryText'

const meta: Meta<typeof GlossaryText> = {
  title: 'Components/GlossaryText',
  component: GlossaryText,
  tags: ['autodocs'],
}

export default meta
type Story = StoryObj<typeof GlossaryText>

export const Default: Story = {
  args: {} as any,
}
