"""Rows of a database table as Markdown: a section per row, a file per ROWS_PER_FILE rows.

    sql://customers             table "customers" of the database in SQL_SOURCE_URL
    sql://sales.orders          table "orders" in schema "sales"

SQL_SOURCE_URL is a SQLAlchemy URL: postgresql+psycopg://reader:...@host/db works out of the box
(MySQL, SQL Server and others with their driver installed). Give it a read-only database user.

A row becomes

    ## customers 42
    name: Acme Ltd
    plan: Gold
    notes: Wants invoices in EUR from March.

The heading makes the chunker keep each row in its own chunk, and labels it ("Section:
customers 42"), so an answer can say which record it came from.

Indexing rows suits text-heavy tables (product descriptions, support notes, FAQs). Numbers and
totals ("revenue last quarter") don't work through similarity search: an agent should query
those live, with a SQL tool, instead of reading indexed rows.
"""

from functools import partial

from sqlalchemy import Column, Engine, MetaData, Table, create_engine, func, select
from sqlalchemy.exc import NoSuchTableError

from app.core.config import SQL_SOURCE_URL
from app.ingestion.connectors import SourceFile

ROWS_PER_FILE = 500


def _render(engine: Engine, table: Table, key: list[Column], offset: int) -> bytes:
    sections = []
    with engine.connect() as conn:
        # a stable order, so file N always holds the same rows (unless rows are added or deleted
        # in between, which shifts them: fine for a nightly snapshot)
        rows = conn.execute(select(table).order_by(*key).offset(offset).limit(ROWS_PER_FILE)).mappings()
        for row in rows:
            ident = ", ".join(str(row[c.name]) for c in key)
            fields = "\n".join(
                f"{col}: {value}" for col, value in row.items()
                if value not in (None, "") and not isinstance(value, bytes | memoryview)  # no blobs
            )
            sections.append(f"## {table.name} {ident}\n{fields}")
    return "\n\n".join(sections).encode("utf-8")


def list_files(url: str) -> list[SourceFile]:
    name = url.removeprefix("sql://").strip("/")
    schema, _, table_name = name.rpartition(".")
    if not table_name:
        raise ValueError(f"expected sql://<table> or sql://<schema>.<table>, got {url!r}")
    if not SQL_SOURCE_URL:
        raise ValueError("set SQL_SOURCE_URL to the database's SQLAlchemy URL")
    engine = create_engine(SQL_SOURCE_URL)
    try:
        # reflection: read the table's columns from the database. It also means the name is
        # checked against real tables and never pasted into SQL ourselves
        table = Table(table_name, MetaData(), schema=schema or None, autoload_with=engine)
    except NoSuchTableError as e:
        raise ValueError(f"table not found: {name}") from e
    key = list(table.primary_key.columns) or list(table.columns)[:1]
    with engine.connect() as conn:
        count = conn.scalar(select(func.count()).select_from(table)) or 0
    # rows are read when a file is uploaded, not now: size unknown, so 0 (never skipped as too big)
    return [
        SourceFile(f"{name}/rows-{offset // ROWS_PER_FILE + 1:05d}.md", 0, partial(_render, engine, table, key, offset))
        for offset in range(0, count, ROWS_PER_FILE)
    ]
