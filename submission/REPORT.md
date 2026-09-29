# Báo cáo cá nhân — K4-L3A Day 13 Monitoring & LLMOps

> Mỗi học viên hoàn thiện một file duy nhất này. Khi dẫn evidence, dùng đường dẫn tương đối, ví dụ `evidence/07-trace-waterfall.png`.

## 1. Thông tin học viên

- **Họ và tên:** Nguyễn Tuấn Thành
- **MSSV:** 2A202602640
- **Lớp:** K4-L3A
- **Repository URL:** https://github.com/Chika1357/K4-L3-DAY13-NguyenTuanThanh-2A202602640-Monitoring-LLMOps
- **Commit SHA cuối:**
- **Challenge ID:**
- **Tên project Langfuse cá nhân:** `day13-k4-l3a-2A202602640`

## 2. Evidence index

Điền đúng đường dẫn tới evidence thực tế. Có thể đổi tên hoặc dùng nhiều ảnh nếu cần.

| Evidence | Đường dẫn |
|---|---|
| Baseline CP0 (trước khi sửa) | [`evidence/00-baseline-pytest.txt`](evidence/00-baseline-pytest.txt), [`evidence/00-baseline-validators.txt`](evidence/00-baseline-validators.txt) |
| Pytest cuối | [`evidence/01-pytest.txt`](evidence/01-pytest.txt) |
| Log validator | [`evidence/02-log-validator.txt`](evidence/02-log-validator.txt) |
| Dashboard validator | [`evidence/03-dashboard-validator.txt`](evidence/03-dashboard-validator.txt) |
| Structured log | [`evidence/04-structured-log.txt`](evidence/04-structured-log.txt) |
| PII redaction | [`evidence/05-pii-redaction.txt`](evidence/05-pii-redaction.txt) |
| Trace list | `evidence/06-trace-list.png` |
| Trace waterfall | `evidence/07-trace-waterfall.png` |
| Trace metadata | `evidence/08-trace-metadata.png` |
| Prompt versions | `evidence/09-prompt-versions.png` |
| Prompt rollback | `evidence/10-prompt-rollback.png` |
| Dashboard runtime | `evidence/11-dashboard-overview.png` |
| Incident metric | `evidence/12-incident-metric.png` |
| Incident log | `evidence/13-incident-log.png` |
| Incident trace | `evidence/14-incident-trace.png` |

## 3. Kết quả kỹ thuật

| Nội dung | Baseline | Kết quả cuối | Nhận xét |
|---|---|---|---|
| `validate_logs.py` | 30/100 (41 records; 40 thiếu required fields/enrichment; 0 unique correlation ID) | CP1: 100/100 (22 records, 11 correlation ID, 0 thiếu field) | Baseline: correlation ID = `MISSING`, chưa bind context. Sau CP1 đo trên file log mới (log baseline lưu riêng ở `data/logs.baseline.jsonl`, không commit) |
| `validate_dashboard.py` | HỢP LỆ 6/6 panel (contract) | CP1: HỢP LỆ 6/6 | Mới kiểm tra contract YAML, chưa có dashboard runtime |
| `pytest` | 22 passed | CP1: 35 passed | Thêm test PII (CCCD, thẻ, hộ chiếu, CCCD+thẻ liền nhau) và test correlation/enrichment/không rò context |
| Số traces hợp lệ | 20 root traces (chưa có child observation) | | Chỉ có root `lab-agent-run` |
| Số PII leak | 0 (theo validator) | CP1: 0 | Baseline 0 chỉ vì `summarize_text` tự scrub `payload`; scrubber chưa được đăng ký vào pipeline log |
| Latency P95 / TTFT P95 | 1164 ms / 50 ms (P50 448 ms, P99 1481 ms; 20 requests) | | |
| Retrieval success rate | 100% (20/20) | | |

## 4. Logging và PII

