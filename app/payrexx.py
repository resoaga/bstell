"""Payrexx hosted payment page (Gateway API). The customer pays on Payrexx's page;
we never see card data. Payrexx webhooks are not signed, so the payload is only
used to find the order - the payment status is always re-read from the API."""

import base64
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request

API_BASE = os.environ.get("PAYREXX_API_BASE", "https://api.payrexx.com/v1.0")


class PayrexxError(Exception):
    pass


def configured(settings) -> bool:
    return bool(settings.payrexx_instance and settings.payrexx_api_key)


def available(settings) -> bool:
    """Online payment is offered to customers: credentials set and switched on in the admin."""
    return bool(settings.online_payment_enabled and configured(settings))


def _signature(params: dict, secret: str) -> str:
    query = urllib.parse.urlencode(params)
    return base64.b64encode(hmac.new(secret.encode(), query.encode(), hashlib.sha256).digest()).decode()


def _call(settings, method: str, path: str, params: dict = None) -> dict:
    params = params or {}
    signature = _signature(params, settings.payrexx_api_key)
    instance = urllib.parse.quote(settings.payrexx_instance)
    if method == "POST":
        url = f"{API_BASE}/{path}?instance={instance}"
        data = urllib.parse.urlencode({**params, "ApiSignature": signature}).encode()
    else:
        url = f"{API_BASE}/{path}?instance={instance}&ApiSignature={urllib.parse.quote(signature)}"
        data = None
    request = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise PayrexxError(f"Payrexx HTTP {exc.code}: {exc.read()[:200]!r}") from exc
    except Exception as exc:
        raise PayrexxError(f"Payrexx nicht erreichbar: {exc}") from exc
    if body.get("status") != "success" or not body.get("data"):
        raise PayrexxError(f"Payrexx Fehler: {body.get('message', body)}")
    return body["data"][0]


def reference_for(order) -> str:
    return f"order-{order.id}"


def create_gateway(settings, order, base_url: str) -> dict:
    """Returns {'id': int, 'link': str} for the payment page of this order."""
    token = order.tracking_token
    data = _call(
        settings,
        "POST",
        "Gateway/",
        {
            "amount": int(round(order.total * 100)),
            "currency": "CHF",
            "referenceId": reference_for(order),
            "validity": 30,
            "successRedirectUrl": f"{base_url}/bestellung/{token}/zahlung?r=ok",
            "failedRedirectUrl": f"{base_url}/bestellung/{token}/zahlung?r=failed",
            "cancelRedirectUrl": f"{base_url}/bestellung/{token}/zahlung?r=cancel",
        },
    )
    return {"id": int(data["id"]), "link": data["link"]}


def gateway_state(settings, order) -> str:
    """'paid' | 'pending' | 'failed' - read from Payrexx, never from redirects/webhooks."""
    if not order.payrexx_gateway_id:
        return "failed"
    data = _call(settings, "GET", f"Gateway/{order.payrexx_gateway_id}/")
    if data.get("referenceId") and data["referenceId"] != reference_for(order):
        return "failed"
    status = data.get("status")
    if status == "confirmed":
        return "paid" if int(data.get("amount", -1)) == int(round(order.total * 100)) else "failed"
    if status in ("waiting", "authorized", "reserved"):
        return "pending"
    return "failed"
