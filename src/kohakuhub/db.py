"""Database models for Kohaku Hub.

Major refactoring: Merged User and Organization into unified User model.
All relationships now use proper ForeignKey constraints for data integrity.
"""

from datetime import datetime, timezone
from functools import partial

from kohakuhub.constants import DB_ON_DELETE_SET_NULL
from peewee import (
    AutoField,
    BigIntegerField,
    BlobField,
    BooleanField,
    CharField,
    DateField,
    DateTimeField,
    ForeignKeyField,
    IntegerField,
    Model,
    PostgresqlDatabase,
    SqliteDatabase,
    TextField,
)

from kohakuhub.config import cfg
from kohakuhub.logger import get_logger

logger = get_logger("DB")


def _sqlite_path(url: str) -> str:
    return url.replace("sqlite:///", "")


# Choose DB backend
if cfg.app.db_backend == "postgres":
    url = cfg.app.database_url.replace("postgresql://", "")
    user_pass, host_db = url.split("@")
    user, password = user_pass.split(":")
    host_port, dbname = host_db.split("/")
    if ":" in host_port:
        host, port = host_port.split(":")
    else:
        host, port = host_port, 5432

    logger.info(f"Connecting to PostgreSQL: {user}@{host}:{port}/{dbname}")

    db = PostgresqlDatabase(
        dbname,
        user=user,
        password=password,
        host=host,
        port=int(port),
    )
else:
    db = SqliteDatabase(_sqlite_path(cfg.app.database_url), pragmas={"foreign_keys": 1})


class BaseModel(Model):
    class Meta:
        database = db


class User(BaseModel):
    """Unified User/Organization model.

    When is_org=False: Regular user with email/password
    When is_org=True: Organization (no email/password required)
    """

    id = AutoField()
    username = CharField(unique=True, index=True)
    normalized_name = CharField(
        unique=True, index=True
    )  # Normalized username for fast conflict checking
    is_org = BooleanField(default=False, index=True)  # True for organizations

    # User-specific fields (nullable for orgs)
    email = CharField(unique=True, index=True, null=True)
    password_hash = CharField(null=True)
    email_verified = BooleanField(default=False)
    is_active = BooleanField(default=True)

    # Separate quotas for private and public repositories
    private_quota_bytes = BigIntegerField(null=True)  # NULL = unlimited
    public_quota_bytes = BigIntegerField(null=True)  # NULL = unlimited
    private_used_bytes = BigIntegerField(default=0)
    public_used_bytes = BigIntegerField(default=0)

    # Profile fields (shared by both users and orgs)
    full_name = CharField(null=True)
    bio = TextField(null=True)
    description = TextField(null=True)  # For backward compat with orgs
    website = CharField(null=True)
    social_media = TextField(
        null=True
    )  # JSON: {twitter_x, threads, github, huggingface}

    # Avatar (1024x1024 JPEG stored as binary)
    avatar = BlobField(null=True)  # Binary JPEG data
    avatar_updated_at = DateTimeField(null=True)  # Track updates for cache busting
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))


class EmailVerification(BaseModel):
    id = AutoField()
    user = ForeignKeyField(
        User, backref="email_verifications", on_delete="CASCADE", index=True
    )
    token = CharField(unique=True, index=True)
    expires_at = DateTimeField()
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))


class Session(BaseModel):
    id = AutoField()
    session_id = CharField(unique=True, index=True)
    user = ForeignKeyField(User, backref="sessions", on_delete="CASCADE", index=True)
    secret = CharField()
    expires_at = DateTimeField()
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))


class Token(BaseModel):
    id = AutoField()
    user = ForeignKeyField(User, backref="tokens", on_delete="CASCADE", index=True)
    token_hash = CharField(unique=True, index=True)
    name = CharField()
    last_used = DateTimeField(null=True)
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))


class UserExternalToken(BaseModel):
    """User-specific external fallback source tokens.

    Allows users to provide their own tokens for external sources (HuggingFace, etc.).
    User tokens override admin-configured tokens for matching URLs.
    """

    id = AutoField()
    user = ForeignKeyField(
        User, backref="external_tokens", on_delete="CASCADE", index=True
    )
    url = CharField()  # Base URL (e.g., "https://huggingface.co")
    encrypted_token = TextField()  # Encrypted token (using crypto.py)
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))
    updated_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))

    class Meta:
        indexes = ((("user", "url"), True),)  # Unique per user+url


