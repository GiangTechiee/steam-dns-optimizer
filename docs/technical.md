# Phép đo và dữ liệu cục bộ

## Một lượt quét diễn ra thế nào?

Mỗi nhà cung cấp được thử trên `store.steampowered.com`, `steamcommunity.com`, `login.steampowered.com`, `api.steampowered.com` và `help.steampowered.com`. Scanner gửi truy vấn DNS trực tiếp tới địa chỉ chính của nhà cung cấp, rồi mở kết nối HTTPS đến IP vừa phân giải với tên miền Steam tương ứng (SNI). Trên card chỉ có IPv6, phép đo dùng bản ghi AAAA.

Mỗi cặp DNS và miền có tối đa 2 giây cho DNS và thêm 2,5 giây dùng chung cho các lần thử HTTPS. Nếu IP CDN đầu tiên lỗi, scanner có thể thử IP công cộng kế tiếp trong thời gian còn lại. Phản hồi HTTP hợp lệ như 302, 403 và 404 vẫn xác nhận được đường kết nối HTTPS tới dịch vụ. Lỗi chứng chỉ, dữ liệu rác và kết nối bị ngắt được ghi riêng trong nhật ký.

**Nghi chặn SNI/DPI** xuất hiện khi ít nhất ba máy chủ DNS khác địa chỉ phân giải được cùng một miền bắt buộc (Store hoặc Community), nhưng mọi lần thử HTTPS tới miền đó đều lỗi. Khi tất cả truy vấn DNS lỗi hoặc chỉ trả về IP không dùng được, ứng dụng báo **Không phân giải được tên miền Steam** (`OFFLINE_OR_DNS_BLOCKED`).

## Cấu hình DNS được lưu ở đâu?

Ứng dụng lưu `custom_dns.json` và `dns_backup.json` trong `%LOCALAPPDATA%\SteamDNSOptimizer`. Bản gốc được lưu riêng theo GUID của từng card trước lần đổi đầu tiên. Áp dụng DNS lần thứ hai vẫn giữ bản gốc của lần đầu.

- **Khôi phục cũ** đặt từng họ địa chỉ về trạng thái tự động hoặc DNS tĩnh ban đầu, kể cả DNS nội bộ.
- **Về mặc định (DHCP)** yêu cầu Windows tự nhận DNS. Sau khi đọc lại và xác nhận cấu hình, ứng dụng mới kết thúc chu kỳ sao lưu cho card đó.
- Nếu IPv6 bị tắt và cấu hình IPv6 gốc còn khác, ứng dụng giữ bản sao để có thể khôi phục sau. Nếu thao tác ghi lỗi giữa hai họ địa chỉ, ứng dụng thử hoàn tác rồi đọc lại cấu hình.
- Tệp JSON rỗng hoặc hỏng được giữ nguyên nội dung trong tệp `.bak` trước khi khởi tạo mặc định. Các lần sau dùng `.1.bak`, `.2.bak` nếu cần.
- Trang **Sao lưu & Nhật ký** cho biết DNS gốc của từng card và nhật ký chẩn đoán trong phiên. Khi gửi báo lỗi, hãy xem lại phần nhật ký định chia sẻ vì nó có thể chứa tên card và địa chỉ mạng.

## Kiểm thử và build

Các bài kiểm thử dùng thư mục tạm và giả lập DNS, socket, PowerShell hoặc UAC để chạy ngoại tuyến. Kiểm thử đóng gói nạp DLL từ chính EXE và sẽ chạy khi có bản build một tệp. Để chọn bản EXE cần kiểm tra, đặt biến môi trường `STEAMDNS_TEST_EXE` thành đường dẫn đầy đủ.

Bản phát hành v1254251126 dùng CPython 3.12.13 trên Windows x64. Để cài các phiên bản phụ thuộc của lần build này trong môi trường ảo, chạy lệnh bên dưới, sau đó chạy `.\build.ps1`. [Danh sách phiên bản](build-constraints.txt)

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -c docs/build-constraints.txt
```
