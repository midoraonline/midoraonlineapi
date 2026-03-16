from pathlib import Path

from fastapi_mail import ConnectionConfig, FastMail, MessageSchema
from pydantic import EmailStr

from core.config import get_settings

_conf: FastMail | None = None


def get_mail() -> FastMail:
    global _conf
    if _conf is None:
        settings = get_settings()
        config = ConnectionConfig(
            MAIL_USERNAME=settings.email,
            MAIL_PASSWORD=settings.email_password,
            MAIL_FROM=settings.email,
            MAIL_PORT=587,
            MAIL_SERVER="smtp.gmail.com",
            MAIL_STARTTLS=True,
            MAIL_SSL_TLS=False,
        )
        _conf = FastMail(config)
    return _conf


async def send_verification_email(to: str, verification_link: str) -> None:
    mail = get_mail()
    message = MessageSchema(
        subject="Verify your email",
        recipients=[to],
        body=f"Please verify your email by clicking: {verification_link}",
        subtype="plain",
    )
    await mail.send_message(message)
