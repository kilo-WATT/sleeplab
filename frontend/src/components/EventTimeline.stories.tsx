import type { Meta, StoryObj } from '@storybook/react'
import EventTimeline from './EventTimeline'

const meta: Meta<typeof EventTimeline> = {
  title: 'Components/EventTimeline',
  component: EventTimeline,
  tags: ['autodocs'],
}

export default meta
type Story = StoryObj<typeof EventTimeline>

export const Default: Story = {
  args: {} as any,
}
