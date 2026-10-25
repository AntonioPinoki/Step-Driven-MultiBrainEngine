"""One reentrant lock orders all preset/lore/package writes in this process."""
import threading

LOCK = threading.RLock()