- **Cách tạo/nhận và truyền correlation ID:** `CorrelationIdMiddleware` (`app/middleware.py`) gọi `clear_contextvars()` đầu mỗi request để không rò context của request trước. Nếu client gửi `x-request-id` hợp lệ (chỉ gồm `[A-Za-z0-9._-]`, tối đa 64 ký tự) thì dùng lại; nếu thiếu hoặc không an toàn (có xuống dòng, khoảng trắng, quá dài → chống log injection) thì sinh `req-<8 hex>` từ `uuid4`. ID được `bind_contextvars` nên mọi log trong request tự có `correlation_id`, được lưu vào `request.state` để truyền vào `LabAgent.run` (đưa vào trace metadata), và trả lại qua header `x-request-id` cùng `x-response-time-ms`. Ví dụ: gửi `x-request-id: req-cafe1234` → response header và body đều trả `req-cafe1234`.
- **Các metadata được ghi vào structured log:** `ts` (ISO UTC), `level`, `service`, `event`, `correlation_id`, và context bind trong `/chat` trước log `request_received`: `user_id_hash` (SHA-256 cắt 12 ký tự, không ghi `user_id` thô), `session_id`, `feature`, `model`, `env`. Event `response_sent` có thêm `latency_ms`, `ttft_ms`, `tokens_in`, `tokens_out`, `cost_usd`, `quality_score`, `tool_name`, `tool_success` — đây là nguồn cho dashboard.
- **Cách bảo đảm PII được scrub trước khi ghi:** processor `scrub_event` được đăng ký trong `structlog.configure` ngay sau `format_exc_info` và **trước** `JsonlFileProcessor`/`JSONRenderer`, nên dữ liệu được che trước khi serialize hoặc ghi file. `scrub_event` duyệt đệ quy mọi field chuỗi (payload lồng nhau, context vars, text exception), chỉ bỏ qua các field do hệ thống sinh (`ts`, `level`, `correlation_id`, `user_id_hash` — tránh trường hợp hash toàn chữ số bị nhận nhầm là CCCD). `app/pii.py` có pattern email, thẻ thanh toán, CCCD (12 số), SĐT Việt Nam (`0`/`+84`, có dấu cách/chấm/gạch) và hộ chiếu (`[A-Z]\d{7}`); thẻ/CCCD được xử lý trước SĐT để một đoạn số thẻ không bị gán nhầm là SĐT.
- **Cách kiểm chứng kết quả:** (1) `validate_logs.py` đạt 100/100 trên log mới, 0 PII leak ([`evidence/02-log-validator.txt`](evidence/02-log-validator.txt)); (2) gửi request có PII giả (email, SĐT, CCCD) và chạy workload mẫu (email, SĐT, thẻ) rồi `grep` từng giá trị thô trong `data/logs.jsonl` → 0 kết quả, chỉ còn nhãn `[REDACTED_*]` ([`evidence/05-pii-redaction.txt`](evidence/05-pii-redaction.txt)); (3) test tự động trong `tests/test_pii.py` và `tests/test_correlation_logging.py` kiểm tra format ID, header, propagate ID của client, enrichment, context không rò giữa hai request liên tiếp và PII không có trong file log.

## 5. Tracing và prompt versioning

- **Cách xác nhận traces do chính tôi tạo trong project cá nhân:**
- **Cấu trúc root/retrieval/generation observations:**
- **Cách nối trace với log:**
- **Prompt name:**
- **Version/label baseline:**
- **Version/label candidate:**
- **Trace ID của mỗi version:**
- **Cách promote và rollback `production`:**

## 6. Dashboard, SLO và alerts

- **Dashboard và sáu panel:**
- **SLO và lý do chọn:**
- **Cách tính error budget:**
- **Ba alert và runbook tương ứng:**

## 7. Điều tra challenge

- **Challenge ID:**
- **Khoảng thời gian điều tra:**
- **Triệu chứng từ metrics:**
- **Log line và correlation ID liên quan:**
- **Trace ID và span gây ảnh hưởng:**
- **Root cause:**
- **Fix action:**
- **Preventive measure:**

## 8. Giải thích và tự đánh giá

- **Một quyết định kỹ thuật quan trọng và lý do:**
- **Một lỗi/blocker đã gặp:** (1) CP0: `python -m pytest -q` báo `ModuleNotFoundError: No module named 'structlog'`/`'langfuse'`. (2) CP1: regex thẻ ban đầu `\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}` khớp nhầm khi CCCD đứng ngay trước số thẻ (`001203004567 4111 1111 1111 1111` → khớp `001203004567 4111`), để lộ `1111 1111 1111` của số thẻ trong log — validator không phát hiện vì chuỗi này có dấu cách.
- **Cách tìm nguyên nhân và xử lý:** (1) Traceback cho thấy đang dùng Python toàn cục (`...\Python311\Lib`) thay vì `.venv`; kích hoạt `.\.venv\Scripts\Activate.ps1` ở terminal chạy test là hết lỗi. (2) Test tích hợp gửi nhiều loại PII trong một message đã fail và in ra `message_preview` còn sót số; sửa regex thẻ để bắt buộc cùng một loại ngăn cách giữa các nhóm (`(?P<sep>[- ]?)` + `(?P=sep)`) và thêm test hồi quy `test_scrub_adjacent_cccd_and_card_leaves_no_digits`.
- **Cách hiểu luồng Metrics → Logs → Traces:**
- **Vai trò của prompt version, token/cost, SLO hoặc rollback trong vận hành LLM:**
- **Điều quan trọng nhất đã học:**
- **Hạn chế hoặc phần chưa hoàn thành, nếu có:**

## 9. Checklist trước khi nộp

- [ ] Kết quả và evidence thuộc commit SHA cuối.
- [ ] Tất cả ảnh/output mở được bằng đường dẫn tương đối.
- [ ] Incident evidence nối đúng metric → log → trace.
- [ ] Trace/prompt evidence thuộc project Langfuse cá nhân và ảnh không lộ key/secret.
- [ ] Repository chạy lại được theo README.
- [ ] Không có secret, API key, PII thô hoặc evidence của người khác/lớp khác.
- [ ] URL repo và commit SHA cuối đã được nộp trên LMS/Codelabs.
