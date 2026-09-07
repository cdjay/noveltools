import { create } from 'zustand'
import { persist } from 'zustand/middleware'

/** 纯界面偏好，持久化到 localStorage；与业务状态完全隔离，不跨书籍串数据。 */
type UiPrefs = {
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
  navCollapsed: false,
  readerMode: 'read',
  readerFontSize: 17,
  readerShowChapters: true,
  readerShowContext: true,
  toggleNav: () => set(state => ({ navCollapsed: !state.navCollapsed })),
  setReaderMode: (readerMode) => set({ readerMode }),
  setReaderFontSize: (readerFontSize) => set({ readerFontSize: Math.max(14, Math.min(22, readerFontSize)) }),
  toggleReaderChapters: () => set(state => ({ readerShowChapters: !state.readerShowChapters })),
  toggleReaderContext: () => set(state => ({ readerShowContext: !state.readerShowContext })),
}), { name: 'noveltools-ui-prefs' }))
