/** NOVELTOOLS 设计变量：纸白、暖灰、墨色、黛青。所有页面统一引用，不逐页堆样式。 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // 纸面底色
        paper: {
          DEFAULT: '#faf8f4',
          raised: '#fffefb',
          sunken: '#f3f0e9',
        },
        // 墨色文字层级
        ink: {
          DEFAULT: '#2b2822',
          soft: '#5f5a4e',
          faint: '#938d7e',
        },
        // 分隔线
        line: {
          DEFAULT: '#e5e0d3',
          soft: '#efebe0',
          strong: '#d5cfbd',
        },
        // 黛青：唯一强调色
        accent: {
          50: '#eff4f4',
          100: '#dde8e8',
          200: '#bcd1d2',
          300: '#8fb0b2',
          400: '#5f888b',
          500: '#476e71',
          600: '#3a5b5e',
          700: '#314a4d',
          800: '#293c3f',
          900: '#1f2f31',
        },
        // 语义状态色（低饱和）
        ok: {
          soft: '#e8f0e6',
          DEFAULT: '#3e7c59',
          strong: '#2f6045',
        },
        warn: {
          soft: '#f6eedd',
          DEFAULT: '#a97c2f',
          strong: '#7d5a1e',
        },
        danger: {
          soft: '#f6e8e4',
          DEFAULT: '#a84636',
          strong: '#823425',
        },
      },
      fontFamily: {
        sans: ['"Noto Sans SC Variable"', '"Noto Sans SC"', '"Microsoft YaHei"', 'system-ui', 'sans-serif'],
        serif: ['"Noto Serif SC Variable"', '"Noto Serif SC"', '"Source Han Serif SC"', '"Songti SC"', 'serif'],
        mono: ['"SFMono-Regular"', 'Consolas', '"Liberation Mono"', 'Menlo', 'monospace'],
      },
      fontSize: {
        meta: ['0.75rem', { lineHeight: '1.5' }],
        body: ['0.875rem', { lineHeight: '1.65' }],
      },
      borderRadius: {
        sm: '6px',
        md: '8px',
        lg: '12px',
      },
      boxShadow: {
        card: '0 1px 2px rgba(43, 40, 34, 0.05)',
        pop: '0 8px 28px rgba(43, 40, 34, 0.12)',
      },
      transitionDuration: {
        fast: '120ms',
      },
      maxWidth: {
        measure: '42rem',
      },
    },
  },
  plugins: [],
}
