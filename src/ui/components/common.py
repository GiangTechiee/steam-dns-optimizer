from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QSizePolicy, QStyle, QStyleOptionButton,
    QVBoxLayout, QWidget,
)
from qfluentwidgets import CaptionLabel, ComboBox, MessageBox, TableWidget, ToolTip, ToolTipFilter, ToolTipPosition


COMPACT_BELOW = 780
WIDE_FROM = 1180
MAX_CONTENT_WIDTH = 1140


def elided_text(widget: QWidget, text: str, width: int) -> str:
    return QFontMetrics(widget.font(), widget).elidedText(text, Qt.TextElideMode.ElideRight, max(0, width))


class AdapterComboBox(ComboBox):
    """Keep full Fluent menu entries and GUIDs while fitting the closed button."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        tip(self, "")

    def setText(self, text: str) -> None:
        super().setText(text)
        self.setToolTip(text)
        self.setAccessibleDescription(text)

    def initStyleOption(self, option: QStyleOptionButton) -> None:
        super().initStyleOption(option)
        # The Fluent style reserves the arrow and padding in the content rectangle.
        rect = self.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, self)
        option.text = elided_text(self, option.text, rect.width()).replace("&", "&&")


class _LabelElider(QObject):
    def __init__(self, label: QLabel):
        super().__init__(label)
        self.full_text = label.text()

    def eventFilter(self, label: QLabel, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.Resize, QEvent.Type.FontChange, QEvent.Type.Show):
            text = elided_text(label, self.full_text, label.contentsRect().width())
            if text != label.text():
                label.setText(text)
        return False


def fit_label(label: QLabel) -> QLabel:
    """Fit a static table label to its cell and retain its full accessible text."""
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setAccessibleName(label.text())
    tip(label, label.text())
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    label.installEventFilter(_LabelElider(label))
    return label


def mono_font(size: int = 9) -> QFont:
    font = QFont()
    font.setFamilies(["Cascadia Mono", "Consolas"])
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSize(size)
    return font


def caption(text: str = "", mono: bool = False) -> CaptionLabel:
    label = CaptionLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setTextColor(QColor("#555b65"), QColor("#b5bbc6"))
    if mono:
        label.setFont(mono_font())
    return label


class _HelpToolTipFilter(ToolTipFilter):
    def _canShowToolTip(self) -> bool:
        # A disabled action still needs its name and the reason it is unavailable.
        return bool(self.parent().toolTip())

    def _createToolTip(self) -> ToolTip:
        bubble = ToolTip("", self.parent().window())
        bubble.label.setTextFormat(Qt.TextFormat.PlainText)
        return bubble


def tip(widget: QWidget, text: str) -> None:
    widget.setToolTip(text)
    widget.installEventFilter(_HelpToolTipFilter(widget, showDelay=200, position=ToolTipPosition.TOP))


def page_layout(page: QWidget) -> QVBoxLayout:
    # QBoxLayout centers its sole child once that child reaches its maximum width.
    frame = QHBoxLayout(page)
    frame.setContentsMargins(0, 0, 0, 0)
    content = QWidget()
    content.setMaximumWidth(MAX_CONTENT_WIDTH)
    frame.addWidget(content)
    layout = QVBoxLayout(content)
    layout.setContentsMargins(16, 12, 16, 16)
    layout.setSpacing(12)
    return layout


def make_table(headers: tuple[str, ...]) -> TableWidget:
    table = TableWidget()
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setDefaultSectionSize(54)
    table.verticalHeader().hide()
    table.setBorderVisible(True)
    table.setBorderRadius(8)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.setAlternatingRowColors(False)
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    head = table.horizontalHeader()
    head.setHighlightSections(False)
    head.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    head.setStretchLastSection(False)
    head.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    return table


def cell(widget: QWidget, fill: bool = False) -> QWidget:
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(8, 4, 8, 4)
    layout.setSpacing(0)
    layout.addWidget(widget)
    if not fill:
        layout.addStretch()
    return holder


def text_pair(top: QWidget, bottom: QWidget) -> QWidget:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
    layout.addWidget(top)
    layout.addWidget(bottom)
    return box


def confirm(parent: QWidget, title: str, content: str, action: str) -> bool:
    dialog = MessageBox(title, content, parent.window())
    dialog.yesButton.setText(action)
    dialog.cancelButton.setText("Hủy")
    return bool(dialog.exec())
