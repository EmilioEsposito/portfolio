


def adhoc_generate_new_password():
    import hashlib
    import secrets

    # Generate a random salt
    salt = secrets.token_hex(16)
    password = input("Type password and hit ENTER: ")

    # Create salted hash
    password_bytes = (password + salt).encode('utf-8')
    password_hash = hashlib.sha256(password_bytes).hexdigest()

    print(f"Add these to your .env file:")
    print(f"NEXT_PUBLIC_ADMIN_PASSWORD_SALT={salt}")
    print(f"BUILDING_MESSAGE_PASSWORD_HASH={password_hash}")


