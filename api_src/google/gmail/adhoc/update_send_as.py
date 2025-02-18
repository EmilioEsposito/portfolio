from types import new_class
import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import json
from pprint import pprint
from api_src.google.common.auth import (
    get_delegated_credentials
)


def update_send_as(
    send_as_configuration: dict,
    user_email: str = "example_user@serniacapital.com",
    target_alias_email: str = "some_alias@serniacapital.com",
    normalize_signature: bool = False,
):
    """Update send-as alias's in gmail.
    Returns:Draft object, including updated signature.
    """

    scopes = [
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.labels",
        "https://www.googleapis.com/auth/gmail.metadata",
        "https://www.googleapis.com/auth/gmail.settings.basic",
        "https://www.googleapis.com/auth/gmail.settings.sharing",
    ]

    # Use delegated credentials instead of service credentials
    try:
        print(f"Getting delegated credentials for {user_email}...")
        creds = get_delegated_credentials(user_email=user_email, scopes=scopes)

        if creds.valid:
            print(f"Credentials obtained successfully:")
        else:
            raise ValueError(f"Credentials for {user_email} are not valid. Does {user_email} exist?")

    except Exception as e:
        print(f"Error getting credentials for {user_email}. {e}")
        raise

    try:
        # create gmail api client
        # print("\nBuilding Gmail service...")
        service = build("gmail", "v1", credentials=creds)

        target_alias = None
        print("\nFetching send-as aliases...")
        # pylint: disable=E1101
        aliases = service.users().settings().sendAs().list(userId="me").execute()
        for alias in aliases.get("sendAs"):
            if alias.get("sendAsEmail") == target_alias_email:
                target_alias = alias
                break

        if target_alias is None:
            raise ValueError(f"Alias {target_alias_email} not found for user {user_email}")

        print(f"BEFORE {user_email}")
        pprint(target_alias)


        if normalize_signature:
            new_signature = target_alias.get("signature").replace(
                user_email, target_alias_email
            )
            send_as_configuration["signature"] = new_signature

        # pylint: disable=E1101
        result = (
            service.users()
            .settings()
            .sendAs()
            .patch(
                userId=user_email,
                sendAsEmail=target_alias.get("sendAsEmail"),
                body=send_as_configuration,
            )
            .execute()
        )
        print(f"AFTER {user_email}")
        pprint(result)

    except HttpError as error:
        print(f"An error occurred: {error}")
        result = None

    return result.get("signature")


if __name__ == "__main__":
    send_as_configuration = {
        "displayName": "Sernia Capital",
    }
    update_send_as(
        send_as_configuration=send_as_configuration,
        user_email="example@serniacapital.com",
        target_alias_email="some_alias@serniacapital.com",
        normalize_signature=True,
    )
