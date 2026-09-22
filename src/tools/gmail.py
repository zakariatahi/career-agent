from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/gmail.send"
]


class GmailAuthorizationError(RuntimeError):
    """Authentication failed before any message-send request was made."""


def get_gmail_service(*, read_inbox: bool = False, interactive: bool = True, force_reconnect: bool = False):
    creds = None
    scopes = SCOPES + (["https://www.googleapis.com/auth/gmail.readonly"] if read_inbox else [])

    root = Path(__file__).resolve().parents[2]
    token_path = root / "token.json"

    if token_path.exists() and not force_reconnect:
        creds = Credentials.from_authorized_user_file(
            token_path,
        )

    if creds and not creds.has_scopes(scopes):
        creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())

        else:
            if not interactive:
                raise GmailAuthorizationError("Gmail connection needs authorization. Use Connect Gmail in the Application Center.")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(root / "credentials.json"),
                scopes,
            )

            creds = flow.run_local_server(
                port=0, timeout_seconds=180,
            )

        token_path.write_text(
            creds.to_json(),
            encoding="utf-8",
        )

    return build(
        "gmail",
        "v1",
        credentials=creds,
    )

import base64
import mimetypes
from email.message import EmailMessage
from pathlib import Path


def send_email(
    recipient: str,
    subject: str,
    body: str,
    attachment_path: str | None = None,
):
    try:
        service = get_gmail_service()
    except Exception as error:
        raise GmailAuthorizationError("Gmail authorization failed before sending. Use Connect Gmail to sign in again.") from error

    message = EmailMessage()

    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    if attachment_path:
        path = Path(attachment_path)

        mime_type, _ = mimetypes.guess_type(
            path.name
        )

        if mime_type:
            maintype, subtype = mime_type.split(
                "/",
                1,
            )
        else:
            maintype = "application"
            subtype = "octet-stream"

        with open(path, "rb") as file:
            message.add_attachment(
                file.read(),
                maintype=maintype,
                subtype=subtype,
                filename=path.name,
            )

    encoded = base64.urlsafe_b64encode(
        message.as_bytes()
    ).decode()

    return (
        service.users()
        .messages()
        .send(
            userId="me",
            body={"raw": encoded},
        )
        .execute()
    )
