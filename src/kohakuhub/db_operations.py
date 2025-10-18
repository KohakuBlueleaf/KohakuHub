"""Synchronous database operations with transaction support.

All database operations are now synchronous. Use db.atomic() for transactions.
Multi-worker deployment is safe with proper transaction usage.

REFACTORED: Organization model merged into User (is_org flag).
All operations now use proper ForeignKey relationships with backref for convenience.
"""

import json
from datetime import datetime, timezone

from kohakuhub.config import cfg
from kohakuhub.db import (
    Commit,
    DailyRepoStats,
    DownloadSession,
    EmailVerification,
    File,
    Invitation,
    LFSObjectHistory,
    Repository,
    RepositoryLike,
    Session,
    SSHKey,
    StagingUpload,
    Token,
    User,
    UserOrganization,
    db,
)
from kohakuhub.utils.names import normalize_name


# ===== User operations =====


def get_user_by_id(user_id: int) -> User | None:
    """Get user by ID (works for both users and orgs)."""
    return User.get_or_none(User.id == user_id)


def get_user_by_username(username: str) -> User | None:
    """Get user by username (works for both users and orgs)."""
    return User.get_or_none(User.username == username)


def get_user_by_email(email: str) -> User | None:
    """Get user by email (only for regular users, orgs have no email)."""
    return User.get_or_none((User.email == email) & (User.is_org == False))


def get_user_by_email_excluding_id(email: str, exclude_user_id: int) -> User | None:
    """Get user by email, excluding a specific user ID (for email uniqueness checks)."""
    return User.get_or_none(
        (User.email == email) & (User.id != exclude_user_id) & (User.is_org == False)
    )


def create_user(
    username: str,
    email: str,
    password_hash: str,
    email_verified: bool = False,
    is_active: bool = True,
    private_quota_bytes: int | None = None,
    public_quota_bytes: int | None = None,
) -> User:
    """Create a new regular user with default quota values if not specified.

    NOTE: Wrap in db.atomic() if checking existence first to prevent race conditions.
    """
    # Apply default quotas if not explicitly provided
    if private_quota_bytes is None:
        private_quota_bytes = cfg.quota.default_user_private_quota_bytes
    if public_quota_bytes is None:
        public_quota_bytes = cfg.quota.default_user_public_quota_bytes

    return User.create(
        username=username,
        normalized_name=normalize_name(username),
        is_org=False,
        email=email,
        password_hash=password_hash,
        email_verified=email_verified,
        is_active=is_active,
        private_quota_bytes=private_quota_bytes,
        public_quota_bytes=public_quota_bytes,
    )


def update_user(user: User, **fields) -> None:
    """Update user fields."""
    for key, value in fields.items():
        setattr(user, key, value)
    user.save()


def delete_user(user: User) -> None:
    """Delete a user and all related data.

    ForeignKey CASCADE will automatically delete:
    - All sessions (Session.user)
    - All tokens (Token.user)
    - All SSH keys (SSHKey.user)
    - All email verifications (EmailVerification.user)
    - All user-organization memberships (UserOrganization.user)
    - All created invitations (Invitation.created_by)
    - All owned repositories (Repository.owner)
    - All authored commits (Commit.author)

    NOTE: CASCADE deletes handled by database constraints.
    """
    user.delete_instance()


# ===== Organization operations (now uses User with is_org=True) =====


def get_organization(name: str) -> User | None:
    """Get organization by name (User with is_org=TRUE)."""
    return User.get_or_none((User.username == name) & (User.is_org == True))


