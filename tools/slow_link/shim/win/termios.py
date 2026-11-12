"""Bench-only stand-in so the Linux dashboard imports on Windows; serial calls fail like a missing device."""


class error(OSError):
    pass


TCSANOW = 0
TCIFLUSH = 0
TCIOFLUSH = 2
CS8 = 0o60
CREAD = 0o200
CLOCAL = 0o4000


def _unavailable(*args, **kwargs):
    raise OSError(38, "termios is not available on the Windows bench")


tcgetattr = tcsetattr = tcflush = tcdrain = _unavailable
