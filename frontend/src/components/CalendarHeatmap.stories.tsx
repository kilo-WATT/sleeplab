import type { Meta, StoryObj } from '@storybook/react'
import CalendarHeatmap from './CalendarHeatmap'

const meta: Meta<typeof CalendarHeatmap> = {
  title: 'Components/CalendarHeatmap',
  component: CalendarHeatmap,
  tags: ['autodocs'],
}

export default meta
type Story = StoryObj<typeof CalendarHeatmap>

export const Default: Story = {
  args: {} as any,
}
