import type { Meta, StoryObj } from '@storybook/react'
import WearableSleepSummaryChart from './WearableSleepSummaryChart'

const meta: Meta<typeof WearableSleepSummaryChart> = {
  title: 'Components/WearableSleepSummaryChart',
  component: WearableSleepSummaryChart,
  tags: ['autodocs'],
}

export default meta
type Story = StoryObj<typeof WearableSleepSummaryChart>

export const Default: Story = {
  args: {} as any,
}