def create_organization(
    name: str,
    description: str | None = None,
    private_quota_bytes: int | None = None,
    public_quota_bytes: int | None = None,
) -> User:
    """Create a new organization (User with is_org=TRUE) with default quota values if not specified."""
    if private_quota_bytes is None:
        private_quota_bytes = cfg.quota.default_org_private_quota_bytes
    if public_quota_bytes is None:
        public_quota_bytes = cfg.quota.default_org_public_quota_bytes

    return User.create(
        username=name,
        normalized_name=normalize_name(name),
        is_org=True,
        email=None,  # Organizations don't have email
        password_hash=None,  # Organizations don't have password
        description=description,
        email_verified=False,
        is_active=True,
        private_quota_bytes=private_quota_bytes,
        public_quota_bytes=public_quota_bytes,
    )


def update_organization(org: User, **fields) -> None:
    """Update organization fields (User with is_org=TRUE)."""
    for key, value in fields.items():
        setattr(org, key, value)
    org.save()


def delete_organization(org: User) -> None:
    """Delete an organization (User with is_org=TRUE).

    CASCADE will delete:
    - All owned repositories
    - All organization memberships
    """
    org.delete_instance()


# ===== Repository operations =====


def get_repository(repo_type: str, namespace: str, name: str) -> Repository | None:
    """Get repository by type, namespace, and name."""
    return Repository.get_or_none(
        Repository.repo_type == repo_type,
        Repository.namespace == namespace,
        Repository.name == name,
    )


def get_repository_by_full_id(full_id: str, repo_type: str) -> Repository | None:
    """Get repository by full ID and type."""
    return Repository.get_or_none(
        Repository.full_id == full_id, Repository.repo_type == repo_type
    )


def create_repository(
    repo_type: str,
    namespace: str,
    name: str,
    full_id: str,
    private: bool,
    owner: User,
) -> Repository:
    """Create a new repository with ForeignKey to owner (User or Org).

    NOTE: Wrap in db.atomic() if checking existence first.
    """
    return Repository.create(
        repo_type=repo_type,
        namespace=namespace,
        name=name,
        full_id=full_id,
        private=private,
        owner=owner,  # ForeignKey to User (can be user or org)
    )


def delete_repository(repo: Repository) -> None:
    """Delete a repository.

    CASCADE will delete:
    - All files (File.repository)
    - All commits (Commit.repository)
    - All staging uploads (StagingUpload.repository)
    - All LFS history (LFSObjectHistory.repository)
    """
    repo.delete_instance()


def update_repository(repo: Repository, **fields) -> None:
    """Update repository fields."""
    for key, value in fields.items():
        setattr(repo, key, value)
    repo.save()


def list_repositories(
    repo_type: str | None = None, namespace: str | None = None, limit: int | None = None
) -> list[Repository]:
    """List repositories with optional filters."""
    query = Repository.select()
    if repo_type:
        query = query.where(Repository.repo_type == repo_type)
    if namespace:
        query = query.where(Repository.namespace == namespace)
    if limit:
        query = query.limit(limit)
    return list(query)


# ===== Session operations =====


def get_session(session_id: str) -> Session | None:
    """Get session by session ID."""
    return Session.get_or_none(Session.session_id == session_id)


def create_session(session_id: str, user: User, secret: str, expires_at) -> Session:
    """Create a new session with ForeignKey to user."""
    return Session.create(
        session_id=session_id, user=user, secret=secret, expires_at=expires_at
    )


def delete_session(session_id: str) -> None:
    """Delete a session."""
    Session.delete().where(Session.session_id == session_id).execute()


# ===== Token operations =====


def get_token_by_hash(token_hash: str) -> Token | None:
    """Get token by hash."""
    return Token.get_or_none(Token.token_hash == token_hash)


def create_token(user: User, token_hash: str, name: str) -> Token:
    """Create a new API token with ForeignKey to user."""
    return Token.create(user=user, token_hash=token_hash, name=name)


def list_user_tokens(user: User) -> list[Token]:
    """List all tokens for a user using backref."""
    return list(user.tokens)  # Use backref from User


def delete_token(token_id: int) -> None:
    """Delete a token."""
    Token.delete().where(Token.id == token_id).execute()


def update_token_last_used(token: Token, last_used) -> None:
    """Update token last used timestamp."""
    token.last_used = last_used
    token.save()


