"""Async wrappers for Peewee database operations.

All database operations should go through this module to ensure they run
in the dedicated single-worker DB thread pool.
"""

from typing import TypeVar

from kohakuhub.async_utils import run_in_db_executor
from kohakuhub.db import (
    Commit,
    EmailVerification,
    File,
    LFSObjectHistory,
    Organization,
    Repository,
    Session,
    SSHKey,
    StagingUpload,
    Token,
    User,
    UserOrganization,
    db,
)

T = TypeVar("T")


# User operations
async def get_user_by_id(user_id: int) -> User | None:
    """Get user by ID."""
    return await run_in_db_executor(lambda: User.get_or_none(User.id == user_id))


async def get_user_by_username(username: str) -> User | None:
    """Get user by username."""
    return await run_in_db_executor(lambda: User.get_or_none(User.username == username))


async def get_user_by_email(email: str) -> User | None:
    """Get user by email."""
    return await run_in_db_executor(lambda: User.get_or_none(User.email == email))


async def create_user(
    username: str,
    email: str,
    password_hash: str,
    email_verified: bool = False,
    is_active: bool = True,
    private_quota_bytes: int | None = None,
    public_quota_bytes: int | None = None,
) -> User:
    """Create a new user with default quota values if not specified."""
    from kohakuhub.config import cfg

    # Apply default quotas if not explicitly provided
    if private_quota_bytes is None:
        private_quota_bytes = cfg.quota.default_user_private_quota_bytes
    if public_quota_bytes is None:
        public_quota_bytes = cfg.quota.default_user_public_quota_bytes

    def _create():
        return User.create(
            username=username,
            email=email,
            password_hash=password_hash,
            email_verified=email_verified,
            is_active=is_active,
            private_quota_bytes=private_quota_bytes,
            public_quota_bytes=public_quota_bytes,
        )

    return await run_in_db_executor(_create)


async def update_user(user: User, **fields) -> None:
    """Update user fields."""

    def _update():
        for key, value in fields.items():
            setattr(user, key, value)
        user.save()

    await run_in_db_executor(_update)


async def delete_user(user: User) -> None:
    """Delete a user and all related data.

    This will delete:
    - All sessions
    - All tokens
    - All email verifications
    - All user-organization memberships
    - The user record itself

    Note: Repositories owned by the user are NOT deleted automatically.
    Delete repositories separately before calling this function.
    """

    def _delete():
        user_id = user.id

        # Delete all sessions
        Session.delete().where(Session.user_id == user_id).execute()

        # Delete all tokens
        Token.delete().where(Token.user_id == user_id).execute()

        # Delete all email verifications
        EmailVerification.delete().where(EmailVerification.user == user_id).execute()

        # Delete all user-organization memberships
        UserOrganization.delete().where(UserOrganization.user == user_id).execute()

        # Finally delete the user
        user.delete_instance()

    await run_in_db_executor(_delete)


# Session operations
async def get_session(session_id: str) -> Session | None:
    """Get session by session ID."""
    return await run_in_db_executor(
        lambda: Session.get_or_none(Session.session_id == session_id)
    )


async def create_session(
    session_id: str, user_id: int, secret: str, expires_at
) -> Session:
    """Create a new session."""

    def _create():
        return Session.create(
            session_id=session_id, user_id=user_id, secret=secret, expires_at=expires_at
        )

    return await run_in_db_executor(_create)


async def delete_session(session_id: str) -> None:
    """Delete a session."""

    def _delete():
        Session.delete().where(Session.session_id == session_id).execute()

    await run_in_db_executor(_delete)


# Token operations
async def get_token_by_hash(token_hash: str) -> Token | None:
    """Get token by hash."""
    return await run_in_db_executor(
        lambda: Token.get_or_none(Token.token_hash == token_hash)
    )


async def create_token(user_id: int, token_hash: str, name: str) -> Token:
    """Create a new API token."""

    def _create():
        return Token.create(user_id=user_id, token_hash=token_hash, name=name)

    return await run_in_db_executor(_create)


