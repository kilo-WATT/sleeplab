import type { Meta, StoryObj } from '@storybook/react'
import MetricsChartSplit from './MetricsChartSplit'

const meta: Meta<typeof MetricsChartSplit> = {
  title: 'Components/MetricsChartSplit',
  component: MetricsChartSplit,
  tags: ['autodocs'],
}

export default meta
type Story = StoryObj<typeof MetricsChartSplit>

export const Default: Story = {
  args: {} as any,
}