# ===== UserOrganization operations =====


def get_user_organization(user: User, org: User) -> UserOrganization | None:
    """Get user-organization relationship using ForeignKeys."""
    return UserOrganization.get_or_none(
        (UserOrganization.user == user) & (UserOrganization.organization == org)
    )


def create_user_organization(user: User, org: User, role: str) -> UserOrganization:
    """Create user-organization relationship with ForeignKeys."""
    return UserOrganization.create(user=user, organization=org, role=role)


def update_user_organization(user_org: UserOrganization, **fields) -> None:
    """Update user-organization fields."""
    for key, value in fields.items():
        setattr(user_org, key, value)
    user_org.save()


def delete_user_organization(user_org: UserOrganization) -> None:
    """Delete user-organization relationship."""
    user_org.delete_instance()


def list_user_organizations(user: User) -> list[UserOrganization]:
    """List all organizations for a user using backref."""
    return list(user.org_memberships)  # Use backref from User


def list_organization_members(org: User) -> list[UserOrganization]:
    """List all members of an organization using backref."""
    return list(org.members)  # Use backref from User (organization side)


# ===== File operations =====


def get_file(repo: Repository, path_in_repo: str) -> File | None:
    """Get file by repository FK and path (only active files)."""
    return File.get_or_none(
        (File.repository == repo)
        & (File.path_in_repo == path_in_repo)
        & (File.is_deleted == False)
    )


def get_file_by_sha256(sha256: str) -> File | None:
    """Get file by SHA256 hash (only active files)."""
    return File.get_or_none((File.sha256 == sha256) & (File.is_deleted == False))


def create_file(
    repository: Repository,
    path_in_repo: str,
    size: int,
    sha256: str,
    lfs: bool,
    owner: User,
) -> File:
    """Create a new file record with ForeignKeys to repository and owner."""
    return File.create(
        repository=repository,
        path_in_repo=path_in_repo,
        size=size,
        sha256=sha256,
        lfs=lfs,
        is_deleted=False,
        owner=owner,  # Denormalized from repository.owner for convenience
    )


def update_file(file: File, **fields) -> None:
    """Update file fields."""
    for key, value in fields.items():
        setattr(file, key, value)
    file.save()


def delete_file(file: File) -> None:
    """Delete a file record."""
    file.delete_instance()


# ===== Commit operations =====


def create_commit(
    commit_id: str,
    repository: Repository,
    repo_type: str,
    branch: str,
    author: User,
    username: str,
    message: str,
    description: str = "",
) -> Commit:
    """Create commit record with ForeignKeys to repository, author, and owner.

    author = User who made the commit
    owner = Repository owner (denormalized from repository.owner)
    """
    return Commit.create(
        commit_id=commit_id,
        repository=repository,
        repo_type=repo_type,
        branch=branch,
        author=author,
        owner=repository.owner,  # Denormalized for convenience
        username=username,
        message=message,
        description=description,
    )


def get_commit(commit_id: str, repository: Repository) -> Commit | None:
    """Get commit by ID and repository FK."""
    return Commit.get_or_none(
        (Commit.commit_id == commit_id) & (Commit.repository == repository)
    )


def list_commits_by_repo(
    repository: Repository, branch: str | None = None, limit: int | None = None
) -> list[Commit]:
    """List commits for a repository using backref."""
    query = repository.commits.select()  # Use backref
    if branch:
        query = query.where(Commit.branch == branch)
    query = query.order_by(Commit.created_at.desc())
    if limit:
        query = query.limit(limit)
    return list(query)


# ===== SSH Key operations =====


def get_ssh_key_by_id(key_id: int) -> SSHKey | None:
    """Get SSH key by ID."""
    return SSHKey.get_or_none(SSHKey.id == key_id)


