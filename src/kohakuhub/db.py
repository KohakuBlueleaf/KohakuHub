"""Database models for Kohaku Hub."""

from datetime import datetime, timezone
from functools import partial

from peewee import (
    AutoField,
    BigIntegerField,
    BooleanField,
    CharField,
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
    id = AutoField()
    username = CharField(unique=True, index=True)
    email = CharField(unique=True, index=True)
    password_hash = CharField()
    email_verified = BooleanField(default=False)
    is_active = BooleanField(default=True)
    # Separate quotas for private and public repositories
    private_quota_bytes = BigIntegerField(null=True)  # NULL = unlimited
    public_quota_bytes = BigIntegerField(null=True)  # NULL = unlimited
    private_used_bytes = BigIntegerField(default=0)
    public_used_bytes = BigIntegerField(default=0)
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))


class EmailVerification(BaseModel):
    id = AutoField()
    user = IntegerField(index=True)
    token = CharField(unique=True, index=True)
    expires_at = DateTimeField()
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))


class Session(BaseModel):
    id = AutoField()
    session_id = CharField(unique=True, index=True)
    user_id = IntegerField(index=True)
    secret = CharField()
    expires_at = DateTimeField()
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))


class Token(BaseModel):
    id = AutoField()
    user_id = IntegerField(index=True)
    token_hash = CharField(unique=True, index=True)
    name = CharField()
    last_used = DateTimeField(null=True)
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))


class Repository(BaseModel):
    id = AutoField()
    repo_type = CharField(index=True)
    namespace = CharField(index=True)
    name = CharField(index=True)
    full_id = CharField(index=True)  # Not unique - same full_id can exist across types
    private = BooleanField(default=False)
    owner_id = IntegerField(index=True, default=1)
    # Repository-specific quota (NULL = inherit from namespace)
    quota_bytes = BigIntegerField(
        null=True
    )  # NULL = no specific limit, inherit from namespace
    used_bytes = BigIntegerField(default=0)
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))

    class Meta:
        # Unique constraint on (repo_type, namespace, name) allows same name across types
        indexes = ((("repo_type", "namespace", "name"), True),)


class File(BaseModel):
    id = AutoField()
    repo_full_id = CharField(index=True)
    path_in_repo = CharField(index=True)
    size = IntegerField(default=0)
    sha256 = CharField(index=True)
    lfs = BooleanField(default=False)
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))
    updated_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))

    class Meta:
        indexes = ((("repo_full_id", "path_in_repo"), True),)


class StagingUpload(BaseModel):
    id = AutoField()
    repo_full_id = CharField(index=True)
    repo_type = CharField(index=True)
    revision = CharField(index=True)
    path_in_repo = CharField()
    sha256 = CharField(default="")
    size = IntegerField(default=0)
    upload_id = CharField(null=True)
    storage_key = CharField()
    lfs = BooleanField(default=False)
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))


class Organization(BaseModel):
    id = AutoField()
    name = CharField(unique=True, index=True)
    description = TextField(null=True)
    # Separate quotas for private and public repositories
    private_quota_bytes = BigIntegerField(null=True)  # NULL = unlimited
    public_quota_bytes = BigIntegerField(null=True)  # NULL = unlimited
    private_used_bytes = BigIntegerField(default=0)
    public_used_bytes = BigIntegerField(default=0)
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))


class UserOrganization(BaseModel):
    id = AutoField()
    user = ForeignKeyField(
        User,
        backref="user_orgs",
        on_delete="CASCADE",
        index=True,
    )
    organization = ForeignKeyField(
        Organization,
        backref="members",
        on_delete="CASCADE",
        index=True,
    )
    role = CharField(default="member")  # keep your role semantics as before
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))


class Commit(BaseModel):
    """Track commits made by users (LakeFS doesn't track the actual user)."""

    id = AutoField()
    commit_id = CharField(index=True)  # LakeFS commit ID (SHA)
    repo_full_id = CharField(index=True)  # namespace/name
    repo_type = CharField(index=True)  # model/dataset/space
    branch = CharField(index=True)  # Branch name
    user_id = IntegerField(index=True)  # User who made the commit
    username = CharField(index=True)  # Username (denormalized for performance)
    message = TextField()  # Commit message
    description = TextField(default="")  # Optional description
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))

    class Meta:
        indexes = (
            (("repo_full_id", "branch"), False),  # Query commits by repo+branch
            (("commit_id", "repo_full_id"), True),  # Unique commit per repo
        )


class LFSObjectHistory(BaseModel):
    """Track LFS object usage history for garbage collection.

    Keeps track of which commits reference which LFS objects,
    allowing us to preserve K versions of each file.
    """

    id = AutoField()
    repo_full_id = CharField(index=True)
    path_in_repo = CharField(index=True)  # File path
    sha256 = CharField(index=True)  # LFS object hash
    size = IntegerField()
    commit_id = CharField(index=True)  # LakeFS commit ID
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))

    class Meta:
        # Index for quick lookups by repo and path
        indexes = (
            (("repo_full_id", "path_in_repo"), False),
            (("sha256",), False),
        )


class SSHKey(BaseModel):
    """User SSH public keys for Git operations."""

    id = AutoField()
    user_id = IntegerField(index=True)
    key_type = CharField()  # "ssh-rsa", "ssh-ed25519", "ecdsa-sha2-nistp256", etc.
    public_key = TextField()  # Full public key content
    fingerprint = CharField(unique=True, index=True)  # SHA256 fingerprint for lookup
    title = CharField()  # User-friendly title/name for the key
    last_used = DateTimeField(null=True)
    created_at = DateTimeField(default=partial(datetime.now, tz=timezone.utc))

    class Meta:
        indexes = ((("user_id", "fingerprint"), True),)  # Unique per user


def init_db():
    db.connect(reuse_if_open=True)
    db.create_tables(
        [
            User,
            EmailVerification,
            Session,
            Token,
            Repository,
            File,
            StagingUpload,
            Organization,
            UserOrganization,
            Commit,
            LFSObjectHistory,
            SSHKey,
        ],
        safe=True,
    )
