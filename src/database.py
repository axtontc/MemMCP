# src/database.py
"""
SQLite connection manager and serialized write queue for MemMCP.
Provides concurrent multi-reader access and thread-safe serialized writing in WAL mode.
"""

from typing import Any, List, Tuple, Optional
import asyncio
import sqlite3
import json
import logging
import pathlib
from datetime import datetime, timezone

logger = logging.getLogger("memmcp.database")


class DatabaseError(Exception):
    """Base exception for database errors."""

    pass


class MutationRequest:
    """
    Encapsulates a database write operation or a batch of operations.
    """

    def __init__(
        self, queries: List[Tuple[str, List[Any]]], future: asyncio.Future[Any]
    ) -> None:
        self.queries = queries
        self.future = future


class DatabaseManager:
    """
    Manages SQLite database connections, WAL mode configuration,
    and serializes all write operations using an asyncio.Queue to a single worker connection.
    """

    def __init__(self, db_path: str, log_path: str = "memory_wal.log") -> None:
        """
        Initializes the database manager with specific file paths.

        Args:
            db_path: Path to the SQLite database file.
            log_path: Path to the memory mutation audit/recovery log.
        """
        self.db_path = db_path
        self.log_path = log_path
        self.queue: asyncio.Queue[MutationRequest] = asyncio.Queue(maxsize=1000)
        self._worker_task: Optional[asyncio.Task[None]] = None
        self._running: bool = False

    async def start(self) -> None:
        """
        Starts the database manager and initializes the background write worker.
        Raises DatabaseError if already started.
        """
        if self._running:
            raise DatabaseError("DatabaseManager is already running.")
        self._running = True
        self._worker_task = asyncio.create_task(self._writer_worker())
        logger.info(
            f"DatabaseManager started. db_path={self.db_path}, log_path={self.log_path}"
        )

    async def close(self) -> None:
        """
        Stops the background write worker, processes remaining items (if possible),
        and cleans up database connections.
        """
        if not self._running:
            return
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                logger.debug("Database worker task cancelled successfully.")
            self._worker_task = None
        logger.info("DatabaseManager closed.")

    async def execute_read(
        self, query: str, params: Optional[List[Any]] = None
    ) -> List[sqlite3.Row]:
        """
        Executes a read query concurrently using asyncio.to_thread to avoid blocking the event loop.
        Uses a separate, short-lived read connection.

        Args:
            query: The SQL select statement.
            params: Parameters to bind to the query.

        Returns:
            A list of sqlite3.Row rows matching the query.
        """
        bind_params = params if params is not None else []

        def _read() -> List[sqlite3.Row]:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                cursor = conn.cursor()
                cursor.execute(query, bind_params)
                return cursor.fetchall()
            except sqlite3.Error as e:
                logger.error(f"SQLite read error on query '{query}': {e}")
                raise DatabaseError(f"Database read failed: {e}") from e
            finally:
                conn.close()

        return await asyncio.to_thread(_read)

    async def execute_write(
        self, query: str, params: Optional[List[Any]] = None
    ) -> Any:
        """
        Enqueues a single SQL mutation (INSERT, UPDATE, DELETE) and awaits completion.

        Args:
            query: The SQL mutation query.
            params: Parameters to bind to the query.

        Returns:
            The last row ID or result of the operation.
        """
        bind_params = params if params is not None else []
        return await self.execute_batch_write([(query, bind_params)])

    async def execute_batch_write(self, queries: List[Tuple[str, List[Any]]]) -> Any:
        """
        Enqueues multiple SQL mutations to be executed in a single atomic transaction.

        Args:
            queries: A list of tuples containing (query, params).

        Returns:
            The result of the batch operation (e.g. number of rows inserted/updated, or None).
        """
        if not self._running:
            raise DatabaseError(
                "DatabaseManager is not running. Call start() before executing writes."
            )

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        request = MutationRequest(queries, future)
        try:
            await self.queue.put(request)
        except asyncio.QueueFull as e:
            logger.error("Database mutation write queue is full.")
            raise DatabaseError("Database mutation queue is full.") from e

        return await future

    async def _writer_worker(self) -> None:
        """
        Dedicated worker running on a background loop that processes all database mutations.
        Uses a single persistent write connection to serialize writes.
        """
        # Ensure database directory exists
        db_dir = pathlib.Path(self.db_path).parent
        if db_dir:
            db_dir.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        try:
            # Enable WAL mode and NORMAL synchronous settings
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")

            # Setup database tables and FTS5 search index
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE,
                    metadata TEXT
                );
            """)

            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                    id UNINDEXED,
                    content
                );
            """)

            # Set up triggers to sync insert, delete, and updates into the FTS5 virtual table
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS memories_insert_trigger AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts (id, content) VALUES (new.id, new.content);
                END;
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS memories_delete_trigger AFTER DELETE ON memories BEGIN
                    DELETE FROM memories_fts WHERE id = old.id;
                END;
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS memories_update_trigger AFTER UPDATE ON memories BEGIN
                    DELETE FROM memories_fts WHERE id = old.id;
                    INSERT INTO memories_fts (id, content) VALUES (new.id, new.content);
                END;
            """)
            conn.commit()

            while self._running:
                try:
                    request = await self.queue.get()
                except asyncio.CancelledError:
                    break

                try:
                    cursor = conn.cursor()
                    # Execute all batch writes in a single transaction
                    conn.execute("BEGIN IMMEDIATE TRANSACTION;")

                    for sql, params in request.queries:
                        cursor.execute(sql, params)

                    # Log write to memory_wal.log before commit (WAL design)
                    timestamp = (
                        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    )
                    log_entry = {"timestamp": timestamp, "queries": request.queries}

                    # Ensure directory for log file exists
                    log_dir = pathlib.Path(self.log_path).parent
                    if log_dir:
                        log_dir.mkdir(parents=True, exist_ok=True)

                    with open(self.log_path, "a", encoding="utf-8") as log_f:
                        log_f.write(json.dumps(log_entry) + "\n")

                    conn.commit()
                    request.future.set_result(cursor.lastrowid)
                except sqlite3.Error as err:
                    try:
                        conn.rollback()
                    except sqlite3.Error as rollback_err:
                        logger.error(
                            f"Failed to rollback database transaction: {rollback_err}"
                        )
                    logger.error(f"Database mutation failed: {err}")
                    request.future.set_exception(
                        DatabaseError(f"Database write transaction failed: {err}")
                    )
                finally:
                    self.queue.task_done()
        except sqlite3.Error as e:
            logger.critical(f"Database writer loop hit critical error: {e}")
        finally:
            conn.close()
            logger.info("Database write worker connection closed.")