def get_ssh_key_by_fingerprint(fingerprint: str) -> SSHKey | None:
    """Get SSH key by fingerprint."""
    return SSHKey.get_or_none(SSHKey.fingerprint == fingerprint)


def list_user_ssh_keys(user: User) -> list[SSHKey]:
    """List all SSH keys for a user using backref."""
    return list(user.ssh_keys.order_by(SSHKey.created_at.desc()))  # Use backref


def create_ssh_key(
    user: User, key_type: str, public_key: str, fingerprint: str, title: str
) -> SSHKey:
    """Create a new SSH key with ForeignKey to user."""
    return SSHKey.create(
        user=user,
        key_type=key_type,
        public_key=public_key,
        fingerprint=fingerprint,
        title=title,
    )


def update_ssh_key(key: SSHKey, **fields) -> None:
    """Update SSH key fields."""
    for field_name, value in fields.items():
        setattr(key, field_name, value)
    key.save()


def delete_ssh_key(key: SSHKey) -> None:
    """Delete an SSH key."""
    key.delete_instance()


# ===== LFS operations =====


def create_lfs_history(
    repository: Repository,
    path_in_repo: str,
    sha256: str,
    size: int,
    commit_id: str,
    file: File | None = None,
) -> LFSObjectHistory:
    """Create LFS object history record with ForeignKeys."""
    from kohakuhub.logger import get_logger

    logger = get_logger("DB")

    entry = LFSObjectHistory.create(
        repository=repository,
        path_in_repo=path_in_repo,
        sha256=sha256,
        size=size,
        commit_id=commit_id,
        file=file,  # Optional FK to File for faster lookups
    )

    logger.success(
        f"[LFS_HISTORY_CREATE] repo={repository.full_id}, "
        f"path={path_in_repo}, sha256={sha256[:8]}, size={size:,}, "
        f"commit={commit_id[:8]}, id={entry.id}"
    )

    return entry


def list_lfs_history(
    repository: Repository, path_in_repo: str, limit: int | None = None
) -> list[LFSObjectHistory]:
    """List LFS history for a file using repository FK."""
    query = (
        LFSObjectHistory.select()
        .where(
            (LFSObjectHistory.repository == repository)
            & (LFSObjectHistory.path_in_repo == path_in_repo)
        )
        .order_by(LFSObjectHistory.created_at.desc())
    )
    if limit:
        query = query.limit(limit)
    return list(query)


def get_lfs_history_entry(
    repository: Repository,
    path_in_repo: str,
    sha256: str,
) -> LFSObjectHistory | None:
    """Get existing LFS history entry for repo/path/sha256.

    Args:
        repository: Repository FK object
        path_in_repo: File path
        sha256: LFS object SHA256 hash

    Returns:
        LFSObjectHistory instance or None
    """
    return LFSObjectHistory.get_or_none(
        (LFSObjectHistory.repository == repository)
        & (LFSObjectHistory.path_in_repo == path_in_repo)
        & (LFSObjectHistory.sha256 == sha256)
    )


def refresh_lfs_history_timestamp(
    lfs_entry: LFSObjectHistory,
    commit_id: str,
) -> None:
    """Refresh LFS history timestamp and update commit_id.

    Args:
        lfs_entry: LFSObjectHistory instance to update
        commit_id: New commit ID
    """
    lfs_entry.commit_id = commit_id
    lfs_entry.created_at = datetime.now(timezone.utc)
    lfs_entry.save()


def get_effective_lfs_threshold(repo: Repository) -> int:
    """Get effective LFS threshold for a repository.

    Returns repository-specific threshold or server default if not set.

    Args:
        repo: Repository object

    Returns:
        LFS threshold in bytes
    """
    if repo.lfs_threshold_bytes is not None:
        return repo.lfs_threshold_bytes
    return cfg.app.lfs_threshold_bytes


