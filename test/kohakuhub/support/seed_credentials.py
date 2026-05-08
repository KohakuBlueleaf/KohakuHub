"""Deterministic credential fixtures planted by the test seed.

The values here are the canonical answer to "which API tokens / SSH keys
should the baseline ship with?" — the baseline ``build_baseline()`` plants
exactly these rows, and admin-side tests assert against them.

Both ed25519 keypairs are real: the private key ships alongside its public
key so future Git-over-SSH integration tests can sign with the matching
private half. The keypairs were generated once with
``cryptography.hazmat.primitives.asymmetric.ed25519`` and frozen here, so
every CI run sees identical fingerprints.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Keypairs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedKeypair:
    public_key: str
    private_key: str
    fingerprint: str


SEED_KEYPAIR_PRIMARY = SeedKeypair(
    public_key=(
        "ssh-ed25519 "
        "AAAAC3NzaC1lZDI1NTE5AAAAICkTsun+Px+5LKYR5hM1PFHI07H0mEdBCkjnieQBa8La "
        "seed-primary"
    ),
    private_key=(
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZWQyNTUx\n"
        "OQAAACApE7Lp/j8fuSymEeYTNTxRyNOx9JhHQQpI54nkAWvC2gAAAIgSvm6wEr5usAAAAAtzc2gt\n"
        "ZWQyNTUxOQAAACApE7Lp/j8fuSymEeYTNTxRyNOx9JhHQQpI54nkAWvC2gAAAEAR+JseVIp318U4\n"
        "qACfo8LGhfSE0tgeEyg4ieaaxYZMdCkTsun+Px+5LKYR5hM1PFHI07H0mEdBCkjnieQBa8LaAAAA\n"
        "AAECAwQF\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
    ),
    fingerprint="SHA256:iK3NzYswWRZyxvuXMcA5x7DscKDXBqdcJDHcnsAmSl0",
)

SEED_KEYPAIR_SECONDARY = SeedKeypair(
    public_key=(
        "ssh-ed25519 "
        "AAAAC3NzaC1lZDI1NTE5AAAAIM9LPgCG2V6b6eusP4Ds32HSeT9XI5kEh8znwZJL8Kon "
        "seed-secondary"
    ),
    private_key=(
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZWQyNTUx\n"
        "OQAAACDPSz4Ahtlem+nrrD+A7N9h0nk/VyOZBIfM58GSS/CqJwAAAIgdEjqnHRI6pwAAAAtzc2gt\n"
        "ZWQyNTUxOQAAACDPSz4Ahtlem+nrrD+A7N9h0nk/VyOZBIfM58GSS/CqJwAAAEARKCxI67mFiA8F\n"
        "KohS5CM4TZ3Yr1XmegpG6k39BVGyz89LPgCG2V6b6eusP4Ds32HSeT9XI5kEh8znwZJL8KonAAAA\n"
        "AAECAwQF\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
    ),
    fingerprint="SHA256:V64HYiVM8qORIqyxawv2j9z+f001Zlb2Gfe6es+1yME",
)

SEED_KEYPAIR_TERTIARY = SeedKeypair(
    public_key=(
        "ssh-ed25519 "
        "AAAAC3NzaC1lZDI1NTE5AAAAIEE6+Zx4EGmF78hvFxw7V99nO+2AMlMq4P3HwC2J1JLl "
        "seed-tertiary"
    ),
    private_key=(
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZWQyNTUx\n"
        "OQAAACBBOvmceBBphe/IbxccO1ffZzvtgDJTKuD9x8AtidSS5QAAAIjPifOsz4nzrAAAAAtzc2gt\n"
        "ZWQyNTUxOQAAACBBOvmceBBphe/IbxccO1ffZzvtgDJTKuD9x8AtidSS5QAAAEABkNyXrWp46jN2\n"
        "rlPPMjrdliTdytyHw4SrwcmwUFFwzkE6+Zx4EGmF78hvFxw7V99nO+2AMlMq4P3HwC2J1JLlAAAA\n"
        "AAECAwQF\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
    ),
    fingerprint="SHA256:UHiMFHDl1bHuDziVnLOYlAHSDQlah+DAk6yVUe10ZWI",
)


# ---------------------------------------------------------------------------
# SSH-key plants
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedSshKey:
    user: str
    title: str
    keypair: SeedKeypair
    last_used_days_ago: int | None  # None -> the row stays "never used"


SEED_SSH_KEYS: tuple[SeedSshKey, ...] = (
    # Owner has two distinct keypairs — a workstation key recently active,
    # and an archived laptop key untouched for ~200 days. The schema's
    # global UNIQUE on ``fingerprint`` is the reason owner cannot share a
    # keypair with member; each row needs its own.
    SeedSshKey(
        user="owner",
        title="Workstation",
        keypair=SEED_KEYPAIR_PRIMARY,
        last_used_days_ago=2,
    ),
    SeedSshKey(
        user="owner",
        title="Archived MBP",
        keypair=SEED_KEYPAIR_TERTIARY,
        last_used_days_ago=200,
    ),
    # Member has one key, never used yet.
    SeedSshKey(
        user="member",
        title="Member's MBP",
        keypair=SEED_KEYPAIR_SECONDARY,
        last_used_days_ago=None,
    ),
)


# ---------------------------------------------------------------------------
# API token plants
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedToken:
    user: str
    name: str
    plaintext: str
    last_used_days_ago: int | None  # None -> "never used"


# Token plaintext values are intentionally distinguishable so that a leaked
# value points back to the seed, not to a real production credential.
SEED_TOKENS: tuple[SeedToken, ...] = (
    SeedToken(
        user="owner",
        name="ci-token",
        plaintext="khub_seed_owner_ci_token_d8f1a2",
        last_used_days_ago=1,
    ),
    SeedToken(
        user="owner",
        name="archived-cron",
        plaintext="khub_seed_owner_archived_cron_3b91c4",
        last_used_days_ago=180,
    ),
    SeedToken(
        user="owner",
        name="never-used",
        plaintext="khub_seed_owner_never_used_91dd2e",
        last_used_days_ago=None,
    ),
    SeedToken(
        user="member",
        name="personal",
        plaintext="khub_seed_member_personal_4f2c0a",
        last_used_days_ago=5,
    ),
    SeedToken(
        user="outsider",
        name="scratch",
        plaintext="khub_seed_outsider_scratch_a17e93",
        last_used_days_ago=None,
    ),
)


__all__ = [
    "SEED_KEYPAIR_PRIMARY",
    "SEED_KEYPAIR_SECONDARY",
    "SEED_KEYPAIR_TERTIARY",
    "SEED_SSH_KEYS",
    "SEED_TOKENS",
    "SeedKeypair",
    "SeedSshKey",
    "SeedToken",
]
