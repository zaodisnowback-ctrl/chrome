import ipaddress
import re
from urllib.parse import urlencode, urlsplit


MAX_ADDRESS_LENGTH = 4096


def resolve_address(value: str) -> str:
    address = value.strip()
    if not address:
        raise ValueError("Enter a web address or search query.")
    if len(address) > MAX_ADDRESS_LENGTH:
        raise ValueError("The address is too long.")
    if any(ord(character) < 32 or ord(character) == 127 for character in address):
        raise ValueError("The address contains control characters.")

    candidate = address
    try:
        parts = urlsplit(candidate)
    except ValueError as error:
        raise ValueError("Enter a valid HTTP or HTTPS address.") from error
    if parts.scheme.lower() in ("http", "https"):
        return validate_web_url(candidate)

    if "://" in address:
        raise ValueError("Only HTTP and HTTPS addresses are supported.")

    first_part = address.split("/", 1)[0]
    if not any(character.isspace() for character in address) and "." in first_part:
        return validate_web_url("https://" + address)

    return "https://www.google.com/search?" + urlencode({"q": address})


def validate_web_url(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_ADDRESS_LENGTH:
        raise ValueError("Enter a valid HTTP or HTTPS address.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("The address contains control characters.")
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        if parts.scheme.lower() not in ("http", "https") or not hostname:
            raise ValueError
        if parts.username is not None or parts.password is not None:
            raise ValueError
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            ascii_hostname = hostname.encode("idna").decode("ascii")
            labels = ascii_hostname.rstrip(".").split(".")
            if (
                len(ascii_hostname) > 253
                or not labels
                or any(
                    not label
                    or len(label) > 63
                    or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
                    for label in labels
                )
            ):
                raise ValueError
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError
    except (UnicodeError, ValueError) as error:
        raise ValueError("Enter a valid HTTP or HTTPS address without credentials.") from error
    return value
