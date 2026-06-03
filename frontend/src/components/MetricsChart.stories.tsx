import type { Meta, StoryObj } from '@storybook/react'
import MetricsChart from './MetricsChart'

const meta: Meta<typeof MetricsChart> = {
  title: 'Components/MetricsChart',
  component: MetricsChart,
  tags: ['autodocs'],
}

export default meta
type Story = StoryObj<typeof MetricsChart>

export const Default: Story = {
  args: {} as any,
}
