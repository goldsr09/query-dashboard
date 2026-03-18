# Database Connection Pool Implementation
import sqlite3
import threading
from contextlib import contextmanager
from queue import Queue, Empty
import time

class SQLiteConnectionPool:
    def __init__(self, database_path, max_connections=10, timeout=30):
        self.database_path = database_path
        self.max_connections = max_connections
        self.timeout = timeout
        self.pool = Queue(maxsize=max_connections)
        self.lock = threading.Lock()
        self.created_connections = 0
        
        # Pre-populate pool with some connections
        for _ in range(3):
            self._create_connection()
    
    def _create_connection(self):
        """Create a new database connection"""
        if self.created_connections < self.max_connections:
            conn = sqlite3.connect(
                self.database_path,
                check_same_thread=False,
                timeout=20.0
            )
            conn.row_factory = sqlite3.Row  # Enable dict-like access
            conn.execute("PRAGMA journal_mode=WAL")  # Better concurrency
            conn.execute("PRAGMA synchronous=NORMAL")  # Better performance
            conn.execute("PRAGMA cache_size=10000")  # 10MB cache
            self.pool.put(conn)
            self.created_connections += 1
            return conn
        return None
    
    @contextmanager
    def get_connection(self):
        """Context manager for getting database connections"""
        conn = None
        try:
            # Try to get existing connection
            try:
                conn = self.pool.get(timeout=self.timeout)
            except Empty:
                # Pool exhausted, try to create new connection
                with self.lock:
                    conn = self._create_connection()
                    if conn is None:
                        raise RuntimeError("Database connection pool exhausted")
            
            yield conn
            
        except Exception as e:
            # If connection is bad, don't return it to pool
            if conn:
                conn.close()
            raise e
        finally:
            # Return connection to pool
            if conn:
                try:
                    # Test connection is still valid
                    conn.execute("SELECT 1")
                    self.pool.put(conn)
                except:
                    # Connection is bad, close it
                    conn.close()
                    with self.lock:
                        self.created_connections -= 1

# Usage example for your application:
db_pool = SQLiteConnectionPool("query_cache.db")

def get_cached_results_optimized(record_names, date_from, date_to):
    with db_pool.get_connection() as conn:
        cursor = conn.cursor()
        # Your existing query logic here
        cursor.execute("""
            SELECT 
                d.id AS record_id,
                d.name AS record_name,
                d.date AS record_date,
                SUM(f.is_selected) AS total_selected,
                COUNT(f.id) AS total_count
            FROM
                records d
            LEFT JOIN
                entries f ON d.id = f.record_id
            WHERE 
                d.name IN ({})
                AND d.date BETWEEN ? AND ?
            GROUP BY 
                d.id
        """.format(','.join('?'*len(record_names))), (*record_names, date_from, date_to))
        results = cursor.fetchall()
        return results
