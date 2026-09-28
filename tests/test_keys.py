"""Users' own SauceNAO keys in SQLite."""

import stat

import pytest

from mikke.keys import KeyStore, UserKey

KEY = "0123456789abcdef0123456789abcdef0123a1b2"
NEW_KEY = "fedcba9876543210fedcba9876543210fedcc3d4"


@pytest.fixture
def store(tmp_path) -> KeyStore:
    return KeyStore(tmp_path / "data" / "mikke.sqlite3")


async def test_a_key_is_saved_per_user(store):
    assert await store.get(7) is None

    await store.set(7, KEY)

    assert await store.get(7) == UserKey(KEY, valid=True)
    assert await store.get(8) is None


async def test_keys_survive_a_new_store_on_the_same_file(tmp_path):
    path = tmp_path / "mikke.sqlite3"
    await KeyStore(path).set(7, KEY)

    assert await KeyStore(path).get(7) == UserKey(KEY, valid=True)


async def test_a_new_key_replaces_the_old_one_and_is_valid_again(store):
    await store.set(7, KEY)
    await store.invalidate(7, KEY)

    await store.set(7, NEW_KEY)

    assert await store.get(7) == UserKey(NEW_KEY, valid=True)


async def test_remove(store):
    await store.set(7, KEY)

    assert await store.remove(7) is True
    assert await store.get(7) is None
    assert await store.remove(7) is False


async def test_invalidate_marks_the_key_once(store):
    await store.set(7, KEY)

    assert await store.invalidate(7, KEY) is True
    assert await store.invalidate(7, KEY) is False
    assert await store.get(7) == UserKey(KEY, valid=False)


async def test_invalidate_leaves_a_key_the_user_has_since_replaced(store):
    await store.set(7, NEW_KEY)

    assert await store.invalidate(7, KEY) is False
    assert await store.get(7) == UserKey(NEW_KEY, valid=True)


def test_only_the_tail_of_a_key_is_ever_shown():
    key = UserKey(KEY, valid=True)
    assert key.masked == "…a1b2"
    assert KEY not in repr(key)
    assert KEY not in str(key)


def test_the_keys_are_readable_by_their_owner_only(tmp_path):
    path = tmp_path / "data" / "mikke.sqlite3"

    KeyStore(path)

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_key_file_made_before_is_made_the_owners_alone(tmp_path):
    path = tmp_path / "data" / "mikke.sqlite3"
    path.parent.mkdir()
    path.touch(mode=0o644)
    path.chmod(0o644)

    KeyStore(path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
