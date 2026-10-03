"""Small, dependency-free HTML wrapper for transactional e-mails (inline styles only,
because mail clients ignore <style>/external CSS). The plain-text part stays the fallback."""

from html import escape

ACCENT_DEFAULT = "#c8102e"


def wrap(shop: str, accent: str, title: str, inner_html: str, footer: str = "") -> str:
    accent = accent or ACCENT_DEFAULT
    return (
        '<!doctype html><html lang="de"><body style="margin:0;padding:0;background:#f6f4f1;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f6f4f1;padding:18px 10px;">'
        '<tr><td align="center">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#ffffff;border-radius:10px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;color:#18130f;">'
        f'<tr><td style="background:{escape(accent)};color:#ffffff;padding:16px 20px;font-size:18px;font-weight:bold;">{escape(shop)}</td></tr>'
        f'<tr><td style="padding:20px;font-size:15px;line-height:1.5;"><h1 style="margin:0 0 12px;font-size:20px;">{escape(title)}</h1>{inner_html}</td></tr>'
        f'<tr><td style="padding:14px 20px;background:#faf8f5;color:#6b6b6b;font-size:12px;line-height:1.4;">{footer}</td></tr>'
        "</table></td></tr></table></body></html>"
    )


def button(url: str, label: str, accent: str) -> str:
    return (
        f'<p style="margin:18px 0;"><a href="{escape(url)}" style="background:{escape(accent or ACCENT_DEFAULT)};color:#ffffff;'
        f'text-decoration:none;padding:12px 22px;border-radius:8px;font-weight:bold;display:inline-block;">{escape(label)}</a></p>'
    )


def rows(items) -> str:
    """items: [(label, amount_text, bold?)]"""
    out = '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin:10px 0;">'
    for label, amount, bold in items:
        weight = "font-weight:bold;border-top:2px solid #18130f;" if bold else "border-top:1px solid #e3ddd4;"
        out += (
            f'<tr><td style="padding:7px 0;{weight}">{escape(label)}</td>'
            f'<td align="right" style="padding:7px 0;white-space:nowrap;{weight}">{escape(amount)}</td></tr>'
        )
    return out + "</table>"
