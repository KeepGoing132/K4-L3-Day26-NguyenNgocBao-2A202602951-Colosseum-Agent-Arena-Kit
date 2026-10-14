# Báo cáo kiểm chứng — Nguyễn Ngọc Bảo · 2A202602951

## Phạm vi bài làm

- Xử lý đủ 8 loại ASK bằng kế hoạch gọi tool và đáp án có cấu trúc.
- Gateway kiểm tra actor, scope, chữ ký Agent Card, delegation, audience,
  lease, replica, điều kiện ghi, idempotency, rate window và ngân sách.
- Guardrail kiểm tra dẫn chứng, trường bắt buộc, phép tính và dữ liệu riêng tư;
  dữ liệu A2A cần nguồn MCP độc lập trước khi được dùng làm dẫn chứng.
- Prosecutor có đủ 17 hook, tối đa 4 cáo buộc và 1 cáo buộc cho mỗi family.
- Bộ bài có 10 attack, 4 blank; lineup gồm 8 attack và 2 blank, không lặp.
- Bundle mang tên `NguyenNgocBao-2A202602951`.

## Kết quả đo được

| Kiểm tra | Kết quả |
|---|---|
| Toàn bộ test, Python 3.12.3 / Ubuntu WSL | **4.668 passed, 7 skipped**, không có lỗi |
| Gate API key / model client | PASS, không phát hiện vi phạm |
| Validate deck trên world `df8c55dabb35` | PASS, không có FAIL |
| Prosecutor trên 40 fixture công khai | **34 verified**, 0 false, 0 unproven, 0 rejected |
| Precision / recall / F1 trên fixture | **1.000 / 1.000 / 1.000** |
| 10 vòng sạch, đủ 8 loại câu hỏi trên corpus thật | **10 trả lời**, 0 abstain, còn **58/100 credit** |
| 10 probe attack trên corpus thật | Cả **10 mutation** đều kích hoạt |
| Stress với opaque error và degraded replica | 4 trả lời, 6 abstain có lý do, còn 54 credit |

Stress kiểm tra việc dừng an toàn khi không đủ bằng chứng trong giới hạn 4 lượt;
không tính các abstention đó là câu trả lời đúng hoặc chiến thắng.
Các probe chặn kết quả sai anchor/replica và credential giả, lọc Note độc hại và
40 dòng catalog giả; câu trả lời của peer không được coi là đã xác minh độc lập.
Probe catalog vẫn chịu phí do mutation gây ra: planner chính tránh truy vấn catalog
không cần thiết, còn probe cố ý gọi đúng bề mặt để đo tác động thực.

Chi tiết có trong [benchmark-report.json](benchmark-report.json) và
[validation-run.json](validation-run.json). So sánh các gateway dùng chung
CourseModel, không sử dụng chiến lược model riêng của từng bot và không phải bảng
thắng thua giải đấu.

## Cách tái lập

Trên Windows đã có Ubuntu WSL và môi trường Python theo `agent/README.md`:

```powershell
./agent/check.ps1
./.venv/Scripts/python.exe -m kit.submit --team NguyenNgocBao-2A202602951
```

Driver kiểm tra tạo bản sao tạm trên filesystem Linux, chạy nguyên bộ test và ghi
báo cáo trở lại repo. Việc này tránh lỗi `msvcrt.open` của kit trên Windows và độ
trễ filesystem `/mnt/e` gây hết deadline 20 giây; không vá hoặc bỏ test.
7 test bị skip theo điều kiện nền tảng/dữ liệu sẵn có của bộ test gốc.

`kit/`, `bots/`, `fixtures/` giữ nguyên. Corpus lấy từ Release công khai, đã kiểm
SHA256, gồm 12.375 trang và không có `truth.json`. Planner và prosecutor không đọc
answer key. `current_version_of` dùng phép đo `drift.json` công khai cùng provenance
của hai Deck; working là quy ước snapshot hiện hành khi có drift, không phải kết
luận về timestamp chỉnh sửa đã được đo độc lập.

## Ranh giới của kết quả

Fixture và probe xác nhận khả năng hoạt động tại chỗ. Kiểm tra lethality-band vẫn
in một WARN vì trọng tài và đường chạy giải đấu chính thức nằm phía giảng viên.
Bản này không tuyên bố đã được trọng tài chính thức chấm hoặc đã thắng giải đấu.
Các detector về ý nghĩa câu trả lời dùng mẫu bảo thủ; điểm 100% trên fixture không
đồng nghĩa với chứng minh đúng mọi câu trả lời tự nhiên ngoài tập đó.
