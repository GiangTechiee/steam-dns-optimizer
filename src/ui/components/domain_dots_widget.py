from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from src.core.models import DOMAIN_LABELS, DomainTestDetail
from .common import caption, tip


def domain_tooltip(detail: DomainTestDetail) -> str:
    dns = f"{'OK' if detail.dns_ok else 'Lỗi'} ({detail.dns_ms:.0f} ms)"
    https = "Chưa đo"
    if detail.dns_ok:
        https = "OK" if detail.https_ok else "Lỗi"
        if detail.tls_ms is not None:
            https += f" ({detail.tls_ms:.0f} ms)"
    lines = [detail.domain, f"DNS: {dns}  ·  HTTPS: {https}"]
    if detail.resolved_ip:
        lines.append(detail.resolved_ip)
    if detail.error:
        lines.append(detail.error)
    return "\n".join(lines)


class DomainDotsWidget(QWidget):
    def __init__(self, details: tuple[DomainTestDetail, ...], parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        for short, detail in zip(DOMAIN_LABELS, details):
            item = QWidget()
            column = QVBoxLayout(item)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(0)
            symbol, color = ("✓", "#75dcb4") if detail.https_ok else (
                ("!", "#f6c879") if detail.dns_ok else ("×", "#ff999f")
            )
            dot = QLabel(symbol)
            dot.setFixedSize(16, 16)
            dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            font = dot.font()
            font.setPixelSize(12)
            font.setBold(True)
            dot.setFont(font)
            dot.setStyleSheet(f"background-color: {color}; color: #18232a; border-radius: 8px;")
            label = caption(short)
            label_font = label.font()
            label_font.setPixelSize(10)
            label.setFont(label_font)
            for child in (dot, label):
                child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                column.addWidget(child, 0, Qt.AlignmentFlag.AlignHCenter)
            description = domain_tooltip(detail)
            item.setAccessibleName(description)
            tip(item, description)
            layout.addWidget(item)
