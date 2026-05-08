---
title: Docker Deployment
description: Complete Docker Compose setup guide for KohakuHub.
icon: i-carbon-container-services
---

# Docker Deployment

This guide provides a complete walkthrough for deploying KohakuHub using Docker Compose.

## Quick Deploy (Recommended)

This is the easiest and fastest way to get KohakuHub running.

### 1. Clone the Repository

```bash
git clone https://github.com/KohakuBlueleaf/KohakuHub.git
cd KohakuHub
```

### 2. Configure and Deploy

First, run the interactive script to generate your `docker-compose.yml` file. This will guide you through setting up the database, storage, and other services.

```bash
python scripts/generate_docker_compose.py
```

Then, run the deployment script. This will automatically build the frontend applications and start all the Docker services.

```bash
python scripts/deploy.py
```

That's it! The application is now running.

## Step-by-Step Setup

If you need more control over the setup process, you can follow these manual steps.

### 1. Clone the Repository

```bash
git clone https://github.com/KohakuBlueleaf/KohakuHub.git
cd KohakuHub
```

### 2. Generate `docker-compose.yml`

Copy the example file and manually edit it to fit your environment.

```bash
cp docker-compose.example.yml docker-compose.yml
```

### 3. Build the Frontend

Before starting the services, you need to build the frontend applications:

```bash
pnpm install
pnpm run build
```

### 4. Start the Services

To start all services in detached mode, run:

```bash
docker-compose up -d --build
```

## Security Configuration

It is **critical** to change the default secrets before deploying to production.

### Generate Secret Keys

You can generate secure random strings for your secrets using the following commands:

```bash
# Generate a 64-character random string for session and admin tokens
python scripts/generate_secret.py 64

# Or use openssl
openssl rand -base64 48
```

Update the following variables in your `docker-compose.yml` with the generated secrets:

- `KOHAKU_HUB_SESSION_SECRET`
- `KOHAKU_HUB_ADMIN_SECRET_TOKEN`
- `LAKEFS_AUTH_ENCRYPT_SECRET_KEY`

## Services

The Docker Compose setup includes the following services:

- **hub-ui**: Nginx server for the frontend application (port `28080`).
- **hub-api**: The main FastAPI backend (port `48888`).
- **postgres**: PostgreSQL database for metadata (port `5432`).
- **lakefs**: LakeFS for data versioning (port `28000`). Pinned to
  `treeverse/lakefs:latest`; **minimum supported LakeFS is v0.54.0**
  (2021-11-08) because the file-list `expand=true` path uses
  `logCommits`'s `objects=` / `prefixes=` / `limit=` filters introduced
  in that release.
- **minio**: MinIO for S3-compatible object storage (ports `29000` and `29001`).

## Managing the Application

### View Logs

To view the logs for all services, use:

```bash
docker-compose logs -f
```

To view the logs for a specific service, use:

```bash
docker-compose logs -f hub-api
```

### Stop the Services

To stop all running services, use:

```bash
docker-compose down
```

## Accessing the Application

- **Web UI**: `http://localhost:28080`
- **Admin Portal**: `http://localhost:28080/admin`
- **API Docs**: `http://localhost:48888/docs`
- **LakeFS UI**: `http://localhost:28000`
- **MinIO Console**: `http://localhost:29000`
