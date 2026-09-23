from collections.abc import Callable

from .extractor import (
    Article,
    EmptyArticleError,
    LoginRequiredError,
    SubscriptionRequiredError,
    extract_url_article,
)
from .fetcher import HTTPStatusError, PublicFetchError, UnsafeURL
from .kindle import send_to_kindle


class PipelineError(RuntimeError):
    """A safe, user-facing failure from the one-off article delivery pipeline."""

    kind = "pipeline"

    def __init__(self, user_message: str, stage: str):
        self.user_message = user_message
        self.stage = stage
        super().__init__(user_message)


class AuthenticationError(PipelineError):
    kind = "authentication"


class AccessError(PipelineError):
    kind = "access"


class UnsafeURLError(PipelineError):
    kind = "unsafe_url"


class FetchError(PipelineError):
    kind = "fetch"


class ExtractionError(PipelineError):
    kind = "extraction"


class DeliveryError(PipelineError):
    kind = "delivery"

    def __init__(self, user_message: str, *, delivery_state: str = "unknown"):
        self.delivery_state = delivery_state
        super().__init__(user_message, stage="sending")


def _raise_fetch_error(exc: Exception) -> PipelineError:
    if isinstance(exc, UnsafeURL):
        return UnsafeURLError(
            "The article, image, or cover URL points to a non-public destination and was blocked.",
            stage="fetching",
        )
    if isinstance(exc, HTTPStatusError):
        status = exc.status_code
        if status == 403:
            return AccessError(
                "The website denied access (HTTP 403). It may require a subscription or reject "
                "automated requests. Check that the account and article have access, then retry.",
                stage="fetching",
            )
        if status == 401:
            return AccessError(
                "The website requires authorization (HTTP 401). Check that the page is public "
                "or that its supported Substack login is configured.",
                stage="fetching",
            )
        if status == 402:
            return AccessError(
                "The website requires additional access (HTTP 402). Check the article's "
                "subscription requirements and retry.",
                stage="fetching",
            )
        return FetchError(f"The article page returned HTTP {status}.", stage="fetching")
    if isinstance(exc, PublicFetchError):
        return FetchError(
            "The article could not be fetched from its public website. Check the URL and retry.",
            stage="fetching",
        )
    return FetchError("The article could not be fetched from its public website.", stage="fetching")


def send_url_to_kindle(
    url: str,
    config: dict,
    on_stage: Callable[[str], None] | None = None,
) -> Article:
    """Fetch, extract, and email one public article to the configured Kindle address."""
    try:
        article = extract_url_article(url, config, on_stage=on_stage)
    except LoginRequiredError as exc:
        raise AuthenticationError(str(exc), stage="fetching") from exc
    except SubscriptionRequiredError as exc:
        raise AccessError(str(exc), stage="fetching") from exc
    except EmptyArticleError as exc:
        raise ExtractionError(str(exc), stage="extracting") from exc
    except (UnsafeURL, PublicFetchError, HTTPStatusError) as exc:
        raise _raise_fetch_error(exc) from exc
    except Exception as exc:
        raise ExtractionError(
            "The page could not be prepared as a readable article.", stage="extracting"
        ) from exc

    if on_stage is not None:
        on_stage("sending")

    required = ("kindle_email", "sender_email", "sender_password")
    if any(not config.get(key) for key in required):
        raise DeliveryError(
            "Kindle delivery is not configured. Set KINDLE_EMAIL, SENDER_EMAIL, and "
            "SENDER_PASSWORD before retrying.",
            delivery_state="not_sent",
        )

    try:
        send_to_kindle(
            article=article,
            kindle_email=config["kindle_email"],
            sender_email=config["sender_email"],
            sender_password=config["sender_password"],
            smtp_host=config.get("smtp_host", "smtp.gmail.com"),
            smtp_port=config.get("smtp_port", 587),
        )
    except Exception as exc:
        raise DeliveryError(
            "Delivery could not be confirmed. Check your Kindle address and SMTP settings "
            "before retrying; the message may have been accepted by the mail server.",
            delivery_state="unknown",
        ) from exc

    return article
