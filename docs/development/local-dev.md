# Local Development

This setup runs the KohakuHub backend locally in your Python virtualenv, while Docker provides the supporting services:

- PostgreSQL for application metadata
- MinIO for local S3-compatible storage
- LakeFS for repository versioning
- Vite dev servers for the main UI and admin UI

It does not require `docker compose`. The scripts below use plain `docker`.

## Prerequisites

- Docker Engine
- Python 3.10+
- Node.js 18+
- An existing virtualenv for backend work

## One-Time Setup

If you prefer a single command surface, run `make help` from the repo root to see the shortcuts below.

### 1. Backend dependencies

```bash
./venv/bin/pip install -e ".[dev]"
```

If you already activated the virtualenv:

```bash
pip install -e ".[dev]"
```

### 2. Frontend dependencies

```bash
npm install --prefix src/kohaku-hub-ui
npm install --prefix src/kohaku-hub-admin
```

### 3. Create your local env file

```bash
cp .env.dev.example .env.dev
```

Or:

```bash
make init-env
```

The defaults are already wired to the local Docker services and Vite dev servers.

## Start Local Infra

```bash
./scripts/dev/up_infra.sh
```

Or:

```bash
make infra-up
```

This starts:

- Postgres on `127.0.0.1:25432`
- MinIO API on `127.0.0.1:29001`
- MinIO console on `127.0.0.1:29000`
- LakeFS on `127.0.0.1:28000`

Persistent dev data is stored under `hub-meta/dev/`.

## Start The Backend

```bash
./scripts/dev/run_backend.sh
```

Or:

```bash
make backend
```

What this script does:

- loads `.env.dev`
- initializes LakeFS on first run
- writes LakeFS credentials to `hub-meta/dev/lakefs/credentials.env`
- runs database migrations
- auto-seeds fixed demo users/orgs/repos on a fresh local environment when `KOHAKU_HUB_DEV_AUTO_SEED=true`
- starts `uvicorn` with `--reload` on `127.0.0.1:48888`

Swagger docs will be available at `http://127.0.0.1:48888/docs`.

If you want the migrations + demo seed without holding the terminal open for `uvicorn`, run:

```bash
make seed-demo
```

This writes a local manifest to `hub-meta/dev/demo-seed-manifest.json`.

## Start The Frontends

Main UI:

```bash
npm run dev --prefix src/kohaku-hub-ui
```

Or:

```bash
make ui
```

Admin UI:

```bash
npm run dev --prefix src/kohaku-hub-admin
```

Or:

```bash
make admin
```

Access:

- Main UI: `http://127.0.0.1:5173`
- Admin UI: `http://127.0.0.1:5174`

The Vite configs already proxy API traffic to the backend at `127.0.0.1:48888`.

## Why `KOHAKU_HUB_INTERNAL_BASE_URL` Exists

For local development, the backend should generate public links that point to the main UI dev server (`5173`), but its own internal follow-up requests should still hit the backend directly (`48888`).

Set in `.env.dev`:

```bash
KOHAKU_HUB_BASE_URL=http://127.0.0.1:5173
KOHAKU_HUB_INTERNAL_BASE_URL=http://127.0.0.1:48888
```

This keeps:

- browser-facing links on the frontend dev server
- backend self-calls off the Vite proxy path

## First Login / Admin

Main UI seeded account:

- Username: `mai_lin`
- Password: `KohakuDev123!`

Additional seeded users use the same password:

- `leo_park`
- `sara_chen`
- `noah_kim`
- `ivy_ops`

The seeded data also includes fixed organizations and repositories, including public/private repos, model/dataset/space types, branches, tags, likes, LFS files, and dataset preview files.

Admin UI login does not use a username/password. Open `http://127.0.0.1:5174` and use the token from `.env.dev`.

Default local token:

```bash
KOHAKU_HUB_ADMIN_SECRET_TOKEN=dev-admin-token-change-me
```

## Common Commands

Restart infra:

```bash
./scripts/dev/down_infra.sh
./scripts/dev/up_infra.sh
```

Or:

```bash
make infra-down
make infra-up
```

Stop infra only:

```bash
./scripts/dev/down_infra.sh
```

Or:

```bash
make infra-down
```

Tail a container log:

```bash
docker logs -f kohakuhub-dev-lakefs
docker logs -f kohakuhub-dev-minio
docker logs -f kohakuhub-dev-postgres
```

## Reset Local Data

`make reset-local-data` is intentionally destructive. The script prints a bold red warning, explains the consequences, and requires typing the same confirmation phrase twice before it removes `hub-meta/dev/`.

If you want a clean local reset followed by fresh demo data bootstrapping:

```bash
make reset-and-seed
```

That command still goes through the same double-confirmation prompt before anything is deleted.

## Troubleshooting

### Docker service ports are already taken

Adjust the port mappings inside [`scripts/dev/up_infra.sh`](/home/zhangshaoang/wtf-projects/KohakuHub/scripts/dev/up_infra.sh) and keep `.env.dev` in sync.

### LakeFS says it is already initialized but credentials are missing

The bootstrap credentials are only returned once. If `hub-meta/dev/lakefs-data/` still exists but `hub-meta/dev/lakefs/credentials.env` was removed, either:

- restore the credentials file, or
- delete `hub-meta/dev/lakefs-data/` and initialize again

### Backend cannot connect to Postgres

Check:

```bash
docker logs kohakuhub-dev-postgres
cat .env.dev
```

Make sure `KOHAKU_HUB_DATABASE_URL` matches `DEV_POSTGRES_*`.
