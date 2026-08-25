import time


class Temp:
    """Small process-wide state — bot identity, index-job cancel flag."""
    BOT = None
    U_NAME = ""   # bot username, no @
    CANCEL_INDEX = False


temp = Temp()


def human_size(size: int) -> str:
    if not size:
        return "N/A"
    size = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


def readable_time(seconds: float) -> str:
    seconds = int(seconds)
    periods = [("d", 86400), ("h", 3600), ("m", 60), ("s", 1)]
    parts = []
    for name, secs in periods:
        val, seconds = divmod(seconds, secs)
        if val:
            parts.append(f"{val}{name}")
    return " ".join(parts) or "0s"


class Throttle:
    """Only allow an action every `interval` seconds — used to keep progress
    message edits from hitting Telegram's rate limit during indexing."""

    def __init__(self, interval: float = 3.0):
        self.interval = interval
        self._last = 0.0

    def ready(self) -> bool:
        now = time.time()
        if now - self._last >= self.interval:
            self._last = now
            return True
        return False
