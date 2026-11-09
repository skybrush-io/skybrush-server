import os
from base64 import b64encode
from hashlib import sha1
from pathlib import Path

import bcrypt
from passlib.handlers.md5_crypt import apr_md5_crypt
from pytest import fixture

from flockwave.server.ext.auth_basic import create_htpasswd_validator


def bcrypt_hash(password: str) -> str:
    # Apache's htpasswd tool writes bcrypt hashes with the $2y$ prefix
    hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=4))
    return hash.decode("ascii").replace("$2b$", "$2y$", 1)


def sha_hash(password: str) -> str:
    digest = sha1(password.encode("utf-8")).digest()
    return "{SHA}" + b64encode(digest).decode("ascii")


@fixture
def htpasswd_file(tmp_path: Path) -> Path:
    path = tmp_path / "htpasswd"
    path.write_text(
        f"alice:{bcrypt_hash('secret')}\n"
        f"bob:{apr_md5_crypt.hash('hunter2')}\n"
        f"carol:{sha_hash('password')}\n"
    )
    return path


def test_htpasswd_bcrypt(htpasswd_file: Path):
    validator = create_htpasswd_validator(htpasswd_file)
    assert validator("alice", "secret")
    assert not validator("alice", "Secret")
    assert not validator("alice", "")


def test_htpasswd_other_schemes(htpasswd_file: Path):
    validator = create_htpasswd_validator(htpasswd_file)
    assert validator("bob", "hunter2")
    assert not validator("bob", "secret")
    assert validator("carol", "password")
    assert not validator("carol", "secret")


def test_htpasswd_unknown_user(htpasswd_file: Path):
    validator = create_htpasswd_validator(htpasswd_file)
    assert not validator("dave", "secret")


def test_htpasswd_bcrypt_long_password(htpasswd_file: Path):
    # bcrypt raises an error for passwords longer than 72 bytes. The validator
    # must reject them instead of propagating the error.
    validator = create_htpasswd_validator(htpasswd_file)
    assert not validator("alice", "x" * 100)
    assert not validator("alice", "secret" + "x" * 100)


def test_htpasswd_reload(htpasswd_file: Path):
    validator = create_htpasswd_validator(htpasswd_file)
    assert not validator("dave", "secret")

    with htpasswd_file.open("a") as fp:
        fp.write(f"dave:{bcrypt_hash('secret')}\n")

    # Bump the modification time explicitly; the file system may not have a
    # fine enough timestamp resolution to notice the change otherwise
    mtime = htpasswd_file.stat().st_mtime + 10
    os.utime(htpasswd_file, (mtime, mtime))

    assert validator("dave", "secret")