class Repository(BaseModel):
    id = AutoField()
    repo_type = CharField(index=True)
    namespace = CharField(index=True)
    name = CharField(index=True)
    full_id = CharField(index=True)  # Not unique - same full_id can exist across types
    private = BooleanField(default=False)
    owner = ForeignKeyField(
        User, backref="owned_repos", on_delete="CASCADE", index=True
    )

    # Repository-specific quota (NULL = inherit from namespace)
    quota_bytes = BigIntegerField(
        null=True
    )  # NULL = no specific limit, inherit from namespace
    used_bytes = BigIntegerField(default=0)

    # LFS settings (NULL = use server defaults from config)
    lfs_threshold_bytes = IntegerField(
        null=True
    )  # NULL = use cfg.app.lfs_threshold_bytes
    lfs_keep_versions = IntegerField(null=True)  # NULL = use cfg.app.lfs_keep_versions
    lfs_suffix_rules = TextField(
        null=True
    )  # JSON list of suffixes like [".safetensors", ".bin"], NULL = no suffix rules

    # Social metrics (denormalized counters for fast queries)
    downloads = IntegerField(default=0)  # Total download sessions (not file count)
    likes_count = IntegerField(default=0)  # Total likes

    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))

    class Meta:
        # Unique constraint on (repo_type, namespace, name) allows same name across types
        indexes = ((("repo_type", "namespace", "name"), True),)


class File(BaseModel):
    id = AutoField()
    repository = ForeignKeyField(
        Repository, backref="files", on_delete="CASCADE", index=True
    )
    path_in_repo = CharField(index=True)
    size = BigIntegerField(default=0)  # Changed from IntegerField to support files >2GB
    sha256 = CharField(index=True)
    lfs = BooleanField(default=False)
    is_deleted = BooleanField(default=False, index=True)  # Soft delete flag
    owner = ForeignKeyField(
        User, backref="owned_files", on_delete="CASCADE", index=True
    )  # Repository owner (denormalized for convenience)
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))
    updated_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))

    class Meta:
        indexes = ((("repository", "path_in_repo"), True),)


class StagingUpload(BaseModel):
    id = AutoField()
    repository = ForeignKeyField(
        Repository, backref="staging_uploads", on_delete="CASCADE", index=True
    )
    repo_type = CharField(index=True)
    revision = CharField(index=True)
    path_in_repo = CharField()
    sha256 = CharField(default="")
    size = BigIntegerField(default=0)  # Changed from IntegerField to support files >2GB
    upload_id = CharField(null=True)
    storage_key = CharField()
    lfs = BooleanField(default=False)
    uploader = ForeignKeyField(
        User, backref="uploads", on_delete=DB_ON_DELETE_SET_NULL, null=True, index=True
    )
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))


class UserOrganization(BaseModel):
    """User-Organization membership.

    Links regular users (is_org=False) to organizations (is_org=True).
    """

    id = AutoField()
    user = ForeignKeyField(
        User, backref="org_memberships", on_delete="CASCADE", index=True
    )
    organization = ForeignKeyField(
        User, backref="members", on_delete="CASCADE", index=True
    )
    role = CharField(default="member")  # visitor, member, admin, super-admin
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))

    class Meta:
        indexes = ((("user", "organization"), True),)  # Unique membership


class Commit(BaseModel):
    """Track commits made by users (LakeFS doesn't track the actual user).

    author = actual user who made the commit
    owner = repository owner (user or org, denormalized for convenience)
    """

    id = AutoField()
    commit_id = CharField(index=True)  # LakeFS commit ID (SHA)
    repository = ForeignKeyField(
        Repository, backref="commits", on_delete="CASCADE", index=True
    )
    repo_type = CharField(index=True)  # model/dataset/space
    branch = CharField(index=True)  # Branch name
    author = ForeignKeyField(
        User, backref="authored_commits", on_delete="CASCADE", index=True
    )  # User who made commit
    owner = ForeignKeyField(
        User, backref="owned_commits", on_delete="CASCADE", index=True
    )  # Repository owner (denormalized)
    username = CharField(index=True)  # Username (denormalized for performance)
    message = TextField()  # Commit message
    description = TextField(default="")  # Optional description
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))

    class Meta:
        indexes = (
            (("repository", "branch"), False),  # Query commits by repo+branch
            (("commit_id", "repository"), True),  # Unique commit per repo
        )


