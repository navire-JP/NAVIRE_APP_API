import requests
from app.meoles_site.config import meoles_settings


def send_order_confirmation(to_email: str, customer_name: str, items: list, total: int):
    """Envoie un email de confirmation de commande MEOLES via Brevo."""

    items_html = "".join([
        f"""
        <tr>
            <td style="padding:10px 0;font-family:'Courier New',monospace;font-size:14px;color:#1a1a1a;border-bottom:1px solid #f0f0f0;">
                {item['name']}
            </td>
            <td style="padding:10px 0;text-align:center;font-family:'Courier New',monospace;font-size:14px;color:#888;">
                x{item['quantity']}
            </td>
            <td style="padding:10px 0;text-align:right;font-family:'Courier New',monospace;font-size:14px;color:#1a1a1a;">
                {item['subtotal'] / 100:.2f}€
            </td>
        </tr>
        """
        for item in items
    ])

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"></head>
    <body style="margin:0;padding:0;background:#f5f5f0;font-family:sans-serif;">

        <table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f0;padding:40px 20px;">
            <tr><td align="center">
                <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 4px 30px rgba(0,0,0,0.06);">

                    <!-- Header -->
                    <tr>
                        <td style="background:#1a1a1a;padding:35px 40px;text-align:center;">
                            <img src="https://i.imgur.com/ZzoQZxC.png" alt="MEOLES" style="height:28px;width:auto;">
                        </td>
                    </tr>

                    <!-- Corps -->
                    <tr>
                        <td style="padding:40px 40px 30px;">
                            <p style="font-family:'Courier New',monospace;font-size:13px;color:#888;margin:0 0 8px;">
                                Confirmation de commande
                            </p>
                            <h1 style="font-family:Montserrat,sans-serif;font-size:22px;font-weight:700;color:#1a1a1a;margin:0 0 24px;">
                                Merci, {customer_name or "pour votre commande"} ✦
                            </h1>
                            <p style="font-family:'Courier New',monospace;font-size:14px;color:#555;line-height:1.7;margin:0 0 30px;">
                                Votre commande a bien été reçue. Chaque pièce MEOLES est réalisée à la main par fonte à la cire perdue — nous prenons soin de chaque détail avant de vous l'envoyer.
                            </p>

                            <!-- Récap commande -->
                            <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #f0f0f0;border-radius:10px;overflow:hidden;">
                                <tr style="background:#fafafa;">
                                    <td style="padding:12px 15px;font-family:Montserrat,sans-serif;font-size:11px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:1px;">Produit</td>
                                    <td style="padding:12px 15px;text-align:center;font-family:Montserrat,sans-serif;font-size:11px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:1px;">Qté</td>
                                    <td style="padding:12px 15px;text-align:right;font-family:Montserrat,sans-serif;font-size:11px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:1px;">Prix</td>
                                </tr>
                                <tr><td colspan="3" style="padding:0 15px;">
                                    <table width="100%" cellpadding="0" cellspacing="0">
                                        {items_html}
                                    </table>
                                </td></tr>
                                <tr style="background:#fafafa;">
                                    <td colspan="2" style="padding:14px 15px;font-family:Montserrat,sans-serif;font-size:13px;font-weight:700;color:#1a1a1a;">TOTAL</td>
                                    <td style="padding:14px 15px;text-align:right;font-family:Montserrat,sans-serif;font-size:15px;font-weight:700;color:#1a1a1a;">{total / 100:.2f}€</td>
                                </tr>
                            </table>
                        </td>
                    </tr>

                    <!-- Message bas -->
                    <tr>
                        <td style="padding:0 40px 40px;">
                            <p style="font-family:'Courier New',monospace;font-size:13px;color:#888;line-height:1.7;margin:24px 0 0;">
                                Des questions ? Écrivez-nous à <a href="mailto:meoles.contact@gmail.com" style="color:#1a1a1a;">meoles.contact@gmail.com</a>
                            </p>
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td style="background:#1a1a1a;padding:20px 40px;text-align:center;">
                            <p style="font-family:'Courier New',monospace;font-size:11px;color:#888;margin:0;">
                                © 2026 MEOLES — Artisanat français, fonte à la cire perdue
                            </p>
                        </td>
                    </tr>

                </table>
            </td></tr>
        </table>

    </body>
    </html>
    """

    response = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={
            "api-key": meoles_settings.BREVO_API_KEY,
            "Content-Type": "application/json"
        },
        json={
            "sender": {"name": "MEOLES", "email": "meoles.contact@gmail.com"},
            "to": [{"email": to_email}],
            "subject": "✦ Votre commande MEOLES est confirmée",
            "htmlContent": html_content
        }
    )
    return response.status_code == 201
