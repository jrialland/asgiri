"""
Database session backend implementation.
This is a generic code that use DB-API 2.0 (PEP 249) - compliant drivers.
"""

from . import SessionBackend, SessionDict
from typing import Any, Callable, Generator, Iterable, Optional, Protocol, Sequence, Tuple, Union, runtime_checkable
from contextlib import contextmanager
import datetime

# Define DB-API compliant Cursor protocol
@runtime_checkable
class CursorProtocol(Protocol):
    description: Optional[Sequence[Tuple]]  # Column metadata
    rowcount: int

    def execute(self, operation: str, parameters: Union[Sequence[Any], dict] = ...) -> "CursorProtocol": ...
    def executemany(self, operation: str, seq_of_parameters: Iterable[Union[Sequence[Any], dict]]) -> "CursorProtocol": ...
    def fetchone(self) -> Optional[Tuple[Any, ...]]: ...
    def fetchmany(self, size: int = ...) -> list[Tuple[Any, ...]]: ...
    def fetchall(self) -> list[Tuple[Any, ...]]: ...
    def close(self) -> None: ...

# Define DB-API compliant Connection protocol
@runtime_checkable
class ConnectionProtocol(Protocol):
    def cursor(self) -> CursorProtocol: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...

@contextmanager
def connection_context(connection_factory: Callable[[], ConnectionProtocol]) -> Generator[ConnectionProtocol, None, None]:
    conn = connection_factory()
    try:
        yield conn
    except Exception:
        raise
    finally:
        conn.close()


class DatabaseSessionBackend(SessionBackend):
    """
    A database-backed session backend.
    """

    def __init__(self, connection_factory: Callable[[], ConnectionProtocol]):
        """
        Initialize the database session backend.
        Args:
            db_connection (Any): A database connection object compliant with Python DB API 2.0.
        """
        self.connection_factory = connection_factory
        self._initialize_db()

    def _connection(self):
        return connection_context(self.connection_factory)

    def _initialize_db(self):
        """
        Initialize the sessions table in the database.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    created_at TIMESTAMP,
                    last_touched TIMESTAMP
                )
            """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS session_data (
                    session_id TEXT PRIMARY KEY,
                    key TEXT,
                    value TEXT,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                )
            """
            )
            conn.commit()

    async def touch_session(self, session: SessionDict) -> None:
        session.last_touched = datetime.datetime.now(tz=datetime.timezone.utc)
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE sessions
                SET last_touched = ?
                WHERE session_id = ?
            """,
                (session.last_touched, session.session_id),
            )
            conn.commit()

    async def get_session(self, session_id: str) -> SessionDict | None:
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT created_at, last_touched FROM sessions WHERE session_id = ?
            """,
                (session_id,),
            )
            row = cursor.fetchone()
            if row:
                created_at, last_touched = row
                session = SessionDict(
                    session_id=session_id,
                    created_at=created_at,
                    last_touched=last_touched,
                )
                cursor.execute(
                    """
                    SELECT key, value FROM session_data WHERE session_id = ?
                """,
                    (session_id,),
                )
                data_rows = cursor.fetchall()
                for key, value in data_rows:
                    session.data[key] = value
                return session
        return None

    async def delete_session(self, session_id: str) -> None:
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                DELETE FROM session_data WHERE session_id = ?
            """,
                (session_id,),
            )
            cursor.execute(
                """
                DELETE FROM sessions WHERE session_id = ?
            """,
                (session_id,),
            )
            conn.commit()

    async def save_session(self, session: SessionDict) -> None:
        session.last_touched = datetime.datetime.now(tz=datetime.timezone.utc)
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO sessions (session_id, created_at, last_touched)
                VALUES (?, ?, ?)
            """,
                (session.session_id, session.created_at, session.last_touched),
            )
            cursor.execute(
                """DELETE FROM session_data WHERE session_id = ?""",
                (session.session_id,),
            )
            for key, value in session.data.items():
                cursor.execute(
                    """
                    INSERT INTO session_data (session_id, key, value)
                    VALUES (?, ?, ?)
                """,
                    (session.session_id, key, value),
                )
            conn.commit()

    async def update_session(self, session: SessionDict) -> None:
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE sessions
                SET last_touched = ?
                WHERE session_id = ?
            """,
                (session.last_touched, session.session_id),
            )

            cursor.execute(
                """
                DELETE FROM session_data WHERE session_id = ?
            """,
                (session.session_id,),
            )

            for key, value in session.data.items():
                cursor.execute(
                    """
                    INSERT INTO session_data (session_id, key, value) VALUES (?, ?, ?)
                """,
                    (session.session_id, key, value),
                )
            conn.commit()
