# Alert và Runbook

Mỗi alert dựa trên triệu chứng người dùng hoặc SLO, không dựa trực tiếp vào tên implementation nội bộ. Rule nằm trong [`../config/alert_rules.yaml`](../config/alert_rules.yaml); nguồn dữ liệu là `data/logs.jsonl`, xem nhanh trên dashboard `http://127.0.0.1:8000/dashboard`.

Quy trình chung cho mọi alert: **Metrics → Logs → Traces**. Xác định khoảng thời gian trên dashboard → lọc log trong khoảng đó lấy `correlation_id` → mở trace trên Langfuse (filter metadata `correlation_id`, hoặc dùng `scripts/find_trace.py`) → so sánh thời gian/level của các span `retrieval`, `prompt-resolve`, `llm-generate`.

## Alert 1

- Tên: `chat_latency_p95_high`
- Severity: P2
- Duration: 5m
- Kênh thông báo: Slack `#day13-l3a-oncall`
- SLI/SLO liên quan: `fast_successful_requests` (99.5% request trả lời thành công trong ≤ 3000 ms, cửa sổ 28 ngày)
- Điều kiện và thời gian duy trì: P95 `latency_ms` của `response_sent` > 3000 ms liên tục 5 phút
- Ảnh hưởng tới người dùng: câu trả lời chậm rõ rệt; mỗi request > 3 s tiêu error budget của SLO
- Ba bước kiểm tra đầu tiên:
  1. Dashboard panel **Latency**: P50 có tăng cùng P95 không (toàn bộ chậm) hay chỉ tail; TTFT P95 có tăng không (chậm ở LLM hay trước LLM).
  2. Lọc log chậm: `python -c "import json; [print(r['correlation_id'], r['latency_ms']) for r in map(json.loads, open('data/logs.jsonl', encoding='utf-8')) if r.get('event')=='response_sent' and r['latency_ms']>3000]"`.
  3. Tra trace bằng `python scripts/find_trace.py <correlation_id>` hoặc filter metadata trong Langfuse: span nào chiếm phần lớn thời gian — `retrieval` (vector store chậm), `prompt-resolve` (Langfuse fetch/cold cache) hay `llm-generate` (provider chậm).
- Mitigation tạm thời: nếu `retrieval` chậm → giảm top-k/timeout retrieval, bật fallback trả lời không dùng context; nếu `prompt-resolve` chậm → kiểm tra kết nối Langfuse, tăng `cache_ttl_seconds`, prompt vẫn fallback về template local; nếu `llm-generate` chậm → chuyển model nhỏ hơn/giảm max tokens. Báo trạng thái trên Slack mỗi 30 phút.
- Owner: Nguyễn Tuấn Thành (on-call)

## Alert 2

- Tên: `chat_error_rate_high`
- Severity: P1
- Duration: 5m
- Kênh thông báo: Slack `#day13-l3a-oncall`
- SLI/SLO liên quan: `fast_successful_requests`; guardrail `error_rate_pct_max: 2`, `retrieval_success_rate_pct_min: 90`
- Điều kiện và thời gian duy trì: `request_failed / request_received × 100` > 2% liên tục 5 phút
- Ảnh hưởng tới người dùng: người dùng nhận HTTP 500, không có câu trả lời; tiêu error budget nhanh nhất
- Ba bước kiểm tra đầu tiên:
  1. Dashboard panel **Errors**: error rate, breakdown `error_type` và retrieval success rate — nếu retrieval success giảm cùng lúc thì lỗi nằm ở tool/retrieval.
  2. Lọc log `request_failed`: xem `error_type`, `tool_name`, `payload.detail` và `correlation_id` (ví dụ `RuntimeError: Vector store timeout`).
  3. Tìm trace trên Langfuse theo metadata `correlation_id`: observation `retrieval` có `level=ERROR` và `status_message` chứa lỗi gốc; kiểm tra có deploy/đổi prompt label gần thời điểm bắt đầu không.
- Mitigation tạm thời: nếu lỗi do retrieval → bật fallback trả lời không có context hoặc trả thông báo thân thiện thay vì 500; nếu do thay đổi mới (prompt/model) → rollback label `production` về version trước (`python scripts/prompt_versions.py set-production <v>`) hoặc rollback deploy.
- Owner: Nguyễn Tuấn Thành (on-call)

## Alert 3

- Tên: `llm_cost_per_request_spike`
- Severity: P3
- Duration: 15m
- Kênh thông báo: Slack `#day13-l3a-llm-cost`
- SLI/SLO liên quan: guardrail `daily_cost_usd_max: 2.5`
- Điều kiện và thời gian duy trì: trung bình `cost_usd` mỗi `response_sent` > 0.005 USD (≈ 2× baseline 0.0022 USD) liên tục 15 phút
- Ảnh hưởng tới người dùng: không lỗi ngay, nhưng vượt ngân sách; câu trả lời thường dài bất thường (khó đọc) và làm tăng latency
- Ba bước kiểm tra đầu tiên:
  1. Dashboard panel **Cost** và **Tokens**: cost tăng do traffic (panel Traffic tăng) hay do cost/request; `tokens_out` hay `tokens_in` tăng.
  2. Lọc log `response_sent` có `cost_usd` cao: so sánh `tokens_in`/`tokens_out`, `feature`, `model` với baseline.
  3. Mở trace: generation `llm-generate` có `usage`/`cost` và prompt version nào — nếu `tokens_in` tăng sau khi đổi prompt label thì do prompt mới; nếu `tokens_out` tăng thì do output không bị giới hạn.
- Mitigation tạm thời: đặt `max_tokens` cho output, rollback prompt label `production` nếu vừa đổi version, chuyển feature ít quan trọng sang model rẻ hơn; theo dõi cumulative cost trên dashboard so với ngưỡng 2.5 USD.
- Owner: Nguyễn Tuấn Thành (on-call)
