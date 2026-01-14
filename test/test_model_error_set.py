from flockwave.server.model import ErrorSet


def test_empty_errorset_reports_zero_length_and_falsey():
    es = ErrorSet()
    assert len(es) == 0
    assert not bool(es)


def test_init_with_iterable():
    es = ErrorSet([17, 42, 80, 42])
    assert len(es) == 3
    assert set(es) == {17, 42, 80}
    assert sorted(es.json) == [17, 42, 80]


def test_addition_removal_single():
    es = ErrorSet()
    es.ensure(17)

    assert 17 in es
    assert len(es) == 1
    assert list(es) == [17]
    assert es.json == [17]

    es.ensure(42)
    assert 42 in es
    assert len(es) == 2
    assert set(es) == {17, 42}
    assert es.json == [17, 42] or es.json == [42, 17]

    es.ensure(17, present=False)
    assert 17 not in es
    assert len(es) == 1
    assert list(es) == [42]
    assert es.json == [42]

    es.ensure(80, present=False)  # removing non-existing code
    assert len(es) == 1
    assert list(es) == [42]
    assert es.json == [42]


def test_addition_removal_many():
    es = ErrorSet([1, 2, 3, 4, 5])

    es.ensure_many({3: False, 4: False, 6: True, 7: True})
    assert len(es) == 5
    assert set(es) == {1, 2, 5, 6, 7}
    assert sorted(es.json) == [1, 2, 5, 6, 7]


def test_replace_all_items():
    es = ErrorSet([10, 20, 30])
    es.set([20, 40, 50])

    assert len(es) == 3
    assert set(es) == {20, 40, 50}
    assert sorted(es.json) == [20, 40, 50]


def test_clear_errors():
    es = ErrorSet([100, 200, 300])
    es.clear()

    assert len(es) == 0
    assert not bool(es)
    assert list(es) == []
    assert es.json == []
