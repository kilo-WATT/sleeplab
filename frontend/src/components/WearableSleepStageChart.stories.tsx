import type { Meta, StoryObj } from '@storybook/react'
import WearableSleepStageChart from './WearableSleepStageChart'

const meta: Meta<typeof WearableSleepStageChart> = {
  title: 'Components/WearableSleepStageChart',
  component: WearableSleepStageChart,
  tags: ['autodocs'],
}

export default meta
type Story = StoryObj<typeof WearableSleepStageChart>

export const Default: Story = {
  args: {} as any,
}
