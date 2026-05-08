# Kohaku Hub UI

This directory contains the frontend application for Kohaku Hub, providing the user interface for repository management, user profiles, and administrative tasks.

## Technology Stack

- **Framework**: [Vue 3](https://vuejs.org/) with `<script setup>` syntax
- **Build Tool**: [Vite](https://vitejs.dev/) for fast development and optimized builds
- **State Management**: [Pinia](https://pinia.vuejs.org/) for centralized state management
- **Routing**: [Vue Router](https://router.vuejs.org/) with file-based routing (`unplugin-vue-router`)
- **Styling**: [UnoCSS](https://unocss.dev/) for utility-first CSS
- **UI Components**: [Element Plus](https://element-plus.org/) for a rich set of UI components
- **Auto Imports**: `unplugin-auto-import` and `unplugin-vue-components` for automatic imports of Vue APIs and components

## Project Structure

```
src/
├── api/          # API client for communicating with the backend
├── assets/       # Static assets (images, fonts)
├── components/   # Reusable Vue components
├── layouts/      # Layout components (e.g., TheHeader, TheFooter)
├── pages/        # Page components for each route (file-based routing)
├── stores/       # Pinia stores for state management (auth, theme)
├── styles/       # Global styles and themes
├── utils/        # Utility functions
├── App.vue       # Main application component
└── main.js       # Application entry point
```

## Getting Started

### Prerequisites

- Node.js 18+
- pnpm 10+

### Installation

From the repository root:

```bash
pnpm install
```

### Development

To start the development server with hot-reloading:

```bash
pnpm run dev:ui
```

If you prefer to run it inside this directory directly:

```bash
pnpm dev
```

The application will be available at `http://localhost:5173`. The Vite development server is configured to proxy API requests to the backend running on `http://localhost:48888`.

### Build

To build the application for production:

```bash
pnpm run build:ui
```

The optimized static files will be generated in the `dist/` directory.

## Key Features

- **Repository Viewer**: Browse files, view commits, and manage repository settings.
- **User and Organization Profiles**: View user and organization information, repositories, and settings.
- **Authentication**: Login, registration, and session management.
- **File Management**: Upload, download, and view files.
- **Dark Mode**: Theme switching with persistence.
- **Responsive Design**: Adapts to different screen sizes.
