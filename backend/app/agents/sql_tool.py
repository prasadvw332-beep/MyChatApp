"""Text-to-SQL: the agent answers questions about structured data by querying the database live.

The `sql://` connector puts rows into the knowledge base as text, which suits descriptions and
notes. It can't answer "how many orders did Pune ship last month?": similarity search doesn't
count or sum. For that the agent writes SQL:

    describe_database   tables and columns, so the model knows what exists (it guesses badly otherwise)
    query_database      runs one SELECT, returns up to SQL_AGENT_MAX_ROWS rows as JSON

Model-written SQL is untrusted input. The protection that actually holds is the database's own:
SQL_AGENT_URL must be a user with SELECT rights only, ideally on views made for this purpose
(no salary column, no other customers' data). The checks below are extra layers: one statement,
starting with SELECT or WITH, inside a READ ONLY transaction with a statement timeout, and always
rolled back. A keyword check alone would never be enough: functions can have side effects, and
SQL has many ways to hide a statement.
"""

import json
import re
from functools import cache

from langchain_core.tools import BaseTool, tool
from sqlalchemy import create_engine, inspect, text

from app.core.config import SQL_AGENT_MAX_ROWS, SQL_AGENT_URL

TIMEOUT_MS = 5000
MAX_TABLES = 50


@cache
def _engine():
    return create_engine(SQL_AGENT_URL, pool_pre_ping=True, pool_size=2)


def _check(sql: str) -> str:
    statement = sql.strip().rstrip(";").strip()
    if ";" in statement:
        raise ValueError("Only one statement at a time.")
    if not re.match(r"(?is)^(select|with)\b", statement):
        raise ValueError("Only SELECT queries are allowed.")
    return statement


def tools() -> list[BaseTool]:
    """The SQL tools, or none when SQL_AGENT_URL isn't set."""
    if not SQL_AGENT_URL:
        return []

    @tool
    def describe_database() -> str:
        """List the database's tables and their columns (with types). Call this before writing SQL."""
        insp = inspect(_engine())
        tables = {}
        for name in insp.get_table_names()[:MAX_TABLES]:
            tables[name] = [f"{c['name']} {c['type']}" for c in insp.get_columns(name)]
        return json.dumps(tables)

    @tool
    def query_database(sql: str) -> str:
        """Run one read-only SQL query (SELECT or WITH ... SELECT) and get the rows back as JSON.
        Use it for counts, sums, lists and lookups in structured data. Add LIMIT for long lists."""
        try:
            statement = _check(sql)
        except ValueError as e:
            return json.dumps({"error": str(e)})
        engine = _engine()
        with engine.connect() as conn:
            if engine.dialect.name == "postgresql":
                conn.execute(text("SET TRANSACTION READ ONLY"))
                conn.execute(text(f"SET LOCAL statement_timeout = {TIMEOUT_MS}"))
            try:
                result = conn.execute(text(statement))
                columns = list(result.keys())
                rows = [list(r) for r in result.fetchmany(SQL_AGENT_MAX_ROWS)]
            except Exception as e:  # a wrong column name: the model gets the error and can fix its SQL
                return json.dumps({"error": str(e).splitlines()[0][:300]})
            finally:
                conn.rollback()
        return json.dumps({"columns": columns, "rows": rows, "truncated": len(rows) == SQL_AGENT_MAX_ROWS}, default=str)

    return [describe_database, query_database]
