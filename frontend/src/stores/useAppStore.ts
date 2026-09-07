import { create } from 'zustand'
import type { Book, Chapter } from '../types'

type AppState = {
  books: Book[]
  currentBook: Book | null
  chapters: Chapter[]
  currentChapter: Chapter | null
  setBooks: (books: Book[]) => void
  setCurrentBook: (book: Book | null) => void
  setChapters: (chapters: Chapter[]) => void
  setCurrentChapter: (chapter: Chapter | null) => void
}

export const useAppStore = create<AppState>((set) => ({
  books: [], currentBook: null, chapters: [], currentChapter: null,
  setBooks: (books) => set({ books }),
  setCurrentBook: (currentBook) => set(state => state.currentBook?.id === currentBook?.id
    ? { currentBook }
    : { currentBook, currentChapter: null, chapters: [] }),
  setChapters: (chapters) => set(state => ({ chapters, currentBook: state.currentBook ? { ...state.currentBook, chapter_count: chapters.length, total_word_count: chapters.reduce((sum, chapter) => sum + chapter.word_count, 0) } : null })), setCurrentChapter: (currentChapter) => set({ currentChapter }),
}))
