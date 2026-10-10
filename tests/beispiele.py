"""Beispieltexte von Rechnungen für die Tests (fiktiv)."""

OPENAI = """OpenAI, LLC
548 Market Street, PMB 97273
San Francisco, California 94104-5401
United States
Invoice
Invoice number 8F3A21-0007
Date of issue September 3, 2026
Date due September 3, 2026
Bill to
Marcel Muster
DE123456789
ChatGPT Plus Sep 3 – Oct 3, 2026  1  $20.00  $20.00
Subtotal $20.00
Total excluding tax $20.00
VAT - Germany (0% on $20.00) $0.00
Total $20.00
Amount paid $20.00
Tax to be paid on reverse charge basis
"""

HOSTING_DE = """Hosting GmbH
Musterstr. 1, 10115 Berlin
USt-IdNr.: DE987654321
Rechnung Nr. R-2026-0912
Rechnungsdatum: 12.09.2026
Server Miete 50,00 €
Summe netto 50,00 €
zzgl. 19 % MwSt. 9,50 €
Gesamtbetrag inkl. MwSt. 59,50 €
"""

AUSGANG_EU = """Marcel Muster, USt-IdNr. DE123456789
Rechnung Nr. 2026-031
Datum: 30.09.2026
An: Acme B.V., Amsterdam, VAT NL853746333B01
Beratung September 8.400,00 EUR
Nettobetrag 8.400,00 EUR
Steuerschuldnerschaft des Leistungsempfängers (Reverse Charge)
Rechnungsbetrag 8.400,00 EUR
"""

AUSGANG_INLAND = """Marcel Muster, Steuernummer 123/456/78901
Rechnung Nr. 2026-030
Rechnungsdatum 15.09.2026
Kunde: Beispiel AG, Köln
Entwicklung 40 Std. x 95,00 € 3.800,00 €
Summe netto 3.800,00 €
Umsatzsteuer 19 % 722,00 €
Rechnungsbetrag 4.522,00 €
"""

EU_SAAS = """Example Software Ltd
Dublin, Ireland, VAT IE9825613N
Invoice date: 2026-09-05
Invoice #INV-55123
Subscription 100.00 EUR
Subtotal 100.00 EUR
VAT reverse charge – Article 196 Council Directive 2006/112/EC
VAT 0% 0.00 EUR
Total 100.00 EUR
"""

# Aufbau wie bei Telekom: Tabelle Netto/Steuer/Brutto mit Fußnote, Abbuchungstag im Fließtext
TELEKOM = """Telekom Deutschland GmbH
Rechnung
Datum 04.09.2026
Rechnungsnummer 123 456 7890
Kundennummer 1234567890
Rechnungsbetrag 16,89 €
Monatliche Beträge
Leistungszeitraum Netto Steuer Brutto
MagentaMobil 08/2026 14,19 € 2,70 €A1 16,89 €
Summe 14,19 € 2,70 € 16,89 €
Steuersatz Netto Steuer Brutto
19 % 14,19 € 2,70 € 16,89 €
14,19 € 2,70 € 16,89 €
A1 = Umsatzsteuer 19 %
Der Rechnungsbetrag wird am 10. September von Ihrem Konto abgebucht.
USt-IdNr. DE 814645262
"""
