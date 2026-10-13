#!/usr/bin/env python3
"""Create an admin password hash without writing the plaintext to disk."""
import getpass
from dca_bot.auth import hash_password


def main() -> None:
    first = getpass.getpass('New dashboard password (12+ characters): ')
    second = getpass.getpass('Confirm password: ')
    if first != second:
        raise SystemExit('Passwords do not match.')
    print('\nADMIN_PASSWORD_HASH=' + hash_password(first))


if __name__ == '__main__':
    main()
