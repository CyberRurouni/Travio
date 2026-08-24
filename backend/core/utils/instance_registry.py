import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Literal, Optional, Union

FactoryType = Literal["async", "sync"]


class InstanceRegistry:
    """
    Registry that ensures single instantiation per key with optional TTL.
    Supports async or sync factories.
    """

    def __init__(self, ttl: Optional[timedelta] = None):
        self._ttl = ttl
        self._instances: Dict[str, tuple[Any, datetime]] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(
        self,
        key: str,
        factory: Callable[..., Any],
        factory_type: FactoryType = "async",
        **kwargs,
    ) -> Any:
        """
        Returns a cached instance or creates it using the factory.
        factory_type: "async" (default) or "sync"
        """
        async with self._lock:
            now = datetime.now(timezone.utc)

            # Evict expired entries
            if self._ttl:
                expired_keys = [k for k, (_, ts) in self._instances.items() if now - ts > self._ttl]
                for k in expired_keys:
                    del self._instances[k]

            # Return cached instance if exists
            if key in self._instances:
                instance, _ = self._instances[key]
                self._instances[key] = (instance, now)
                return instance

            # Create instance
            if factory_type == "async":
                # Schedule async factory
                task = asyncio.create_task(factory(**kwargs))
                self._instances[key] = (task, now)
            else:
                # Sync factory
                instance = factory(**kwargs)
                self._instances[key] = (instance, now)
                return instance

        # Await async task outside the lock
        if factory_type == "async":
            try:
                instance = await task
                async with self._lock:
                    # Replace task placeholder with actual instance
                    self._instances[key] = (instance, datetime.now(timezone.utc))
                return instance
            except Exception:
                async with self._lock:
                    # Remove failed task
                    if self._instances.get(key, (None,))[0] is task:
                        del self._instances[key]
                raise

    async def evict(self, key: str):
        """
        Remove a cached instance for the given key.

        Used to invalidate in-memory instances whose underlying state has
        changed (e.g. a session that has just been ended) so the next call
        rebuilds them instead of reusing stale ones.
        """
        async with self._lock:
            self._instances.pop(key, None)


# ============================================================================
# 🔹 SHARED REGISTRY SINGLETONS
# ============================================================================
# Module-level registries shared across request handlers so instances are
# reused within their TTL window instead of being rebuilt per email/message.
# Prospects/sessions/assistants are short-lived per email burst (1h),
# SMTP connections are worth keeping longer (6h).
prospect_registry = InstanceRegistry(ttl=timedelta(hours=1))
session_registry = InstanceRegistry(ttl=timedelta(hours=1))
assistant_registry = InstanceRegistry(ttl=timedelta(hours=1))
smtp_registry = InstanceRegistry(ttl=timedelta(hours=6))

