"""S05: the kill-marker poll can read the bucket and finds no marker for this run."""
from rh import backup


def test_killpoll():
    assert backup.killpoll() is False
