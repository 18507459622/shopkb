import DOMPurify from 'dompurify'
import { marked } from 'marked'

// marked 渲染 + DOMPurify 消毒（禁 raw HTML，开 GFM 表格）
export function renderMarkdown(text: string): string {
  if (!text) return ''
  const html = marked.parse(text, { async: false }) as string
  return DOMPurify.sanitize(html)
}
