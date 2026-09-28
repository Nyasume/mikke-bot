"""Flood protection's arithmetic; the handlers' flood tests go through the dispatcher."""

from mikke.flood import FloodMiddleware


def test_more_than_the_limit_within_the_window_is_a_flood():
    flood = FloodMiddleware(limit=20, window=3)
    flooding = [flood.is_flooding(7, 1_000) for _ in range(21)]
    assert flooding == [False] * 20 + [True]
    # three seconds later it is over
    assert not flood.is_flooding(7, 1_003)


def test_a_backlog_handled_after_a_newer_update_is_not_a_flood():
    # updates are not always handled in the order they were sent: a replayed backlog, parallel webhooks
    flood = FloodMiddleware(limit=20, window=3)
    assert not flood.is_flooding(7, 10_000)

    backlog = [flood.is_flooding(7, 1_000 + 10 * n) for n in range(25)]

    assert not any(backlog)
    # the newer update still counts for what comes after it
    assert [flood.is_flooding(7, 10_001) for _ in range(20)][-1]


def test_a_burst_handled_after_a_newer_update_is_still_a_flood():
    flood = FloodMiddleware(limit=20, window=3)
    assert not flood.is_flooding(7, 10_000)

    burst = [flood.is_flooding(7, 1_000) for _ in range(25)]

    assert burst == [False] * 20 + [True] * 5


def test_an_old_update_after_a_newer_burst_below_the_limit_is_no_flood():
    flood = FloodMiddleware(limit=20, window=3)
    assert not any(flood.is_flooding(7, 10_000) for _ in range(20))

    assert not flood.is_flooding(7, 1_000)
    # the newer ones still count for the next newer one
    assert flood.is_flooding(7, 10_001)
