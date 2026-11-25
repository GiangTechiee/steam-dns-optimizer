import argparse
import sys

from src.app_info import APP_ICON, APP_NAME, VERSION


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")
    parser.add_argument("--tray", action="store_true", help="Khởi động trong khay hệ thống")
    parser.add_argument("--handoff-from", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Ứng dụng cần Windows 10 hoặc Windows 11.")

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox
    from src.core.storage import data_directory
    from src.services.app_instance import AppInstance

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(VERSION)
    app.setWindowIcon(QIcon(str(APP_ICON)))
    app.setQuitOnLastWindowClosed(False)
    try:
        instance = AppInstance(data_directory())
        if not instance.start(tray=args.tray, handoff_from=args.handoff_from):
            return 0
    except OSError as exc:
        QMessageBox.critical(None, app.applicationName(), str(exc))
        return 1
    try:
        from qfluentwidgets import Theme, setTheme, setThemeColor
        from src.ui.main_window import MainWindow

        setTheme(Theme.DARK)
        setThemeColor("#3B82F6")
        window = MainWindow()
        window.quitStarted.connect(instance.stop_listening)
        app.aboutToQuit.connect(instance.stop_listening)
        app.aboutToQuit.connect(window.tray.hide)
        if not args.tray or not window.tray.isVisible():
            window.show()
        instance.on_activation(window.open_window)
        try:
            return app.exec()
        finally:
            window.finish_workers()
    finally:
        instance.close()


if __name__ == "__main__":
    raise SystemExit(main())
