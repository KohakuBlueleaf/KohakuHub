import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import VueRouter from 'unplugin-vue-router/vite'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import UnoCSS from 'unocss/vite'

export default defineConfig({
  base: '/admin/',
  plugins: [
    // Must be before Vue plugin
    VueRouter({
      routesFolder: 'src/pages',
      dts: 'src/typed-router.d.ts',
      extensions: ['.vue'],
      exclude: ['**/components/**']
    }),

    vue(),

    // Auto import APIs
    AutoImport({
      imports: [
        'vue',
        'pinia',
        'vue-router',
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
    // `element-plus/es/components/<name>/style/css` injections are
    // redundant. They were the main source of dev-server lazy dep discovery
    // (and the resulting full-page reloads) when navigating to a page that
    // first uses a new component.
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
          // Split element-plus into separate chunk
          if (id.includes('element-plus')) {
            return 'element-plus'
          }
          // Split echarts into separate chunk
          if (id.includes('echarts')) {
            return 'echarts'
          }
          // Split core vendor libraries
          if (
            id.includes('node_modules/vue/') ||
            id.includes('node_modules/vue-router/') ||
            id.includes('node_modules/pinia/')
          ) {
            return 'vendor'
          }
        }
      }
    },
    chunkSizeWarningLimit: 1000
  },

  // Pre-bundle every third-party dep that admin actually imports, and turn
  // OFF runtime discovery. Combined effect:
  //   * Vite's dep optimizer never finds a "new" dep at runtime, so it never
  //     issues the "optimized dependencies changed. reloading" full-reload
  //     that resets the in-memory admin token.
  //   * Real code edits still go through the normal HMR / file-watcher path
  //     and reload as expected.
  // If a dep is missing from this list it surfaces as an explicit module-not-
  // found error in the terminal — easier to debug than a silent reload.
  optimizeDeps: {
    include: [
      'vue',
      'vue-router',
      'pinia',
      'element-plus',
      'element-plus/es',
      'axios',
      'dayjs',
      'chart.js',
      'vue-chartjs'
    ],
    noDiscovery: true
  },

  // Enable caching for faster rebuilds
  cacheDir: 'node_modules/.vite',

  server: {
    port: 5174, // Different port from main UI (5173)
    proxy: {
      // Proxy admin API calls
      '/admin/api': {
        target: 'http://localhost:48888',
        changeOrigin: true
      },
      // Proxy standard API calls (for admin token usage)
      '/api': {
        target: 'http://localhost:48888',
        changeOrigin: true
      },
      // KohakuHub public hf_hub-compatible URLs (no ``/api`` prefix).
      // The chain tester's Live real probe hits
      // ``/<repo_type>s/<ns>/<name>/resolve/...`` to mirror the exact
      // wire shape an hf_hub client uses. Without these rules vite
      // returns 404 (no SPA route matches, no proxy forwards), the
      // probe never reaches the backend, and the timeline reports
      // CHAIN_EXHAUSTED for non-existent reasons. Production (nginx →
      // backend, same origin) doesn't need this — the SPA shares
      // origin with the backend so requests arrive there directly.
      '/models': {
        target: 'http://localhost:48888',
        changeOrigin: true
      },
      '/datasets': {
        target: 'http://localhost:48888',
        changeOrigin: true
      },
      '/spaces': {
        target: 'http://localhost:48888',
        changeOrigin: true
      }
    }
  }
})
