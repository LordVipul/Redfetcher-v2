import sqlite3
import config
import sys


def get_connection():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def run_query(sql, params=None):
    conn = get_connection()
    try:
        cursor = conn.execute(sql, params or [])
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        conn.commit()
        conn.close()
        return columns, rows, cursor.rowcount
    except Exception as e:
        conn.close()
        print(f"Error: {e}", file=sys.stderr)
        return None, None, -1


def print_results(columns, rows):
    if not rows:
        print("(no results)")
        return

    # Calculate column widths
    widths = [len(c) for c in columns]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(str(val)) if val is not None else 4)

    # Header
    header = " | ".join(c.ljust(widths[i]) for i, c in enumerate(columns))
    print(header)
    print("-" * len(header))

    # Rows
    for row in rows:
        line = " | ".join(
            str(row[i]).ljust(widths[i]) if row[i] is not None else "NULL".ljust(widths[i])
            for i in range(len(columns))
        )
        # Handle non-ASCII characters in terminal
        try:
            print(line)
        except UnicodeEncodeError:
            print(line.encode("utf-8", errors="replace").decode("utf-8"))

    print(f"\n({len(rows)} row{'s' if len(rows) != 1 else ''})")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python sql.py \"<SQL query>\"")
        print("Example: python sql.py \"SELECT * FROM posts LIMIT 5\"")
        print("Example: python sql.py \"DELETE FROM posts\"")
        print("Example: python sql.py \"SELECT subreddit, COUNT(*) FROM posts GROUP BY subreddit\"")
        print()
        print("Or use --watch to run the query every 2 seconds:")
        print("  python sql.py --watch \"SELECT COUNT(*) FROM posts\"")
        sys.exit(1)

    if sys.argv[1] == "--watch":
        import time
        sql = " ".join(sys.argv[2:])
        print(f"Running every 2 seconds. Ctrl+C to stop.\n")
        try:
            while True:
                columns, rows, count = run_query(sql)
                if columns:
                    print_results(columns, rows)
                print()
                time.sleep(2)
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        sql = " ".join(sys.argv[1:])
        columns, rows, count = run_query(sql)
        if columns is not None and rows is not None:
            if count >= 0 and not columns:
                print(f"Query OK. {count} row(s) affected.")
            else:
                print_results(columns, rows)