def get_effective_lfs_keep_versions(repo: Repository) -> int:
    """Get effective LFS keep versions for a repository.

    Returns repository-specific keep versions or server default if not set.

    Args:
        repo: Repository object

    Returns:
        Number of LFS versions to keep
    """
    if repo.lfs_keep_versions is not None:
        return repo.lfs_keep_versions
    return cfg.app.lfs_keep_versions


def get_effective_lfs_suffix_rules(repo: Repository) -> list[str]:
    """Get effective LFS suffix rules for a repository.

    Returns MERGED list of:
    1. Server-wide default suffix rules (from config)
    2. Repository-specific suffix rules (from database)

    This allows repositories to ADD additional suffixes on top of server defaults.
    Server defaults always apply unless explicitly removed at repository level.

    Args:
        repo: Repository object

    Returns:
        List of file suffixes that should always use LFS (e.g., [".safetensors", ".bin"])
        Duplicates are removed, order is preserved (server defaults first)
    """
    # Start with server-wide defaults
    effective_rules = list(cfg.app.lfs_suffix_rules_default)

    # Add repository-specific rules if configured
    if repo.lfs_suffix_rules:
        try:
            repo_rules = json.loads(repo.lfs_suffix_rules)
            if isinstance(repo_rules, list):
                # Merge: Add repo rules that aren't already in server defaults
                for rule in repo_rules:
                    if rule not in effective_rules:
                        effective_rules.append(rule)
        except json.JSONDecodeError:
            pass

    return effective_rules


def should_use_lfs(repo: Repository, file_path: str, file_size: int) -> bool:
    """Determine if a file should use LFS based on size AND/OR suffix rules.

    A file will use LFS if EITHER:
    1. File size >= threshold
    2. File suffix matches one of the configured suffix rules

    Args:
        repo: Repository object
        file_path: File path (used to check suffix)
        file_size: File size in bytes

    Returns:
        True if file should use LFS, False otherwise
    """
    # Check size threshold
    threshold = get_effective_lfs_threshold(repo)
    if file_size >= threshold:
        return True

    # Check suffix rules
    suffix_rules = get_effective_lfs_suffix_rules(repo)
    if suffix_rules:
        file_lower = file_path.lower()
        for suffix in suffix_rules:
            if file_lower.endswith(suffix.lower()):
                return True

    return False


# ===== Email verification operations =====


def create_email_verification(user: User, token: str, expires_at) -> EmailVerification:
    """Create email verification token with ForeignKey to user."""
    return EmailVerification.create(user=user, token=token, expires_at=expires_at)


def get_email_verification(token: str) -> EmailVerification | None:
    """Get email verification by token."""
    return EmailVerification.get_or_none(EmailVerification.token == token)


def delete_email_verification(verification: EmailVerification) -> None:
    """Delete email verification."""
    verification.delete_instance()


# ===== Staging upload operations =====


def create_staging_upload(
    repository: Repository,
    repo_type: str,
    revision: str,
    path_in_repo: str,
    sha256: str,
    size: int,
    storage_key: str,
    lfs: bool,
    upload_id: str | None = None,
    uploader: User | None = None,
) -> StagingUpload:
    """Create a staging upload record with ForeignKeys."""
    return StagingUpload.create(
        repository=repository,
        repo_type=repo_type,
        revision=revision,
        path_in_repo=path_in_repo,
        sha256=sha256,
        size=size,
        storage_key=storage_key,
        lfs=lfs,
        upload_id=upload_id,
        uploader=uploader,
    )


def delete_staging_upload(staging: StagingUpload) -> None:
    """Delete a staging upload record."""
    staging.delete_instance()


# ===== Invitation operations =====


