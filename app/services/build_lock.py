"""Redis-backed distributed lock around the Packer image build.

Why this exists: the build phase in `tasks.py` does a check-then-act pair
(`check_image_exists` → `packer.build`) on the shared OpenStack Glance store.
Two parallel workers triggering a build of the same `(project_id, image_name)`
both observe "not found" and both kick off a build, leaving a duplicate image
behind and burning ~10 minutes of compute. This lock serializes the build for
a given image name within a given OpenStack project so only one worker
actually builds; the other re-checks Glance after waiting and skips straight
to Terraform if the image now exists.

Backend: Redis (already present as Celery's result backend, no new dep).
Lock semantics: SET NX PX with a unique token; release uses a Lua-CAS so a
worker can never release a lock another worker grabbed after the first one's
TTL expired.
"""

from __future__ import annotations

import time
import uuid

import redis

from ..config import settings
from ..utils.logger import get_logger

logger = get_logger(__name__)


_LUA_RELEASE = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""

# Default lock TTL covers the worst-case Packer build (~30 min).
_DEFAULT_TTL_MS = 30 * 60 * 1000
_DEFAULT_POLL_S = 5
_DEFAULT_TOTAL_WAIT_S = 25 * 60


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.CELERY_RESULT_BACKEND)


class PackerBuildLock:
    """Acquire a Redis lock keyed on (project, image_name).

    The class is a context-manager-shaped polling loop. Pattern:

        lock = PackerBuildLock(project_id, image_name)
        while True:
            held = lock.acquire_or_wait()
            if held:
                if image_already_exists(): break  # someone built it for us
                packer.build(...)
                break
            # not held; we slept inside acquire_or_wait — re-check before retry
            if image_already_exists(): break
        lock.release()
    """

    def __init__(
        self,
        project_id: str,
        image_name: str,
        *,
        ttl_ms: int = _DEFAULT_TTL_MS,
        poll_interval_s: int = _DEFAULT_POLL_S,
        total_wait_s: int = _DEFAULT_TOTAL_WAIT_S,
    ):
        self.key = f"lock:packer:{project_id or 'unknown'}:{image_name}"
        self.token = uuid.uuid4().hex
        self.ttl_ms = ttl_ms
        self.poll_interval_s = poll_interval_s
        self.deadline = time.monotonic() + total_wait_s
        self._client = _redis_client()
        self._held = False

    def acquire_or_wait(self) -> bool:
        """Try to acquire. Returns True if held, False if we slept and the
        caller should re-check Glance and call again. Raises TimeoutError
        if the total wait budget is exhausted."""
        if self._client.set(self.key, self.token, nx=True, px=self.ttl_ms):
            self._held = True
            return True
        if time.monotonic() > self.deadline:
            raise TimeoutError(
                f"Timed out waiting for Packer build lock {self.key} " f"(another worker is still building this image)"
            )
        logger.info(
            "Waiting for in-progress Packer build",
            lock_key=self.key,
            poll_interval_s=self.poll_interval_s,
        )
        time.sleep(self.poll_interval_s)
        return False

    def release(self) -> None:
        if not self._held:
            return
        try:
            self._client.eval(_LUA_RELEASE, 1, self.key, self.token)
        except Exception as e:
            # Best-effort: even if release fails, the TTL will eventually
            # expire and the lock self-heals.
            logger.warning(f"Failed to release Packer lock {self.key}: {e}")
        finally:
            self._held = False
