import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import json
from pprint import pprint
from api_src.google.common.auth import get_delegated_credentials


def update_send_as(
    send_as_configuration: dict,
    user_email: str = "example_user@serniacapital.com",
    target_alias_email: str = "some_alias@serniacapital.com",
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
        # validate credentials
        creds.refresh(google.auth.transport.requests.Request())

        if creds.valid:
            print(f"Credentials obtained successfully:")
        else:
            raise ValueError(
                f"Credentials for {user_email} are not valid. Does {user_email} exist?"
            )

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
            print(f"Alias {target_alias_email} not found for user {user_email}.")
            input_resp = input("Would you like to create it? (y/n)")
            if input_resp == "y":

                send_as_configuration["sendAsEmail"] = target_alias_email

                result = (
                    service.users()
                    .settings()
                    .sendAs()
                    .create(userId=user_email, body=send_as_configuration)
                    .execute()
                )
                print(f"AFTER {user_email}")
                pprint(result)
                target_alias = result
            else:
                raise ValueError(
                    f"Alias {target_alias_email} not found for user {user_email}. Aborting."
                )
        else:
            print(f"Alias {target_alias_email} found for user {user_email}")

            print(f"BEFORE {user_email}")
            pprint(target_alias)

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

    # INPUTS
    first_name = "Anna"
    target_alias_email = "all@serniacapital.com"


    user_email = f"{first_name.lower()}@serniacapital.com"
    send_as_configuration = {
        "displayName": "Sernia Capital",
        "treatAsAlias": True,
        "signature": f'<div dir="ltr"><div style="font-family:arial"><b><br></b></div><div style="font-family:arial"><b>{first_name.title()}</b></div><div style="font-family:arial"><a href="mailto:{target_alias_email}" target="_blank">{target_alias_email}</a></div></div>',
    }

    update_send_as(
        send_as_configuration=send_as_configuration,
        user_email=user_email,
        target_alias_email=target_alias_email,
    )