def create_invitation(
    token: str,
    action: str,
    parameters: str,
    created_by: User | None,
    expires_at,
    max_usage: int | None = None,
) -> Invitation:
    """Create a new invitation with ForeignKey to creator.

    Args:
        token: Unique invitation token (UUID)
        action: Action type (e.g., "join_org", "register_account")
        parameters: JSON string with action-specific data
        created_by: User who created the invitation (ForeignKey), or None for system/admin-generated
        expires_at: Expiration datetime
        max_usage: Maximum usage count (None=one-time, -1=unlimited, N=max N uses)

    Returns:
        Created invitation

    Note: Wrap in db.atomic() if checking existence first.
    """
    return Invitation.create(
        token=token,
        action=action,
        parameters=parameters,
        created_by=created_by,  # Can be None for system/admin invitations
        expires_at=expires_at,
        max_usage=max_usage,
        usage_count=0,
    )


def get_invitation(token: str) -> Invitation | None:
    """Get invitation by token."""
    return Invitation.get_or_none(Invitation.token == token)


def get_invitation_by_id(invitation_id: int) -> Invitation | None:
    """Get invitation by ID."""
    return Invitation.get_or_none(Invitation.id == invitation_id)


def mark_invitation_used(invitation: Invitation, used_by: User) -> None:
    """Mark invitation as used by a user (ForeignKey).

    For multi-use invitations, this increments usage_count.
    For single-use invitations, it also sets used_at and used_by.
    """
    # Increment usage count
    invitation.usage_count += 1

    # For first use, set used_at and used_by (legacy fields)
    if invitation.used_at is None:
        invitation.used_at = datetime.now(timezone.utc)
        invitation.used_by = used_by  # ForeignKey

    invitation.save()


def check_invitation_available(invitation: Invitation) -> tuple[bool, str | None]:
    """Check if invitation can still be used.

    Args:
        invitation: Invitation to check

    Returns:
        Tuple of (is_available, error_message)
    """
    # Check expiration
    expires_at = invitation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at <= datetime.now(timezone.utc):
        return False, "Invitation has expired"

    # Check usage limit
    if invitation.max_usage is None:
        # One-time use (legacy)
        if invitation.used_at is not None:
            return False, "Invitation has already been used"
    elif invitation.max_usage == -1:
        # Unlimited use
        return True, None
    else:
        # Limited use (check count)
        if invitation.usage_count >= invitation.max_usage:
            return (
                False,
                f"Invitation has reached maximum usage limit ({invitation.max_usage})",
            )

    return True, None


def delete_invitation(invitation: Invitation) -> None:
    """Delete an invitation."""
    invitation.delete_instance()


def list_org_invitations(org: User) -> list[Invitation]:
    """List all invitations for an organization.

    Args:
        org: Organization (User with is_org=TRUE)

    Returns:
        List of invitations (both pending and used)
    """
    # Get all invitations with action "join_org" and matching org_id in parameters
    invitations = []
    all_invites = Invitation.select().where(Invitation.action == "join_org")

    for invite in all_invites:
        try:
            params = json.loads(invite.parameters)
            if params.get("org_id") == org.id:
                invitations.append(invite)
        except (json.JSONDecodeError, KeyError):
            continue

    return invitations


def delete_expired_invitations() -> int:
    """Delete all expired invitations.

    Returns:
        Number of invitations deleted
    """
    now = datetime.now(timezone.utc)
    deleted = (
        Invitation.delete()
        .where(Invitation.expires_at < now, Invitation.used_at.is_null())
        .execute()
    )

    return deleted


# ===== Likes operations =====


def create_repository_like(repository: Repository, user: User):
    """Create a like for a repository.

    NOTE: Wrap in db.atomic() to prevent race conditions with counter increment.

    Args:
        repository: Repository to like
        user: User who likes

    Returns:
        RepositoryLike instance

    Raises:
        IntegrityError: If user already liked this repository
    """
    return RepositoryLike.create(repository=repository, user=user)


def delete_repository_like(repository: Repository, user: User) -> int:
    """Delete a like for a repository.

    Args:
        repository: Repository to unlike
        user: User who unlikes

    Returns:
        Number of likes deleted (0 or 1)
    """
    return (
        RepositoryLike.delete()
        .where(
            (RepositoryLike.repository == repository) & (RepositoryLike.user == user)
        )
        .execute()
    )


