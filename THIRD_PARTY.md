# Giấy phép và mã nguồn của bản phân phối

Phần mã nguồn, tài liệu và đồ họa do dự án Steam DNS Optimizer cung cấp dùng [giấy phép MIT](LICENSE). Bạn có thể dùng, sửa, bán hoặc phân phối lại phần này khi giữ thông báo bản quyền và giấy phép.

Bản EXE kết hợp ứng dụng với PySide6-Fluent-Widgets. Bản kết hợp được phân phối theo GPLv3. Khi phát hành lại bản kết hợp, hãy giữ các thông báo giấy phép và cung cấp mã nguồn tương ứng theo GPLv3. Các thành phần bên dưới vẫn giữ bản quyền và giấy phép riêng. [Nội dung GPLv3](licenses/GPL-3.0.txt) · [Giấy phép của Fluent Widgets](https://pyqt-fluent-widgets.readthedocs.io/en/latest/license.html)

## Mã nguồn tương ứng của v1254251126

Ứng dụng và quy trình build có trong [tag v1254251126](https://github.com/GiangTechiee/steam-dns-optimizer/tree/v1254251126). Phiên bản các gói Python nằm trong [tệp ràng buộc của bản build](docs/build-constraints.txt). Có thể dùng tham số `-c docs/build-constraints.txt` khi cài `requirements-dev.txt` trên Windows x64 với CPython 3.12.13.

| Thành phần | Mã nguồn đúng phiên bản |
| --- | --- |
| PySide6-Fluent-Widgets 1.11.3 · GPLv3 | [Gói mã nguồn gốc](https://files.pythonhosted.org/packages/be/fa/7ccdf706cf06aa07be142d52606f10c84a0800f9c890faffc097a635c6b1/pyside6_fluent_widgets-1.11.3.tar.gz) |
| PySideSix-Frameless-Window 0.8.2 · LGPLv3 | [Gói mã nguồn gốc](https://files.pythonhosted.org/packages/37/3c/7a564b422b5dd42dcfcf30cff308565ffedd1c6f117433b80854857d48a1/pysidesix_frameless_window-0.8.2.tar.gz) |
| Qt 6.12.0, PySide6 và Shiboken 6.12.0 | [Qt](https://download.qt.io/archive/qt/6.12/6.12.0/single/) · [Qt for Python](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.12.0-src/) |
| CPython 3.12.13 và PyInstaller 6.22.3 | [CPython](https://github.com/python/cpython/tree/v3.12.13) · [PyInstaller](https://pypi.org/project/pyinstaller/6.22.3/#files) |
| dnspython, Pillow, NumPy và SciPy | [dnspython 2.9.2](https://pypi.org/project/dnspython/2.9.2/#files) · [Pillow 13.0.0](https://pypi.org/project/pillow/13.0.0/#files) · [NumPy 2.5.5](https://pypi.org/project/numpy/2.5.5/#files) · [SciPy 1.18.1](https://pypi.org/project/scipy/1.18.1/#files) |
| Thành phần giao diện và tích hợp Windows | [darkdetect 0.8.0](https://pypi.org/project/darkdetect/0.8.0/#files) · [colorthief 0.2.1](https://pypi.org/project/colorthief/0.2.1/#files) · [pywin32 312](https://github.com/mhammond/pywin32/tree/b312) |

Bản EXE mang theo tệp `ThirdPartyNotices.zip` chứa các thông báo giấy phép. Có thể tải riêng tệp này trên trang Release để đọc mà không cần chạy EXE. [Giấy phép LGPLv3](licenses/LGPL-3.0.txt)

Steam là nhãn hiệu của Valve Corporation. Steam DNS Optimizer là dự án độc lập do cộng đồng phát triển.
