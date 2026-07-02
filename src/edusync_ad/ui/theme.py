"""Thèmes clair / sombre de l'interface (§12)."""

from __future__ import annotations

LIGHT_STYLESHEET = """
QWidget { background-color: #f5f6f8; color: #1c2230; font-size: 13px; }
QMainWindow, QDialog { background-color: #f5f6f8; }
#TopBar { background-color: #ffffff; color: #1c2230; border-bottom: 1px solid #dfe3ea; }
#TopBar QLabel { color: #1c2230; }
#Sidebar { background-color: #ffffff; border-right: 1px solid #dfe3ea; }
QPushButton {
    background-color: #2f6fed; color: white; border: none; border-radius: 4px;
    padding: 6px 14px;
}
QPushButton:hover { background-color: #2557c4; }
QPushButton:disabled { background-color: #b9c4d6; }
QPushButton#SidebarButton {
    background-color: transparent; color: #1c2230; text-align: left; border-radius: 0;
    padding: 10px 16px;
}
QPushButton#SidebarButton:checked { background-color: #e8edfb; color: #2f6fed; font-weight: 600; }
QTableWidget, QTableView { background-color: white; gridline-color: #e2e6ee; }
QLineEdit, QComboBox, QSpinBox { padding: 4px 6px; border: 1px solid #c7cedb; border-radius: 4px; }
"""

DARK_STYLESHEET = """
QWidget { background-color: #1b1f27; color: #e6e8ee; font-size: 13px; }
QMainWindow, QDialog { background-color: #1b1f27; }
#TopBar { background-color: #11151c; color: white; }
#TopBar QLabel { color: white; }
#Sidebar { background-color: #20242e; border-right: 1px solid #2c313d; }
QPushButton {
    background-color: #3a6df0; color: white; border: none; border-radius: 4px;
    padding: 6px 14px;
}
QPushButton:hover { background-color: #4f7df5; }
QPushButton:disabled { background-color: #3a3f4c; }
QPushButton#SidebarButton {
    background-color: transparent; color: #e6e8ee; text-align: left; border-radius: 0;
    padding: 10px 16px;
}
QPushButton#SidebarButton:checked { background-color: #2a3142; color: #7da2ff; font-weight: 600; }
QTableWidget, QTableView { background-color: #20242e; gridline-color: #2c313d; }
QLineEdit, QComboBox, QSpinBox {
    padding: 4px 6px; border: 1px solid #3a3f4c; border-radius: 4px; background-color: #20242e;
}
"""


def stylesheet_for(theme: str) -> str:
    return DARK_STYLESHEET if theme == "sombre" else LIGHT_STYLESHEET


# Couleurs de l'indicateur de connexion (bandeau du haut), adaptées à chaque
# thème pour garder un bon contraste (le vert clair utilisé en sombre est
# illisible sur le fond blanc du thème clair).
STATUS_COLORS_LIGHT = {"connected": "#1f9d55", "connecting": "#b9770e", "disconnected": "#c53030"}
STATUS_COLORS_DARK = {"connected": "#6fe08a", "connecting": "#e0a72b", "disconnected": "#e05555"}


def status_colors_for(theme: str) -> dict[str, str]:
    return STATUS_COLORS_DARK if theme == "sombre" else STATUS_COLORS_LIGHT