async def list_user_tokens(user_id: int) -> list[Token]:
    """List all tokens for a user."""

    def _list():
        return list(Token.select().where(Token.user_id == user_id))

    return await run_in_db_executor(_list)


async def delete_token(token_id: int) -> None:
    """Delete a token."""

    def _delete():
        Token.delete().where(Token.id == token_id).execute()

    await run_in_db_executor(_delete)


async def update_token_last_used(token: Token, last_used) -> None:
    """Update token last used timestamp."""

    def _update():
        token.last_used = last_used
        token.save()

    await run_in_db_executor(_update)


# Repository operations
async def get_repository(
    repo_type: str, namespace: str, name: str
) -> Repository | None:
    """Get repository by type, namespace, and name."""
    return await run_in_db_executor(
        lambda: Repository.get_or_none(
            Repository.repo_type == repo_type,
            Repository.namespace == namespace,
            Repository.name == name,
        )
    )


async def get_repository_by_full_id(full_id: str, repo_type: str) -> Repository | None:
    """Get repository by full ID and type."""
    return await run_in_db_executor(
        lambda: Repository.get_or_none(
            Repository.full_id == full_id, Repository.repo_type == repo_type
        )
    )


async def create_repository(
    repo_type: str,
    namespace: str,
    name: str,
    full_id: str,
    private: bool,
    owner_id: int,
) -> Repository:
    """Create a new repository."""

    def _create():
        return Repository.create(
            repo_type=repo_type,
            namespace=namespace,
            name=name,
            full_id=full_id,
            private=private,
            owner_id=owner_id,
        )

    return await run_in_db_executor(_create)


async def delete_repository(repo: Repository) -> None:
    """Delete a repository."""
    await run_in_db_executor(repo.delete_instance)


async def update_repository(repo: Repository, **fields) -> None:
    """Update repository fields."""

    def _update():
        for key, value in fields.items():
            setattr(repo, key, value)
        repo.save()

    await run_in_db_executor(_update)


async def list_repositories(
    repo_type: str | None = None, namespace: str | None = None, limit: int | None = None
) -> list[Repository]:
    """List repositories with optional filters."""

    def _list():
        query = Repository.select()
        if repo_type:
            query = query.where(Repository.repo_type == repo_type)
        if namespace:
            query = query.where(Repository.namespace == namespace)
        if limit:
            query = query.limit(limit)
        return list(query)

    return await run_in_db_executor(_list)


# File operations
async def get_file(repo_full_id: str, path_in_repo: str) -> File | None:
    """Get file by repository and path."""
    return await run_in_db_executor(
        lambda: File.get_or_none(
            File.repo_full_id == repo_full_id, File.path_in_repo == path_in_repo
        )
    )


async def get_file_by_sha256(sha256: str) -> File | None:
    """Get file by SHA256 hash."""
    return await run_in_db_executor(lambda: File.get_or_none(File.sha256 == sha256))


async def create_file(
    repo_full_id: str, path_in_repo: str, size: int, sha256: str, lfs: bool
) -> File:
    """Create a new file record."""

    def _create():
        return File.create(
            repo_full_id=repo_full_id,
            path_in_repo=path_in_repo,
            size=size,
            sha256=sha256,
            lfs=lfs,
        )

    return await run_in_db_executor(_create)


async def update_file(file: File, **fields) -> None:
    """Update file fields."""

    def _update():
        for key, value in fields.items():
            setattr(file, key, value)
        file.save()

    await run_in_db_executor(_update)


async def delete_file(file: File) -> None:
    """Delete a file record."""
    await run_in_db_executor(file.delete_instance)


# Organization operations
async def get_organization(name: str) -> Organization | None:
    """Get organization by name."""
    return await run_in_db_executor(
        lambda: Organization.get_or_none(Organization.name == name)
    )


