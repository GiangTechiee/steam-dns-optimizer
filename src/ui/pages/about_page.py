from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, FluentIcon as FIF, HyperlinkButton, SimpleCardWidget, SubtitleLabel, TitleLabel,
)

from src.app_info import APP_NAME, ASSETS, RELEASED_AT, RELEASE_URL, REPOSITORY_URL, TAG
from src.ui.components.common import caption, page_layout


class AboutPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("about")
        layout = page_layout(self)
        layout.addWidget(TitleLabel("Giới thiệu"))

        card = SimpleCardWidget(self)
        row = QHBoxLayout(card)
        row.setContentsMargins(24, 24, 24, 24)
        row.setSpacing(16)
        logo = QLabel(card)
        logo.setFixedSize(80, 80)
        mark = QPixmap(str(ASSETS / "logo.png"))
        scale = logo.devicePixelRatioF()
        mark = mark.scaled(round(80 * scale), round(80 * scale), Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        mark.setDevicePixelRatio(scale)
        logo.setPixmap(mark)
        row.addWidget(logo)
        identity = QVBoxLayout()
        identity.setSpacing(4)
        identity.addWidget(SubtitleLabel(APP_NAME))
        self.version_label = BodyLabel(f"Phiên bản {TAG}")
        self.version_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        identity.addWidget(self.version_label)
        published = caption(f"Phát hành {RELEASED_AT:%d/%m/%Y} lúc {RELEASED_AT:%H:%M} (UTC+7)")
        published.setWordWrap(True)
        identity.addWidget(published)
        row.addLayout(identity, 1)
        layout.addWidget(card)

        description = BodyLabel("So sánh đường kết nối đến 5 miền Steam qua từng nhà cung cấp DNS. "
                                "Bạn có thể áp dụng DNS đã chọn và khôi phục cấu hình ban đầu ngay trong ứng dụng.")
        description.setWordWrap(True)
        layout.addWidget(description)
        layout.addSpacing(4)
        layout.addWidget(SubtitleLabel("Dự án trên GitHub"))
        self.repo_link = HyperlinkButton(FIF.GITHUB, REPOSITORY_URL, "Xem mã nguồn", self)
        self.release_link = HyperlinkButton(FIF.DOWNLOAD, RELEASE_URL, "Ghi chú phiên bản này", self)
        issues = HyperlinkButton(FIF.FEEDBACK, f"{REPOSITORY_URL}/issues", "Báo lỗi hoặc góp ý", self)
        for link in (self.repo_link, self.release_link, issues):
            link.setToolTip(link.getUrl().toString())
            layout.addWidget(link, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
