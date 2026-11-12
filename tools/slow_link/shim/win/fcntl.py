"""Bench-only stand-in so the Linux dashboard imports on Windows; every call fails like a missing device."""


def ioctl(*args, **kwargs):
    raise OSError(38, "fcntl.ioctl is not available on the Windows bench")


def flock(*args, **kwargs):
    raise OSError(38, "fcntl.flock is not available on the Windows bench")


LOCK_SH = 1
LOCK_EX = 2
LOCK_NB = 4
LOCK_UN = 8
