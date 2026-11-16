"""Create the first administrator without a shared default password."""
from getpass import getpass
from models import init_db, get_db, User


def main():
    init_db()
    db = get_db()
    try:
        if db.query(User).count():
            raise SystemExit('Users already exist. Use account management in the website.')
        username = input('Administrator username: ').strip()
        password = getpass('Password (at least 12 characters): ')
        confirmation = getpass('Confirm password: ')
        if not username or len(password) < 12 or password != confirmation:
            raise SystemExit('Invalid username or password confirmation.')
        user = User(username=username, is_admin=1)
        user.set_password(password)
        db.add(user)
        db.commit()
        print('Administrator created. Run: python wsgi.py')
    finally:
        db.remove()


if __name__ == '__main__':
    main()
