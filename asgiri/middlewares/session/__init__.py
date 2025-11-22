"""Session management middleware for ASGI applications.
This module provides a server-side session management middleware for ASGI applications.
It includes a session dictionary class, an abstract session backend interface, and other related components.
A 'session' attribute is added to the ASGI scope for HTTP requests, allowing easy access to session data within request handlers.
The session data is stored in a SessionDict, which behaves like a standard dictionary but includes additional attributes to track session state.
The SessionBackend abstract class defines the interface for session storage backends, allowing for different implementations (e.g., in-memory, database, file-based, etc.).

This middleware is compatible with [the way starlette handles sessions](https://github.com/Kludex/starlette/blob/4941b4a04993d087dea66e9287fe9472babee879/starlette/requests.py#L160),
making it easy to integrate with existing applications using starlette or FastAPI.

"""

import datetime
from abc import ABC, abstractmethod
from typing import Literal, Any
from asgiref.typing import (
    ASGI3Application,
    Scope,
    HTTPScope,
    ASGIReceiveCallable,
    ASGISendCallable,
)
from contextvars import ContextVar
from werkzeug.local import LocalProxy
from loguru import logger


# ------------------------------------------------------------------------------
class SessionDict(dict[str, Any]):
    """
    Holds session data, behaves like a dictionary.
    It has somme extra attributes to track session state.
    is_new: bool
        Indicates if the session is newly created.
    is_pristine: bool
        Indicates if the session has not been modified.
    """

    UNMODIFIABLE_ERROR = (
        "Session cannot be modified after response headers have been sent."
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_new = True
        self.is_pristine = True
        self.session_id: str | None = None
        self.invalidate_requested: bool = False
        self.created_at: datetime.datetime | None = None
        self.last_touched: datetime.datetime | None = None
        self.modifiable: bool = True

    def __setitem__(self, key, value):
        if not self.modifiable:
            raise RuntimeError(SessionDict.UNMODIFIABLE_ERROR)
        self.is_pristine = False
        return super().__setitem__(key, value)

    def __delitem__(self, key):
        if not self.modifiable:
            raise RuntimeError(SessionDict.UNMODIFIABLE_ERROR)
        self.is_pristine = False
        return super().__delitem__(key)

    def clear(self):
        if not self.modifiable:
            raise RuntimeError(SessionDict.UNMODIFIABLE_ERROR)
        self.is_pristine = False
        return super().clear()

    def pop(self, key, default=None):
        if not self.modifiable:
            raise RuntimeError(SessionDict.UNMODIFIABLE_ERROR)
        self.is_pristine = False
        return super().pop(key, default)

    def popitem(self):
        if not self.modifiable:
            raise RuntimeError(SessionDict.UNMODIFIABLE_ERROR)
        self.is_pristine = False
        return super().popitem()

    def update(self, *args, **kwargs):
        if not self.modifiable:
            raise RuntimeError(SessionDict.UNMODIFIABLE_ERROR)
        self.is_pristine = False
        return super().update(*args, **kwargs)

    def setdefault(self, key, default=None):
        if not self.modifiable:
            raise RuntimeError(SessionDict.UNMODIFIABLE_ERROR)
        self.is_pristine = False
        return super().setdefault(key, default)

    def invalidate(self):
        """Mark the session for invalidation (deletion)"""
        self.invalidate_requested = True


# ------------------------------------------------------------------------------
class SessionBackend(ABC):

    def __init__(self):
        self.max_age: int = 1800  # default max age 30 minutes

    def set_max_age(self, max_age: int) -> None:
        """Set the maximum age for sessions in this backend.
        Args:
            max_age (int): The maximum age of sessions in seconds.
        """
        self.max_age = max_age

    async def create_session(self) -> SessionDict:
        """Create a new session.
        This method returns a new, unsaved session instance, with no session ID assigned.
        By design, calling this method should be a cheap operation as it will be called on every request.
        """
        return SessionDict()

    async def create_session_id(self) -> str:
        """Generate a new unique session ID.
        This method should return a securely generated random string suitable for use as a session ID.
        The session Id will be used to identify the session in storage.
        """
        import secrets

        return secrets.token_urlsafe(32)

    @abstractmethod
    async def get_session(self, session_id: str) -> SessionDict | None:
        """Retrieve session data for the given session ID.
        Args:
            session_id (str): The session ID to look up.
        Returns:
            SessionDict | None: The session data if found and valid, otherwise None.
        """
        pass

    @abstractmethod
    async def save_session(self, session: SessionDict) -> None:
        """Save the session data. This method is called once for new sessions only.
        The session_id attribute of the session will be set before this method is called.
        Args:
            session (SessionDict): The session data to save.
        """
        pass

    async def update_session(self, session: SessionDict) -> None:
        """Update the session data. This method is called when an existing session that has been modified needs to be saved.
        When the session is not modified, touch_session will be called instead.
        Args:
            session (SessionDict): The session data to update.
        """
        await self.save_session(session)

    @abstractmethod
    async def touch_session(self, session: SessionDict) -> None:
        """Update the session's last accessed time without modifying its data.
        This method is called to extend the session's validity period.
        It is not called when update_session was called.
        Args:
            session (SessionDict): The session data to touch.
        """
        pass

    @abstractmethod
    async def delete_session(self, session_id: str) -> None:
        """Delete/invalidate the session with the given session ID.
        Once deleted, get_session should return None for this session ID.
        Args:
            session_id (str): The session ID to delete.
        """
        pass


# ------------------------------------------------------------------------------
session_context: ContextVar[SessionDict] = ContextVar(
    "session_context", default=None
)

# This variable can be used to access the current session in request handlers instead of scope["session"]
session = LocalProxy(session_context)


# ------------------------------------------------------------------------------
class SessionMiddleware(ASGI3Application):

    def __init__(
        self,
        app: ASGI3Application,
        backend: SessionBackend,
        max_age: int = int(datetime.timedelta(minutes=30).total_seconds()),
        cookie_name: str = "sessionid",
        same_site: Literal["Lax", "Strict", "None"] = "Lax",
    ):
        self.app = app
        self.backend = backend
        self.backend.set_max_age(max_age)
        self.max_age = max_age
        self.cookie_name = cookie_name
        self.same_site = same_site

    def _get_session_id_from_cookies(self, scope: HTTPScope) -> str | None:
        headers = dict(scope.get("headers", []))
        for key, value in headers.items():
            if key == b"cookie":
                for cookie in value.decode().split("; "):
                    if cookie.startswith(f"{self.cookie_name}="):
                        return cookie.split("=")[1]
        return None

    def _add_cookie_to_response(
        self,
        scope: HTTPScope,
        headers: list[tuple[bytes, bytes]],
        session_id: str,
    ) -> list[tuple[bytes, bytes]]:
        secure_flag = "Secure; " if scope.get("scheme") == "https" else ""
        cookie_value = f"{self.cookie_name}={session_id}; Path={scope.get('path') or '/'}; HttpOnly; {secure_flag}SameSite={self.same_site}"
        headers.append((b"set-cookie", cookie_value.encode()))
        return headers

    def _add_delete_cookie_to_response(
        self,
        scope: HTTPScope,
        headers: list[tuple[bytes, bytes]],
    ) -> list[tuple[bytes, bytes]]:
        secure_flag = "Secure; " if scope.get("scheme") == "https" else ""
        cookie_value = f"{self.cookie_name}=; Path={scope.get('path') or '/'}; Expires=Thu, 01 Jan 1970 00:00:00 GMT; HttpOnly; {secure_flag}SameSite={self.same_site}"
        headers.append((b"set-cookie", cookie_value.encode()))
        return headers

    def _make_send_wrapper(
        self, send: ASGISendCallable, scope: HTTPScope, session_id: str
    ) -> ASGISendCallable:
        async def wrapped_send(message):
            if message["type"] == "http.response.start":
                session: SessionDict = scope["session"]
                # If the session is new and modified, save it wit a new session id
                if session.is_new or not session.is_pristine:
                    session.session_id = await self.backend.create_session_id()
                    logger.debug(
                        "Saving new session with ID {}", session.session_id
                    )
                    await self.backend.save_session(session)
                    message["headers"] = self._add_cookie_to_response(
                        scope,
                        list(message.get("headers", [])),
                        session.session_id,
                    )
                elif session.session_id:
                    # an existing session
                    if session.invalidate_requested:
                        # delete the session if requested
                        await self.backend.delete_session(session.session_id)
                        message["headers"] = (
                            self._add_delete_cookie_to_response(
                                scope, list(message.get("headers", []))
                            )
                        )
                    else:
                        # update the session if modified
                        if not session.is_pristine:
                            logger.debug(
                                "Updating session with ID {}",
                                session.session_id,
                            )
                            await self.backend.update_session(session)
                        else:
                            # touch the session if not modified
                            logger.debug(
                                "Touching session with ID {}",
                                session.session_id,
                            )
                            await self.backend.touch_session(session.session_id)
                try:
                    await send(message)
                finally:
                    # mark session as unmodifiable after response start, because headers were already sent
                    session.modifiable = False
            else:
                await send(message)

        return wrapped_send

    async def __call__(
        self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:

        if scope["type"] == "http":

            # Try to get session ID from cookies, or create a new session if not found
            session_id = self._get_session_id_from_cookies(scope)
            if session_id and (
                existing_session := await self.backend.get_session(session_id)
            ):
                logger.debug(
                    "Found existing session with ID {}",
                    existing_session.session_id,
                )
                scope["session"] = existing_session
            else:
                scope["session"] = await self.backend.create_session()

            # assign session to context variable
            session_context.set(scope["session"])

            try:
                await self.app(
                    scope,
                    receive,
                    self._make_send_wrapper(send, scope, session_id),
                )
            finally:
                session_context.set(None)

        else:
            await self.app(scope, receive, send)