class LFSObjectHistory(BaseModel):
    """Track LFS object usage history for garbage collection.

    Keeps track of which commits reference which LFS objects,
    allowing us to preserve K versions of each file.
    """

    id = AutoField()
    repository = ForeignKeyField(
        Repository, backref="lfs_history", on_delete="CASCADE", index=True
    )
    path_in_repo = CharField(index=True)  # File path
    sha256 = CharField(index=True)  # LFS object hash
    size = BigIntegerField()  # Changed from IntegerField to support files >2GB
    commit_id = CharField(index=True)  # LakeFS commit ID
    # Optional link to File record for faster lookups
    # IMPORTANT: on_delete=DB_ON_DELETE_SET_NULL prevents CASCADE deletion when File is deleted
    # LFSObjectHistory must persist for quota tracking even after file deletion
    file = ForeignKeyField(
        File,
        backref="lfs_versions",
        on_delete=DB_ON_DELETE_SET_NULL,
        null=True,
        index=True,
    )
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))

    class Meta:
        # Index for quick lookups by repo and path
        indexes = (
            (("repository", "path_in_repo"), False),
            (("sha256",), False),
        )


class SSHKey(BaseModel):
    """User SSH public keys for Git operations."""

    id = AutoField()
    user = ForeignKeyField(User, backref="ssh_keys", on_delete="CASCADE", index=True)
    key_type = CharField()  # "ssh-rsa", "ssh-ed25519", "ecdsa-sha2-nistp256", etc.
    public_key = TextField()  # Full public key content
    fingerprint = CharField(unique=True, index=True)  # SHA256 fingerprint for lookup
    title = CharField()  # User-friendly title/name for the key
    last_used = DateTimeField(null=True)
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))

    class Meta:
        indexes = ((("user", "fingerprint"), True),)  # Unique per user


class Invitation(BaseModel):
    """Generic invitation system for various actions (org invites, account registration, etc.)."""

    id = AutoField()
    token = CharField(unique=True, index=True)  # UUID for invitation link
    action = CharField(index=True)  # "join_org", "register_account", etc.
    parameters = (
        TextField()
    )  # JSON string for action-specific data (org_id, role, etc.)
    created_by = ForeignKeyField(
        User, backref="created_invitations", on_delete="CASCADE", null=True, index=True
    )  # NULL for system/admin-generated invitations
    expires_at = DateTimeField()

    # Multi-use support
    max_usage = IntegerField(null=True)  # NULL=one-time, -1=unlimited, N=max N uses
    usage_count = IntegerField(default=0)  # Track how many times used

    # Legacy fields (kept for single-use compatibility)
    used_at = DateTimeField(null=True)  # First use timestamp
    used_by = ForeignKeyField(
        User,
        backref="used_invitations",
        on_delete=DB_ON_DELETE_SET_NULL,
        null=True,
        index=True,
    )
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))

    class Meta:
        indexes = (
            (
                ("action", "created_by"),
                False,
            ),  # Query invitations by action and creator
        )


class RepositoryLike(BaseModel):
    """User likes for repositories (similar to GitHub stars)."""

    id = AutoField()
    repository = ForeignKeyField(
        Repository, backref="likes", on_delete="CASCADE", index=True
    )
    user = ForeignKeyField(User, backref="liked_repos", on_delete="CASCADE", index=True)
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))

    class Meta:
        indexes = ((("repository", "user"), True),)  # UNIQUE - can't like twice