async def create_organization(
    name: str,
    description: str | None = None,
    private_quota_bytes: int | None = None,
    public_quota_bytes: int | None = None,
) -> Organization:
    """Create a new organization with default quota values if not specified."""
    from kohakuhub.config import cfg

    # Apply default quotas if not explicitly provided
    if private_quota_bytes is None:
        private_quota_bytes = cfg.quota.default_org_private_quota_bytes
    if public_quota_bytes is None:
        public_quota_bytes = cfg.quota.default_org_public_quota_bytes

    def _create():
        return Organization.create(
            name=name,
            description=description,
            private_quota_bytes=private_quota_bytes,
            public_quota_bytes=public_quota_bytes,
        )

    return await run_in_db_executor(_create)


async def update_organization(org: Organization, **fields) -> None:
    """Update organization fields."""

    def _update():
        for key, value in fields.items():
            setattr(org, key, value)
        org.save()

    await run_in_db_executor(_update)


async def delete_organization(org: Organization) -> None:
    """Delete an organization."""
    await run_in_db_executor(org.delete_instance)


# UserOrganization operations
async def get_user_organization(user_id: int, org_id: int) -> UserOrganization | None:
    """Get user-organization relationship."""
    return await run_in_db_executor(
        lambda: UserOrganization.get_or_none(
            UserOrganization.user == user_id, UserOrganization.organization == org_id
        )
    )


async def create_user_organization(
    user_id: int, org_id: int, role: str
) -> UserOrganization:
    """Create user-organization relationship."""

    def _create():
        return UserOrganization.create(user=user_id, organization=org_id, role=role)

    return await run_in_db_executor(_create)


async def update_user_organization(user_org: UserOrganization, **fields) -> None:
    """Update user-organization fields."""

    def _update():
        for key, value in fields.items():
            setattr(user_org, key, value)
        user_org.save()

    await run_in_db_executor(_update)


async def delete_user_organization(user_org: UserOrganization) -> None:
    """Delete user-organization relationship."""
    await run_in_db_executor(user_org.delete_instance)


async def list_user_organizations(user_id: int) -> list[UserOrganization]:
    """List all organizations for a user."""

    def _list():
        return list(UserOrganization.select().where(UserOrganization.user == user_id))

    return await run_in_db_executor(_list)


async def list_organization_members(org_id: int) -> list[UserOrganization]:
    """List all members of an organization."""

    def _list():
        return list(
            UserOrganization.select().where(UserOrganization.organization == org_id)
        )

    return await run_in_db_executor(_list)


# StagingUpload operations
async def create_staging_upload(
    repo_full_id: str,
    repo_type: str,
    revision: str,
    path_in_repo: str,
    sha256: str,
    size: int,
    storage_key: str,
    lfs: bool,
    upload_id: str | None = None,
) -> StagingUpload:
    """Create a staging upload record."""

    def _create():
        return StagingUpload.create(
            repo_full_id=repo_full_id,
            repo_type=repo_type,
            revision=revision,
            path_in_repo=path_in_repo,
            sha256=sha256,
            size=size,
            storage_key=storage_key,
            lfs=lfs,
            upload_id=upload_id,
        )

    return await run_in_db_executor(_create)


async def delete_staging_upload(staging: StagingUpload) -> None:
    """Delete a staging upload record."""
    await run_in_db_executor(staging.delete_instance)


# LFSObjectHistory operations
async def create_lfs_history(
    repo_full_id: str, path_in_repo: str, sha256: str, size: int, commit_id: str
) -> LFSObjectHistory:
    """Create LFS object history record."""

    def _create():
        return LFSObjectHistory.create(
            repo_full_id=repo_full_id,
            path_in_repo=path_in_repo,
            sha256=sha256,
            size=size,
            commit_id=commit_id,
        )

    return await run_in_db_executor(_create)


async def list_lfs_history(
    repo_full_id: str, path_in_repo: str, limit: int | None = None
) -> list[LFSObjectHistory]:
    """List LFS history for a file."""

    def _list():
        query = (
            LFSObjectHistory.select()
            .where(
                LFSObjectHistory.repo_full_id == repo_full_id,
                LFSObjectHistory.path_in_repo == path_in_repo,
            )
            .order_by(LFSObjectHistory.created_at.desc())
        )
        if limit:
            query = query.limit(limit)
        return list(query)

    return await run_in_db_executor(_list)


