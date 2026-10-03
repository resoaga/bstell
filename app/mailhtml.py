"""Dependency-free HTML for transactional e-mails (inline styles only, because mail
clients ignore <style> and external CSS). The plain-text part stays the fallback.
Every dynamic value is escaped here; callers pass raw text."""

from html import escape

ACCENT_DEFAULT = "#c8102e"
FONT = "Arial,Helvetica,sans-serif"


def _a(accent):
    return escape(accent or ACCENT_DEFAULT, quote=True)


def wrap(shop: str, accent: str, title: str, body_html: str, footer_html: str = "", preheader: str = "") -> str:
    return (
        '<!doctype html><html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        '<body style="margin:0;padding:0;background:#f1eee9;">'
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{escape(preheader)}</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f1eee9;padding:24px 10px;">'
        '<tr><td align="center">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;font-family:{FONT};color:#18130f;">'
        f'<tr><td style="padding:0 4px 12px;font-size:20px;font-weight:bold;color:{_a(accent)};letter-spacing:.2px;">{escape(shop)}</td></tr>'
        '<tr><td style="background:#ffffff;border-radius:14px;overflow:hidden;border:1px solid #e6e0d7;">'
        f'<div style="height:6px;background:{_a(accent)};line-height:6px;font-size:0;">&nbsp;</div>'
        f'<div style="padding:26px 26px 22px;font-size:15px;line-height:1.6;"><h1 style="margin:0 0 14px;font-size:22px;line-height:1.25;">{escape(title)}</h1>{body_html}</div>'
        "</td></tr>"
        f'<tr><td style="padding:18px 6px 4px;color:#7a746c;font-size:12px;line-height:1.6;text-align:center;">{footer_html}</td></tr>'
        "</table></td></tr></table></body></html>"
    )


def button(url: str, label: str, accent: str) -> str:
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:20px 0 8px;"><tr><td style="background:{_a(accent)};border-radius:9px;">'
        f'<a href="{escape(url, quote=True)}" style="display:inline-block;padding:13px 26px;color:#ffffff;text-decoration:none;font-weight:bold;font-size:15px;font-family:{FONT};">{escape(label)}</a>'
        "</td></tr></table>"
    )


def text_link(url: str, label: str, accent: str) -> str:
    return f'<a href="{escape(url, quote=True)}" style="color:{_a(accent)};text-decoration:underline;">{escape(label)}</a>'


def paragraph(text: str, muted: bool = False) -> str:
    color = "#6b6b6b" if muted else "#18130f"
    return f'<p style="margin:0 0 12px;color:{color};">{escape(text)}</p>'


def info_box(rows) -> str:
    """rows: [(label, value)] shown in a light box."""
    inner = "".join(
        f'<tr><td style="padding:3px 0;color:#7a746c;width:38%;vertical-align:top;">{escape(l)}</td>'
        f'<td style="padding:3px 0;vertical-align:top;">{escape(v).replace(chr(10), "<br>")}</td></tr>'
        for l, v in rows if v
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#faf8f5;border-radius:10px;margin:14px 0;font-size:14px;"><tr><td style="padding:12px 16px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{inner}</table></td></tr></table>'
    )


def order_lines(items) -> str:
    """items: [(name, options_text_or_empty, qty, amount_text)] - the name line is bold, options go on a second line."""
    out = '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:6px 0 0;">'
    for name, options, qty, amount in items:
        opt = f'<div style="font-size:13px;color:#7a746c;margin-top:2px;">{escape(options)}</div>' if options else ""
        out += (
            '<tr><td style="padding:10px 0;border-top:1px solid #ebe6de;vertical-align:top;">'
            f'<div style="font-weight:bold;">{int(qty)}× {escape(name)}</div>{opt}</td>'
            f'<td align="right" style="padding:10px 0 10px 12px;border-top:1px solid #ebe6de;vertical-align:top;white-space:nowrap;">{escape(amount)}</td></tr>'
        )
    return out + "</table>"


def totals(rows, grand_label: str, grand_amount: str) -> str:
    out = '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 6px;font-size:14px;">'
    for label, amount in rows:
        out += f'<tr><td style="padding:3px 0;color:#7a746c;">{escape(label)}</td><td align="right" style="padding:3px 0;color:#7a746c;white-space:nowrap;">{escape(amount)}</td></tr>'
    out += (
        f'<tr><td style="padding:10px 0 0;border-top:2px solid #18130f;font-weight:bold;font-size:16px;">{escape(grand_label)}</td>'
        f'<td align="right" style="padding:10px 0 0;border-top:2px solid #18130f;font-weight:bold;font-size:16px;white-space:nowrap;">{escape(grand_amount)}</td></tr></table>'
    )
    return out
