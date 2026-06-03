import type { Meta, StoryObj } from '@storybook/react'
import InfoPopover from './InfoPopover'

const meta: Meta<typeof InfoPopover> = {
  title: 'Components/InfoPopover',
  component: InfoPopover,
  tags: ['autodocs'],
}

export default meta
type Story = StoryObj<typeof InfoPopover>

export const Default: Story = {
  args: {} as any,
}