# Commit operations
async def create_commit(
    commit_id: str,
    repo_full_id: str,
    repo_type: str,
    branch: str,
    user_id: int,
    username: str,
    message: str,
    description: str = "",
) -> Commit:
    """Create commit record."""
    from kohakuhub.db import Commit

    def _create():
        return Commit.create(
            commit_id=commit_id,
            repo_full_id=repo_full_id,
            repo_type=repo_type,
            branch=branch,
            user_id=user_id,
            username=username,
            message=message,
            description=description,
        )

    return await run_in_db_executor(_create)


async def get_commit(commit_id: str, repo_full_id: str) -> Commit | None:
    """Get commit by ID and repo."""
    from kohakuhub.db import Commit

    return await run_in_db_executor(
        lambda: Commit.get_or_none(
            Commit.commit_id == commit_id, Commit.repo_full_id == repo_full_id
        )
    )


async def list_commits_by_repo(
    repo_full_id: str, branch: str | None = None, limit: int | None = None
) -> list[Commit]:
    """List commits for a repository."""
    from kohakuhub.db import Commit

    def _list():
        query = Commit.select().where(Commit.repo_full_id == repo_full_id)
        if branch:
            query = query.where(Commit.branch == branch)
        query = query.order_by(Commit.created_at.desc())
        if limit:
            query = query.limit(limit)
        return list(query)

    return await run_in_db_executor(_list)


# EmailVerification operations
async def create_email_verification(
    user_id: int, token: str, expires_at
) -> EmailVerification:
    """Create email verification token."""

    def _create():
        return EmailVerification.create(
            user=user_id, token=token, expires_at=expires_at
        )

    return await run_in_db_executor(_create)


async def get_email_verification(token: str) -> EmailVerification | None:
    """Get email verification by token."""
    return await run_in_db_executor(
        lambda: EmailVerification.get_or_none(EmailVerification.token == token)
    )


async def delete_email_verification(verification: EmailVerification) -> None:
    """Delete email verification."""
    await run_in_db_executor(verification.delete_instance)


# SSH Key operations
async def get_ssh_key_by_id(key_id: int) -> SSHKey | None:
    """Get SSH key by ID."""
    return await run_in_db_executor(lambda: SSHKey.get_or_none(SSHKey.id == key_id))


async def get_ssh_key_by_fingerprint(fingerprint: str) -> SSHKey | None:
    """Get SSH key by fingerprint."""
    return await run_in_db_executor(
        lambda: SSHKey.get_or_none(SSHKey.fingerprint == fingerprint)
    )


async def list_user_ssh_keys(user_id: int) -> list[SSHKey]:
    """List all SSH keys for a user."""

    def _list():
        return list(
            SSHKey.select()
            .where(SSHKey.user_id == user_id)
            .order_by(SSHKey.created_at.desc())
        )

    return await run_in_db_executor(_list)


async def create_ssh_key(
    user_id: int, key_type: str, public_key: str, fingerprint: str, title: str
) -> SSHKey:
    """Create a new SSH key."""

    def _create():
        return SSHKey.create(
            user_id=user_id,
            key_type=key_type,
            public_key=public_key,
            fingerprint=fingerprint,
            title=title,
        )

    return await run_in_db_executor(_create)


async def update_ssh_key(key: SSHKey, **fields) -> None:
    """Update SSH key fields."""

    def _update():
        for field_name, value in fields.items():
            setattr(key, field_name, value)
        key.save()

    await run_in_db_executor(_update)


async def delete_ssh_key(key: SSHKey) -> None:
    """Delete an SSH key."""
    await run_in_db_executor(key.delete_instance)


# Generic query helper
async def execute_db_query(query_func):
    """Execute a custom database query in the DB executor.

    Args:
        query_func: A callable that performs the database operation

    Returns:
        Result of the query function
    """
    return await run_in_db_executor(query_func)
