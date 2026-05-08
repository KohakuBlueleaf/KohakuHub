# KohakuHub Admin Portal

Admin web interface for managing KohakuHub users, organizations, and storage quotas.

## Features

- **User Management**: List, view, create, delete users
- **Email Verification**: Manage email verification status
- **Quota Management**: Set and monitor storage quotas
- **Database Viewer**: Raw database table inspection
- **Fallback Source Management**: Configure external repository sources
- **System Statistics**: View system-wide statistics
- **Secure Authentication**: Token-based admin authentication
- **Dark Mode**: Full dark mode support

## Development

```bash
# Install dependencies from repository root
pnpm install

# Start development server (port 5174)
pnpm run dev:admin

# Build for production
pnpm run build:admin
```

If you prefer to run commands inside this directory directly:

```bash
pnpm dev
pnpm build
pnpm preview
```

Access the dev server at: **http://localhost:5174**

## Authentication

The admin portal uses token-based authentication. Configure the admin token in your backend:

```bash
# Set in environment or docker-compose.yml
KOHAKU_HUB_ADMIN_SECRET_TOKEN=your-secret-token-here

# Generate a secure token using:
python scripts/generate_secret.py 64
```

## Pages

### 1. Login (`/login`)
- Secure token input
- Auto-redirect if already authenticated
- Token stored in localStorage

### 2. Dashboard (`/`)
- System statistics overview
- Total users, organizations, repositories
- Private vs public repository counts
- Quick action buttons

### 3. User Management (`/users`)
- List all users with pagination
- View detailed user information
- Create new users with custom quotas
- Delete users (with safety checks)
- Toggle email verification status
- View storage usage per user

### 4. Quota Management (`/quotas`)
- Search by username or organization
- View private/public quota separately
- Set quotas with human-readable sizes (10GB, 50GB, etc.)
- Recalculate storage usage
- Visual progress bars
- Detailed usage statistics

### 5. Database Viewer (`/database`)
- Raw, read-only access to database tables
- Useful for debugging and data inspection
- Supports pagination and sorting

### 6. Fallback Sources (`/fallback-sources`)
- Configure external sources (e.g., HuggingFace)
- Set priority and credentials for fallback repositories
- Enable/disable sources globally

## Configuration

The admin portal connects to the KohakuHub backend API at:
- **Development**: `http://localhost:48888/api/admin` (proxied via Vite)
- **Production**: `/api/admin` (same domain as main app)

## Tech Stack

- **Vue 3** - Composition API with `<script setup>`
- **Element Plus** - Enterprise-grade UI components
- **UnoCSS** - Atomic CSS with icons
- **Pinia** - State management
- **Vue Router** - File-based routing (unplugin-vue-router)
- **Axios** - HTTP client
- **Day.js** - Date formatting
- **Vite** - Fast build tool

## Quota Management

The system supports separate quotas for private and public repositories:

- **Private Quota**: Storage limit for private repositories
- **Public Quota**: Storage limit for public repositories

### Setting Quotas

Values can be entered as:
- **Human-readable**: `10GB`, `500MB`, `1TB`, `50GB`
- **Unlimited**: `unlimited` or leave empty
- **Bytes**: `10737418240` (if you prefer exact values)

Examples:
- `10GB` = 10,737,418,240 bytes
- `50GB` = 53,687,091,200 bytes
- `100GB` = 107,374,182,400 bytes
- `500GB` = 536,870,912,000 bytes
- `1TB` = 1,099,511,627,776 bytes

## Security Features

- ✅ Admin token stored in `localStorage`
- ✅ Token verification uses SHA3-512 hashing
- ✅ Constant-time comparison prevents timing attacks
- ✅ Automatic logout on invalid token
- ✅ All API calls include token in `X-Admin-Token` header
- ✅ Force delete confirmation for users with repositories

## User Deletion Safety

When deleting a user:

1. **First Attempt**: Checks if user owns repositories
2. **If repositories exist**: Shows list and requires confirmation for force delete
3. **Force Delete**: Deletes user AND all their repositories
4. **Always deletes**: Sessions, tokens, email verifications, org memberships

## Layout

```
Admin Portal
├── Sidebar Navigation
│   ├── Dashboard
│   ├── Users
│   └── Quotas
├── Header
│   ├── Dark mode toggle
│   └── Logout button
└── Main Content Area
```

## Development Tips

- Auto-import enabled for Vue, Pinia, Router APIs
- Auto-import enabled for Element Plus components
- UnoCSS utilities available (Tailwind-like classes)
- Icons: Use `i-carbon-{name}` or `i-ep-{name}` classes
- Dark mode: Automatically syncs with system preference

## API Endpoints Used

- `GET /api/admin/stats` - System statistics
- `GET /api/admin/users` - List users
- `GET /api/admin/users/{username}` - Get user details
- `POST /api/admin/users` - Create user
- `DELETE /api/admin/users/{username}` - Delete user
- `PATCH /api/admin/users/{username}/email-verification` - Set email verification
- `GET /api/admin/quota/{namespace}` - Get quota info
- `PUT /api/admin/quota/{namespace}` - Set quota
- `POST /api/admin/quota/{namespace}/recalculate` - Recalculate storage

## Production Deployment

1. Build the admin portal:
   ```bash
   pnpm run build:admin
   ```

2. Serve `dist/` directory via Nginx/Apache

3. Configure backend proxy for `/api/admin` endpoints

4. Set strong admin token in production environment

## Troubleshooting

### Icons not showing
- The warnings about icon imports are harmless
- Icons will display correctly in the browser
- If icons still don't show, check UnoCSS config

### Cannot login
- Verify `KOHAKU_HUB_ADMIN_ENABLED=true` in backend
- Check admin token matches backend configuration
- Check browser console for API errors

### API calls failing
- Check backend is running on port 48888
- Verify Vite proxy configuration
- Check browser network tab for request details
