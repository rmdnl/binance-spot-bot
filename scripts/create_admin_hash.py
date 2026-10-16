#!/usr/bin/env python3
from getpass import getpass

from passlib.context import CryptContext

PASSWORD_CONTEXT = CryptContext(schemes=["bcrypt"], deprecated="auto")


def main() -> None:
    password = getpass("New dashboard password (minimum 8 characters): ")
    if len(password) < 8:
        raise SystemExit("Password must contain at least 8 characters.")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")
    print(PASSWORD_CONTEXT.hash(password))


if __name__ == "__main__":
    main()