def get_repository_like(repository: Repository, user: User):
    """Get like record for a repository and user.

    Args:
        repository: Repository
        user: User

    Returns:
        RepositoryLike instance or None
    """
    return RepositoryLike.get_or_none(
        (RepositoryLike.repository == repository) & (RepositoryLike.user == user)
    )


def list_repository_likers(repository: Repository, limit: int = 50):
    """List users who liked a repository.

    Args:
        repository: Repository
        limit: Maximum number of likers to return

    Returns:
        List of User objects
    """
    likes = (
        RepositoryLike.select()
        .where(RepositoryLike.repository == repository)
        .order_by(RepositoryLike.created_at.desc())
        .limit(limit)
    )

    return [like.user for like in likes]


# ===== Download tracking operations =====


def create_download_session(
    repository: Repository,
    session_id: str,
    time_bucket: int,
    first_file: str,
    user: User | None = None,
):
    """Create a download session.

    NOTE: Wrap in db.atomic() to prevent race conditions with counter increment.

    Args:
        repository: Repository being downloaded
        session_id: Session cookie ID
        time_bucket: 15-minute time bucket
        first_file: First file downloaded
        user: User (NULL if anonymous)

    Returns:
        DownloadSession instance

    Raises:
        IntegrityError: If session already exists (duplicate)
    """
    return DownloadSession.create(
        repository=repository,
        user=user,
        session_id=session_id,
        time_bucket=time_bucket,
        file_count=1,
        first_file=first_file,
    )


def get_download_session(repository: Repository, session_id: str, time_bucket: int):
    """Get download session by dedup key.

    Args:
        repository: Repository
        session_id: Session cookie ID
        time_bucket: 15-minute time bucket

    Returns:
        DownloadSession instance or None
    """
    return DownloadSession.get_or_none(
        (DownloadSession.repository == repository)
        & (DownloadSession.session_id == session_id)
        & (DownloadSession.time_bucket == time_bucket)
    )


def increment_download_session_files(session_id: int) -> None:
    """Increment file_count and update last_download_at for a session.

    Args:
        session_id: DownloadSession ID
    """
    DownloadSession.update(
        file_count=DownloadSession.file_count + 1,
        last_download_at=datetime.now(timezone.utc),
    ).where(DownloadSession.id == session_id).execute()


def count_repository_sessions(repository: Repository) -> int:
    """Count total download sessions for a repository.

    Args:
        repository: Repository

    Returns:
        Number of download sessions
    """
    return (
        DownloadSession.select().where(DownloadSession.repository == repository).count()
    )


# ===== Daily statistics operations =====


def get_daily_stat(repository: Repository, date_value):
    """Get daily statistics for a specific date.

    Args:
        repository: Repository
        date_value: Date to query

    Returns:
        DailyRepoStats instance or None
    """
    return DailyRepoStats.get_or_none(
        (DailyRepoStats.repository == repository) & (DailyRepoStats.date == date_value)
    )


def list_daily_stats(repository: Repository, start_date, end_date):
    """List daily statistics for a date range.

    Args:
        repository: Repository
        start_date: Start date (inclusive)
        end_date: End date (inclusive)

    Returns:
        List of DailyRepoStats ordered by date ascending
    """
    return list(
        DailyRepoStats.select()
        .where(
            (DailyRepoStats.repository == repository)
            & (DailyRepoStats.date >= start_date)
            & (DailyRepoStats.date <= end_date)
        )
        .order_by(DailyRepoStats.date.asc())
    )


def get_latest_daily_stat(repository: Repository):
    """Get the most recent daily statistics entry for a repository.

    Args:
        repository: Repository

    Returns:
        DailyRepoStats instance or None
    """
    return (
        DailyRepoStats.select()
        .where(DailyRepoStats.repository == repository)
        .order_by(DailyRepoStats.date.desc())
        .first()
    )
