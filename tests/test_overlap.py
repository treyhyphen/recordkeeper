"""Overlap metrics, account boundaries, and missing-data regression tests."""

from recordkeeper.overlap import compare, similarity


def playlist(id, songs, account=1, available=True):
    """Build a small in-memory playlist fixture."""
    return dict(
        id=id,
        name=str(id),
        songs=set(songs),
        artists={"artist"},
        account_id=account,
        available=available,
    )


def test_containment_is_not_jaccard():
    assert similarity({"a"}, {"a", "b"}) == {
        "shared": 1,
        "overall": 50,
        "a_in_b": 100,
        "b_in_a": 50,
    }
    assert similarity(set(), set())["overall"] == 0


def test_account_and_unknown_boundaries():
    assert compare([playlist(1, ["a"]), playlist(2, ["a"], account=2)]) == []
    assert compare([playlist(1, ["a"]), playlist(2, ["a"], available=False)]) == []


def test_duplicate_entries_and_combine_threshold():
    pairs = compare([playlist(1, ["a"] * 10), playlist(2, ["a"])])
    assert pairs[0]["songs"]["shared"] == 1
    assert not pairs[0]["candidate"]
    assert compare([playlist(1, "abcde"), playlist(2, "abcdef")])[0]["candidate"]
