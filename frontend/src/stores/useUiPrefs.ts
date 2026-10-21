import { create } from 'zustand'
import { persist } from 'zustand/middleware'

/** 纯界面偏好，持久化到 localStorage；与业务状态完全隔离，不跨书籍串数据。 */
type UiPrefs = {
  textFormat: 'raw' | 'markdown'
  setTextFormat: (format: 'raw' | 'markdown') => void
  navCollapsed: boolean
  readerMode: 'read' | 'edit'
  readerFontSize: number
  readerShowChapters: boolean
  readerShowContext: boolean
  toggleNav: () => void
  setReaderMode: (mode: 'read' | 'edit') => void
  setReaderFontSize: (size: number) => void
  toggleReaderChapters: () => void
  toggleReaderContext: () => void
}

export const useUiPrefs = create<UiPrefs>()(persist((set) => ({
  textFormat: 'raw',
  setTextFormat: (textFormat) => set({ textFormat }),
  navCollapsed: false,
  readerMode: 'read',
  readerFontSize: 22,
  readerShowChapters: true,
  readerShowContext: true,
  toggleNav: () => set(state => ({ navCollapsed: !state.navCollapsed })),
  setReaderMode: (readerMode) => set({ readerMode }),
  setReaderFontSize: (readerFontSize) => set({ readerFontSize: Math.max(16, Math.min(32, readerFontSize)) }),
  toggleReaderChapters: () => set(state => ({ readerShowChapters: !state.readerShowChapters })),
  toggleReaderContext: () => set(state => ({ readerShowContext: !state.readerShowContext })),
}), {
  name: 'noveltools-ui-prefs',
  version: 2,
  migrate: (persisted) => {
    const saved = persisted as Partial<UiPrefs>
    return { ...saved, readerFontSize: !saved.readerFontSize || [17, 18].includes(saved.readerFontSize) ? 22 : saved.readerFontSize }
  },
}))
