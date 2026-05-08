// src/kohaku-hub-ui/vite.config.js
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import VueRouter from 'unplugin-vue-router/vite'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import UnoCSS from 'unocss/vite'

export default defineConfig({
  plugins: [
    // Must be before Vue plugin
    VueRouter({
      routesFolder: 'src/pages',
      dts: 'src/typed-router.d.ts',
      extensions: ['.vue'],
      exclude: ['**/components/**']
    }),

    vue({
      template: {
        compilerOptions: {
          // Treat cropper custom elements as custom elements, not Vue components
          isCustomElement: (tag) => tag.startsWith('cropper-')
        }
      }
    }),

    // Auto import APIs
    AutoImport({
      imports: [
        'vue',
        'pinia',
        {
          'vue-router': [
            'onBeforeRouteLeave',
            'onBeforeRouteUpdate',
            'useLink'
          ]
        },
        {
          'vue-router/auto': ['useRoute', 'useRouter']
        }
      ],
      resolvers: [ElementPlusResolver()],
      dts: 'src/auto-imports.d.ts',
      eslintrc: {
        enabled: true
      }
    }),

    // Auto import components.
    // importStyle: false — main.js already imports the full
    // `element-plus/dist/index.css`, so the per-component
    // `element-plus/es/components/<name>/style/css` injections that the
    // resolver normally adds are redundant. They were the main source of
    // dev-server lazy dep discovery (and the resulting full-page reloads)
    // when navigating to a page that first uses a new component.
    Components({
      resolvers: [ElementPlusResolver({ importStyle: false })],
      dts: 'src/components.d.ts',
      dirs: ['src/components']
    }),

    UnoCSS()
  ],

  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },

  // Pre-bundle every third-party dep that the UI actually imports, and turn
  // OFF runtime discovery. Combined effect:
  //   * Vite's dep optimizer never finds a "new" dep at runtime, so it never
  //     issues the "optimized dependencies changed. reloading" full-reload
  //     that disrupts whatever the user is doing on the page.
  //   * Real code edits still go through the normal HMR / file-watcher path
  //     and reload as expected.
  // If a dep is missing from this list it surfaces as an explicit module-not-
  // found error in the terminal — easier to debug than a silent reload.
  // MAINTENANCE: when you add a new third-party import to UI source code,
  // append the exact specifier here. See AGENTS.md ("Dev server reload policy").
  optimizeDeps: {
    include: [
      // Vue core
      'vue',
      'vue-router',
      'pinia',

      // UI library
      'element-plus',
      'element-plus/es',

      // HTTP / time
      'axios',
      'dayjs',
      'dayjs/plugin/relativeTime',
      'dayjs/plugin/timezone',
      'dayjs/plugin/utc',

      // Code editor
      'codemirror',
      '@codemirror/commands',
      '@codemirror/state',
      '@codemirror/theme-one-dark',
      '@codemirror/view',
      '@codemirror/lang-cpp',
      '@codemirror/lang-css',
      '@codemirror/lang-html',
      '@codemirror/lang-java',
      '@codemirror/lang-javascript',
      '@codemirror/lang-json',
      '@codemirror/lang-markdown',
      '@codemirror/lang-php',
      '@codemirror/lang-python',
      '@codemirror/lang-rust',
      '@codemirror/lang-sql',
      '@codemirror/lang-xml',

      // Syntax highlighting
      'highlight.js/lib/core',
      'highlight.js/lib/languages/bash',
      'highlight.js/lib/languages/cpp',
      'highlight.js/lib/languages/csharp',
      'highlight.js/lib/languages/css',
      'highlight.js/lib/languages/go',
      'highlight.js/lib/languages/java',
      'highlight.js/lib/languages/javascript',
      'highlight.js/lib/languages/json',
      'highlight.js/lib/languages/kotlin',
      'highlight.js/lib/languages/markdown',
      'highlight.js/lib/languages/php',
      'highlight.js/lib/languages/python',
      'highlight.js/lib/languages/ruby',
      'highlight.js/lib/languages/rust',
      'highlight.js/lib/languages/shell',
      'highlight.js/lib/languages/sql',
      'highlight.js/lib/languages/swift',
      'highlight.js/lib/languages/typescript',
      'highlight.js/lib/languages/xml',
      'highlight.js/lib/languages/yaml',

      // Misc
      'cropperjs',
      'hyparquet',
      'isomorphic-dompurify',
      'js-sha256',
      'js-yaml',
      'markdown-it',
      'mermaid',
      'panzoom'
    ],
    noDiscovery: true
  },

  build: {
    // Target modern browsers (skip legacy transpilation)
    target: 'esnext',

    // Enable minification (rolldown uses built-in minifier)
    minify: true,

    // Disable source maps in production (faster builds)
    sourcemap: false,

    // Optimize CSS
    cssMinify: true,

    rollupOptions: {
      output: {
        manualChunks: (id) => {
          // Split highlight.js into separate chunk (it's large)
          if (id.includes('highlight.js')) {
            return 'highlight';
          }
          // Split element-plus into separate chunk
          if (id.includes('element-plus')) {
            return 'element-plus';
          }
          // Split core vendor libraries
          if (id.includes('node_modules/vue/') ||
              id.includes('node_modules/vue-router/') ||
              id.includes('node_modules/pinia/')) {
            return 'vendor';
          }
        }
      }
    },
    chunkSizeWarningLimit: 1000, // Increase limit to 1000kb to reduce warnings
  },

  // Enable caching for faster rebuilds
  cacheDir: 'node_modules/.vite',

  server: {
    port: 5173,
    proxy: {
      // Mount the admin Vite dev server under /admin so `make ui` exposes the
      // admin portal at the same origin as the main UI. Admin builds with
      // base: '/admin/', and ws: true keeps its HMR socket working through
      // this proxy. The bypass redirects bare `/admin` to `/admin/` so users
      // never see Vite's "did you mean to visit /admin/" base-URL hint page.
      '/admin': {
        target: 'http://localhost:5174',
        changeOrigin: true,
        ws: true,
        bypass: (req, res) => {
          const path = (req.url || '').split('?')[0]
          if (path === '/admin') {
            const qs = (req.url || '').slice(path.length)
            res.writeHead(302, { Location: '/admin/' + qs })
            res.end()
            return false
          }
        }
      },
      // Proxy API calls
      '/api': {
        target: 'http://localhost:48888',
        changeOrigin: true
      },
      // Proxy organization API endpoints (must be more specific to avoid catching /organizations frontend routes)
      // This matches /org/ followed by anything (but not /organizations)
      '^/org/': {
        target: 'http://localhost:48888',
        changeOrigin: true
      },
      // Proxy Git HTTP Smart Protocol endpoints
      // This catches: /{namespace}/{name}.git/info/refs, /{namespace}/{name}.git/git-upload-pack, etc.
      // Enables native Git clone/push operations
      '^/[^/]+/[^/]+\\.git/(info/refs|git-upload-pack|git-receive-pack|HEAD)': {
        target: 'http://localhost:48888',
        changeOrigin: true,
        configure: (proxy, options) => {
          proxy.on('proxyReq', (proxyReq, req, res) => {
            // Disable buffering for Git protocol streaming
            proxyReq.setHeader('X-Forwarded-Proto', 'http');
          });
        }
      },
      // Proxy Git LFS endpoints
      // This catches: /{namespace}/{name}.git/info/lfs/*
      '^/[^/]+/[^/]+\\.git/info/lfs/': {
        target: 'http://localhost:48888',
        changeOrigin: true
      },
      // Proxy file resolve/download endpoints (models/datasets/spaces)
      // This catches: /models/*/resolve/*, /datasets/*/resolve/*, /spaces/*/resolve/*
      '^/(models|datasets|spaces)/.+/resolve/': {
        target: 'http://localhost:48888',
        changeOrigin: true
      },
      // Proxy direct download endpoints (for backward compatibility)
      // This catches: /namespace/name/resolve/*
      '^/[^/]+/[^/]+/resolve/': {
        target: 'http://localhost:48888',
        changeOrigin: true
      }
    }
  }
})
