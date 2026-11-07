import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/docs': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/openapi.json': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      }
    }
  },
  build: {
    modulePreload: true,
    chunkSizeWarningLimit: 3000,
    rollupOptions: {
      output: {
        manualChunks(id) {
          // Vite 的预加载辅助模块被所有动态 import 共用；不指定归属时会被并入
          // mermaid-vendor，导致首页也要静态加载整个 mermaid。固定放进核心 vendor。
          if (id.includes('vite/preload-helper')) return 'app-core-vendor'
          if (!id.includes('node_modules')) return

          const normalized = id.split('node_modules/')[1]
          if (!normalized) return 'vendor-misc'

          const segments = normalized.split('/')
          const packageName = segments[0].startsWith('@')
            ? `${segments[0]}/${segments[1]}`
            : segments[0]

          if (packageName === 'react' || packageName === 'react-dom' || packageName === 'scheduler') {
            return 'app-core-vendor'
          }

          if (
            packageName === 'react-router-dom' ||
            packageName === 'react-router' ||
            packageName === '@remix-run/router' ||
            packageName === 'zustand' ||
            packageName === 'axios' ||
            packageName === 'prop-types' ||
            packageName === 'clsx' ||
            packageName === 'tailwind-merge'
          ) {
            return 'app-core-vendor'
          }

          if (
            packageName === 'react-markdown' ||
            packageName.startsWith('remark') ||
            packageName.startsWith('rehype') ||
            packageName.startsWith('micromark') ||
            packageName.startsWith('mdast') ||
            packageName.startsWith('hast') ||
            packageName === 'unified' ||
            packageName.startsWith('unist') ||
            packageName === 'vfile' ||
            packageName.startsWith('vfile-') ||
            packageName === 'property-information' ||
            packageName === 'comma-separated-tokens' ||
            packageName === 'space-separated-tokens'
          ) {
            return 'markdown-vendor'
          }

          if (
            packageName === 'mermaid' ||
            packageName.startsWith('@mermaid-js') ||
            packageName === '@braintree/sanitize-url' ||
            packageName === '@iconify/utils' ||
            packageName === 'dagre-d3-es' ||
            packageName === 'dayjs' ||
            packageName === 'khroma' ||
            packageName === 'lodash-es' ||
            packageName === 'marked' ||
            packageName === 'roughjs' ||
            packageName === 'stylis' ||
            packageName === 'ts-dedent' ||
            packageName === 'uuid' ||
            packageName === 'd3-sankey'
          ) {
            return 'mermaid-vendor'
          }

          if (packageName === 'katex' || packageName === 'react-katex') {
            return 'katex-vendor'
          }

          if (
            packageName === '@monaco-editor/react' ||
            packageName.startsWith('@monaco-editor') ||
            packageName.includes('codemirror') ||
            packageName.startsWith('@codemirror') ||
            packageName.startsWith('prosemirror') ||
            packageName.startsWith('monaco-editor')
          ) {
            return 'editor-vendor'
          }

          if (
            packageName.startsWith('d3') &&
            packageName !== 'd3-sankey'
          ) {
            return 'd3-vendor'
          }

          if (packageName.startsWith('recharts')) {
            return 'recharts-vendor'
          }

          if (
            packageName.startsWith('cytoscape') ||
            packageName.startsWith('dagre') ||
            packageName.startsWith('elkjs')
          ) {
            return 'graph-vendor'
          }

          if (
            packageName === 'framer-motion' ||
            packageName.startsWith('motion')
          ) {
            return 'motion-vendor'
          }

          if (packageName.startsWith('react-icons')) {
            return 'ui-icons-vendor'
          }

          if (
            packageName === 'dompurify' ||
            packageName.startsWith('vscode-languageserver') ||
            packageName === 'vscode-jsonrpc' ||
            packageName === 'vscode-uri'
          ) {
            return 'editor-vendor'
          }
        },
      },
    },
  },
  experimental: {
    rollupCjsResolve: true,
  },
})
