import type { Meta, StoryObj } from '@storybook/react'
import AHITrendChart from './AHITrendChart'

const meta: Meta<typeof AHITrendChart> = {
  title: 'Components/AHITrendChart',
  component: AHITrendChart,
  tags: ['autodocs'],
}

export default meta
type Story = StoryObj<typeof AHITrendChart>

export const Default: Story = {
  args: {} as any,
}
