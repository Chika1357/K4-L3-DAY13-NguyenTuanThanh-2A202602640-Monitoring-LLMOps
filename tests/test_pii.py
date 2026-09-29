from app.pii import hash_user_id, scrub_text, summarize_text


def test_scrub_email() -> None:
    out = scrub_text("Email me at student@vinuni.edu.vn")
    assert "student@" not in out
    assert "REDACTED_EMAIL" in out


def test_scrub_common_vietnamese_phone_formats() -> None:
    phone_numbers = (
        "0901234567",
        "090 123 4567",
        "090.123.4567",
        "090-123-4567",
        "+84 90 123 4567",
    )

    for phone_number in phone_numbers:
        out = scrub_text(f"Contact: {phone_number}")
        assert phone_number not in out
        assert "REDACTED_PHONE_VN" in out


def test_scrub_cccd() -> None:
    out = scrub_text("CCCD của tôi là 001203004567")
    assert "001203004567" not in out
    assert "REDACTED_CCCD" in out


def test_scrub_credit_card_formats() -> None:
    for card in ("4111 1111 1111 1111", "4111-1111-1111-1111", "4111111111111111"):
        out = scrub_text(f"card {card} please")
        assert card not in out
        assert "REDACTED_CREDIT_CARD" in out
        # A card fragment must not be mislabelled as a phone number.
        assert "REDACTED_PHONE_VN" not in out


def test_scrub_adjacent_cccd_and_card_leaves_no_digits() -> None:
    out = scrub_text("001203004567 4111 1111 1111 1111")
    assert out == "[REDACTED_CCCD] [REDACTED_CREDIT_CARD]"


def test_scrub_passport() -> None:
    out = scrub_text("Passport C1234567 expires soon")
    assert "C1234567" not in out
    assert "REDACTED_PASSPORT" in out


def test_scrub_keeps_non_pii_text() -> None:
    text = "How do I debug tail latency at P95 over 3000 ms?"
    assert scrub_text(text) == text


def test_summarize_text_scrubs_and_truncates() -> None:
    out = summarize_text("Here is my phone 0987654321, " + "x" * 200, max_len=40)
    assert "0987654321" not in out
    assert out.endswith("...")


def test_hash_user_id_is_stable_and_not_raw() -> None:
    assert hash_user_id("u01") == hash_user_id("u01")
    assert hash_user_id("u01") != hash_user_id("u02")
    assert "u01" not in hash_user_id("u01")
    assert len(hash_user_id("u01")) == 12
