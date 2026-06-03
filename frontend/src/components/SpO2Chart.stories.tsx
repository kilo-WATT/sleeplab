import type { Meta, StoryObj } from '@storybook/react'
import SpO2Chart from './SpO2Chart'

const meta: Meta<typeof SpO2Chart> = {
  title: 'Components/SpO2Chart',
  component: SpO2Chart,
  tags: ['autodocs'],
}

export default meta
type Story = StoryObj<typeof SpO2Chart>

export const Default: Story = {
  args: {} as any,
}
