from __future__ import annotations


COLORS = {
    "bg": "#101218",
    "panel": "#171A22",
    "panel_alt": "#1D212B",
    "border": "#2A3040",
    "text": "#F2F4F8",
    "muted": "#9DA7BA",
    "accent": "#7C6CFF",
    "accent_hover": "#9185FF",
    "success": "#35C89A",
    "warning": "#F5B942",
    "danger": "#F06A78",
}


def stylesheet() -> str:
    return f"""
    * {{
        font-family: "Malgun Gothic", "Segoe UI";
        font-size: 10pt;
        color: {COLORS['text']};
    }}
    QMainWindow, QDialog, QWidget#AppRoot {{ background: {COLORS['bg']}; }}
    QWidget#Sidebar {{ background: #13161E; border-right: 1px solid {COLORS['border']}; }}
    QWidget#Page {{ background: {COLORS['bg']}; }}
    QFrame#Card, QGroupBox {{
        background: {COLORS['panel']};
        border: 1px solid {COLORS['border']};
        border-radius: 10px;
    }}
    QGroupBox {{ margin-top: 12px; padding-top: 14px; font-weight: 600; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 5px; color: {COLORS['muted']}; }}
    QLabel#PageTitle {{ font-size: 21pt; font-weight: 700; }}
    QLabel#PageSubtitle, QLabel#Muted {{ color: {COLORS['muted']}; }}
    QLabel#Metric {{ font-size: 25pt; font-weight: 700; }}
    QLabel#BadgeSuccess {{ color: {COLORS['success']}; font-weight: 700; }}
    QLabel#BadgeWarning {{ color: {COLORS['warning']}; font-weight: 700; }}
    QLabel#BadgeError {{ color: {COLORS['danger']}; font-weight: 700; }}
    QPushButton {{
        background: {COLORS['panel_alt']};
        border: 1px solid {COLORS['border']};
        border-radius: 7px;
        padding: 8px 13px;
    }}
    QPushButton:hover {{ background: #272C39; border-color: #3B4356; }}
    QPushButton:pressed {{ background: #303647; }}
    QPushButton:disabled {{ color: #626A7B; background: #171A20; }}
    QPushButton#Primary {{ background: {COLORS['accent']}; border-color: {COLORS['accent']}; font-weight: 700; }}
    QPushButton#Primary:hover {{ background: {COLORS['accent_hover']}; }}
    QPushButton#Danger {{ color: {COLORS['danger']}; }}
    QCheckBox {{ spacing: 9px; color: {COLORS['text']}; }}
    QCheckBox::indicator {{
        width: 18px; height: 18px; border-radius: 5px;
        border: 1px solid #566174; background: #10141B;
    }}
    QCheckBox::indicator:hover {{ border-color: #46C2C7; background: #151C24; }}
    QCheckBox::indicator:checked {{
        border: 2px solid #62D5D0; background: #238F93;
    }}
    QCheckBox::indicator:disabled {{ border-color: #343B48; background: #151920; }}
    QPushButton#Nav {{
        background: transparent; border: 0; border-radius: 8px; text-align: left;
        color: {COLORS['muted']}; padding: 11px 14px;
    }}
    QPushButton#Nav:hover {{ background: #1B1F29; color: {COLORS['text']}; }}
    QPushButton#Nav:checked {{ background: #26233D; color: #B9B1FF; font-weight: 700; }}
    QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QKeySequenceEdit, QTableWidget, QListWidget {{
        background: #12151C; border: 1px solid {COLORS['border']}; border-radius: 7px; padding: 7px;
        selection-background-color: {COLORS['accent']};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QTableWidget:focus, QListWidget:focus {{
        border-color: {COLORS['accent']};
    }}
    QComboBox::drop-down {{ border: 0; width: 24px; }}
    QComboBox QAbstractItemView {{
        background: #12151C; border: 1px solid {COLORS['border']};
        selection-background-color: #302B52; padding: 5px;
    }}
    QScrollArea {{ background: transparent; border: 0; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QHeaderView::section {{
        background: {COLORS['panel_alt']}; color: {COLORS['muted']}; border: 0;
        border-bottom: 1px solid {COLORS['border']}; padding: 8px; font-weight: 700;
    }}
    QTableWidget {{ gridline-color: {COLORS['border']}; }}
    QTableWidget::item, QListWidget::item {{ padding: 7px; }}
    QTableWidget::item:selected, QListWidget::item:selected {{ background: #302B52; }}
    QTabWidget::pane {{ border: 1px solid {COLORS['border']}; border-radius: 8px; }}
    QTabBar::tab {{ background: transparent; color: {COLORS['muted']}; padding: 9px 14px; }}
    QTabBar::tab:selected {{ color: {COLORS['text']}; border-bottom: 2px solid {COLORS['accent']}; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: #394154; min-height: 30px; border-radius: 5px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QSplitter::handle {{ background: {COLORS['border']}; width: 1px; height: 1px; }}
    QStatusBar {{ background: #13161E; color: {COLORS['muted']}; border-top: 1px solid {COLORS['border']}; }}
    """
