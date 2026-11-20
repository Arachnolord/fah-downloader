# ============================================================================
# Erdacht und programmiert von Fabian Hammer.
# (c) 2026 Fabian Hammer - Alle Rechte vorbehalten. Geistiges Eigentum.
# ============================================================================
"""Versionskennung.

Wird im Header der WebGUI angezeigt UND als Cache-Buster an die statischen
Dateien gehaengt (?v=...). Ohne den Cache-Buster liefert der Browser nach
einem Update die alte app.js/style.css aus dem Cache aus -- ein Klassiker,
bei dem ein erfolgreiches Deploy wie ein fehlgeschlagenes aussieht.

Bei jeder Release-ZIP hochzaehlen.
"""
VERSION = "2.5.3"
