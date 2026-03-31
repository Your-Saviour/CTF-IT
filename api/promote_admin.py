"""Promote a user to admin.

Usage:
    python3 -m api.promote_admin <username>
"""
import sys

from api.database import SessionLocal, init_db
from api.models import User


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 -m api.promote_admin <username>")
        sys.exit(1)

    username = sys.argv[1]
    init_db()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            print(f"Error: user '{username}' not found")
            sys.exit(1)
        if user.is_admin:
            print(f"'{username}' is already an admin")
            return
        user.is_admin = True
        db.commit()
        print(f"'{username}' is now an admin")
    finally:
        db.close()


if __name__ == "__main__":
    main()
