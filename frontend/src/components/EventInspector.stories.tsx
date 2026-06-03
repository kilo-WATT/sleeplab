import type { Meta, StoryObj } from '@storybook/react'
import EventInspector from './EventInspector'

const meta: Meta<typeof EventInspector> = {
  title: 'Components/EventInspector',
  component: EventInspector,
  tags: ['autodocs'],
}

export default meta
type Story = StoryObj<typeof EventInspector>

export const Default: Story = {
  args: {} as any,
}
