"""
Command-line entry point for the Member 2 database layer.

Usage (run from backend/ so `database` is importable):

    python -m database init                      # create tables
    python -m database drop                      # drop all tables
    python -m database ingest <file.csv> [--limit N] [--init]
    python -m database stats                     # row counts
    python -m database validate <file.csv>       # validate only, no storage
"""

import argparse
import sys

import pandas as pd


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m database",
                                     description="SAGE Member 2 database layer")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Create all tables (idempotent)")
    sub.add_parser("drop", help="Drop all tables")
    sub.add_parser("stats", help="Print row counts per table")

    p_ingest = sub.add_parser("ingest", help="Validate + preprocess + store a burn-in CSV")
    p_ingest.add_argument("csv", help="Path to the burn-in CSV")
    p_ingest.add_argument("--limit", type=int, default=None, help="Ingest only first N rows")
    p_ingest.add_argument("--init", action="store_true", help="Create tables first")

    p_validate = sub.add_parser("validate", help="Run validation only (no storage)")
    p_validate.add_argument("csv", help="Path to the burn-in CSV")

    args = parser.parse_args(argv)

    from database import connection, crud, ingestion, validation

    if args.command == "init":
        connection.init_db()
        print(f"Tables ready on {connection.get_database_url()}")
        return

    if args.command == "drop":
        connection.drop_db()
        print("All tables dropped.")
        return

    if args.command == "stats":
        connection.init_db()
        counts = crud.count_rows()
        for table, n in counts.items():
            print(f"  {table:18s} {n:>8,}")
        return

    if args.command == "ingest":
        if args.init:
            connection.init_db()
        result = ingestion.ingest_csv_file(args.csv, limit=args.limit)
        print(result.summary())
        return

    if args.command == "validate":
        df = pd.read_csv(args.csv)
        report = validation.validate_dataframe(df)
        print(report.summary())
        for issue in report.issues[:30]:
            print(f"  row {issue.row_index} [{issue.issue_type}] {issue.detail}")
        return

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())