"""In-memory session backend implementation."""

from . import SessionBackend, SessionDict
import datetime
import threading
import time
from loguru import logger


class InMemorySessionBackend(SessionBackend):
    """
    An in-memory session backend.
    """

    def __init__(
        self,
        sessions_dict: dict[str, SessionDict] | None = None,
        run_scavenger: bool = True,
    ):
        """
        Initialize the in-memory session backend.
        The optional sessions_dict parameter can be used to share session data between multiprocessing workers.
        In that case, only one worker should run the scavenger thread to avoid race conditions.
        ```
        from multiprocessing import Manager
        manager = Manager()
        shared_sessions = manager.dict()
        ...
        def worker_main():
            backend = InMemorySessionBackend(sessions_dict=shared_sessions, run_scavenger=(worker_id == 0))
        ...
        ```
        Args:
            sessions_dict (dict[str, tuple[SessionDict, datetime.datetime]] | None): An optional dictionary to use as the session store.
            run_scavenger (bool): Whether to run the scavenger thread to clean up expired sessions.
            max_age (int): The maximum age of a session in seconds.
        """
        self.sessions: dict[str, SessionDict] = sessions_dict or {}
        self.lock = threading.Lock()
        if run_scavenger:
            self.scavenger_thread = threading.Thread(
                name="SessionScavengerThread",
                target=self._scavenger_loop,
                daemon=True,
            )
            self.scavenger_thread.start()

    def _scavenger_loop(self):
        logger.info(
            "Starting session scavenger thread with interval {} seconds",
            self.scavenger_interval,
        )
        while True:
            try:
                time.sleep(self.max_age // 2)
                now = datetime.datetime.now(tz=datetime.timezone.utc)
                with self.lock:
                    expired_sessions = [
                        session_id
                        for session_id, session in self.sessions.items()
                        if (now - session.last_touched).total_seconds()
                        > self.scavenger_interval
                    ]
                    if expired_sessions:
                        logger.info(
                            "Scavenger removing {} expired sessions",
                            len(expired_sessions),
                        )
                    for session_id in expired_sessions:
                        self.sessions.pop(session_id, None)
            except Exception:
                pass  # ignore errors in scavenger

    async def get_session(self, session_id: str) -> SessionDict | None:
        session = self.sessions.get(session_id)
        if session:
            if (
                datetime.datetime.now(tz=datetime.timezone.utc)
                - session.created_at
            ).total_seconds() <= self.max_age:
                return session
            else:
                # session expired
                self.sessions.pop(session_id, None)
        return None

    async def save_session(self, session: SessionDict) -> None:
        session.created_at = datetime.datetime.now(tz=datetime.timezone.utc)
        session.last_touched = session.created_at
        with self.lock:
            self.sessions[session.session_id] = session

    async def touch_session(self, session: SessionDict) -> None:
        session.last_touched = datetime.datetime.now(tz=datetime.timezone.utc)
        with self.lock:
            self.sessions[session.session_id] = session

    async def delete_session(self, session_id: str) -> None:
        if session_id in self.sessions:
            with self.lock:
                self.sessions.pop(session_id, None)