class DownloadSession(BaseModel):
    """Track download sessions for deduplication and statistics.

    A download session represents a bulk download (e.g., git clone, snapshot_download).
    Multiple files downloaded within the same session (15-minute window) count as 1 download.
    """

    id = AutoField()
    repository = ForeignKeyField(
        Repository, backref="download_sessions", on_delete="CASCADE", index=True
    )
    user = ForeignKeyField(
        User,
        backref="downloads",
        on_delete=DB_ON_DELETE_SET_NULL,
        null=True,
        index=True,
    )  # NULL if anonymous

    # Session identification for deduplication
    session_id = CharField(index=True)  # From cookie: auth session OR tracking cookie
    time_bucket = IntegerField(index=True)  # Unix timestamp / 900 (15-minute buckets)

    # Download details
    file_count = IntegerField(default=1)  # Files downloaded in this session
    first_file = CharField()  # Which file started the session

    # Timestamps
    first_download_at = DateTimeField(
        default=partial(datetime.now, tz=timezone.utc), index=True
    )
    last_download_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))

    class Meta:
        indexes = (
            (
                ("repository", "session_id", "time_bucket"),
                True,
            ),  # UNIQUE - deduplication key
        )


class DailyRepoStats(BaseModel):
    """Daily aggregated statistics for repository trends.

    TODAY's stats are updated in real-time.
    Historical stats (yesterday and older) are lazily aggregated from DownloadSession.
    """

    id = AutoField()
    repository = ForeignKeyField(
        Repository, backref="daily_stats", on_delete="CASCADE", index=True
    )
    date = DateField(index=True)  # YYYY-MM-DD

    # Aggregated metrics
    download_sessions = IntegerField(default=0)  # Unique download sessions
    authenticated_downloads = IntegerField(default=0)  # Logged-in users
    anonymous_downloads = IntegerField(default=0)  # Anonymous users
    total_files = IntegerField(default=0)  # Total file /resolve calls

    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))

    class Meta:
        indexes = ((("repository", "date"), True),)  # UNIQUE per repo per day


class FallbackSource(BaseModel):
    """External fallback sources for repository resolution.

    Allows KohakuHub to fall back to HuggingFace or other KohakuHub instances
    when repositories or files are not found locally.
    """

    id = AutoField()
    namespace = CharField(default="", index=True)  # "" for global, or user/org name
    url = CharField()  # Base URL: https://huggingface.co
    token = CharField(null=True)  # Admin-configured token (encrypted)
    priority = IntegerField(default=100, index=True)  # Lower = higher priority
    name = CharField()  # Display name: "HuggingFace"
    source_type = CharField()  # "huggingface" or "kohakuhub"
    enabled = BooleanField(default=True, index=True)
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))
    updated_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))

    class Meta:
        indexes = (
            (("namespace", "priority"), False),  # For ordered lookups
            (("enabled", "priority"), False),  # For active sources
        )


class ConfirmationToken(BaseModel):
    """General-purpose confirmation tokens for dangerous operations.

    Provides two-step confirmation for destructive actions with automatic expiration.
    Works across multiple workers (database-backed).

    Use cases:
    - S3 prefix deletion: action_type="delete_s3_prefix", action_data='{"prefix": "path/"}'
    - Bulk operations: action_type="bulk_delete_repos", action_data='{"repo_ids": [...]}'
    - Any operation requiring explicit user confirmation

    Example usage:
        # Step 1: Create token
        token = ConfirmationToken.create(
            token=str(uuid.uuid4()),
            action_type="delete_s3_prefix",
            action_data=json.dumps({"prefix": "hf-model-org-repo/"}),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=60)
        )

        # Step 2: Validate and consume token
        conf = ConfirmationToken.get_or_none(
            ConfirmationToken.token == token_str,
            ConfirmationToken.expires_at > datetime.now(timezone.utc)
        )
        if conf:
            data = json.loads(conf.action_data)
            # Execute action
            conf.delete_instance()  # Consume token (single-use)
    """

    id = AutoField()
    token = CharField(unique=True, index=True)  # UUID token
    action_type = CharField(index=True)  # Type of action (e.g., "delete_s3_prefix")
    action_data = TextField()  # JSON data for the action
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))
    expires_at = DateTimeField(index=True)  # For automatic cleanup

    class Meta:
        indexes = ((("action_type", "expires_at"), False),)  # For cleanup queries


def init_db():
    db.connect(reuse_if_open=True)
    db.create_tables(
        [
            User,
            EmailVerification,
            Session,
            Token,
            UserExternalToken,
            Repository,
            File,
            StagingUpload,
            UserOrganization,
            Commit,
            LFSObjectHistory,
            SSHKey,
            Invitation,
            RepositoryLike,
            DownloadSession,
            DailyRepoStats,
            FallbackSource,
            ConfirmationToken,
        ],
        safe=True,
    )
