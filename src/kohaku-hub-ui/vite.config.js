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

    // Auto import components
    Components({
      resolvers: [ElementPlusResolver()],
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

  optimizeDeps: {
    include: [
      'highlight.js',
    ],
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
