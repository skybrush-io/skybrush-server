from flockwave.protocols.mavlink.dialects.v20 import ardupilotmega as mavlink
from pytest import fixture

from flockwave.server.ext.mavlink.statustext import StatusTextAssembler

from .conftest import FakeClock


@fixture
def assembler(clock: FakeClock) -> StatusTextAssembler:
    return StatusTextAssembler(timeout=1.0, clock=clock)


def make_message(
    text: str, *, id: int = 0, chunk_seq: int = 0, severity: int = 6, system_id=1
):
    mav = mavlink.MAVLink(None, srcSystem=system_id, srcComponent=1)
    msg = mavlink.MAVLink_statustext_message(
        severity, text.encode("utf-8"), id, chunk_seq
    )
    return mav.decode(bytearray(msg.pack(mav)))


def split(text: str, *, id: int, severity: int = 6):
    """Splits a text into chunks like the firmware does. When the length of
    the text is a multiple of 50, the firmware sends an extra all-null chunk
    to mark the end of the message.
    """
    chunks = [text[i : i + 50] for i in range(0, len(text), 50)]
    if len(text) % 50 == 0:
        chunks.append("")
    return [
        make_message(chunk, id=id, chunk_seq=seq, severity=severity)
        for seq, chunk in enumerate(chunks)
    ]


LONG_TEXT = "A" * 50 + "B" * 50 + "C" * 20


def test_single_message(assembler: StatusTextAssembler):
    uav = object()
    result = assembler.add(make_message("hello", severity=3), uav)  # type: ignore
    assert len(result) == 1
    assert result[0].text == "hello"
    assert result[0].severity == 3
    assert result[0].uav is uav


def test_single_chunk_with_nonzero_id(assembler: StatusTextAssembler):
    result = assembler.add(make_message("hello", id=5))
    assert [item.text for item in result] == ["hello"]


def test_chunks_in_order(assembler: StatusTextAssembler):
    first, second, third = split(LONG_TEXT, id=7)
    assert assembler.add(first) == []
    assert assembler.add(second) == []
    result = assembler.add(third)
    assert [item.text for item in result] == [LONG_TEXT]
    assert assembler.flush_expired() == []


def test_chunks_out_of_order(assembler: StatusTextAssembler):
    first, second, third = split(LONG_TEXT, id=7)
    assert assembler.add(third) == []
    assert assembler.add(first) == []
    result = assembler.add(second)
    assert [item.text for item in result] == [LONG_TEXT]


def test_duplicate_chunks_are_ignored(assembler: StatusTextAssembler):
    first, second, third = split(LONG_TEXT, id=7)
    assert assembler.add(first) == []
    assert assembler.add(first) == []
    assert assembler.add(second) == []
    assert [item.text for item in assembler.add(third)] == [LONG_TEXT]


def test_missing_middle_chunk(assembler: StatusTextAssembler, clock: FakeClock):
    first, _, third = split(LONG_TEXT, id=7)
    assert assembler.add(first) == []
    assert assembler.add(third) == []

    clock.now = 0.5
    assert assembler.flush_expired() == []

    clock.now = 1.0
    result = assembler.flush_expired()
    assert [item.text for item in result] == ["A" * 50 + "[...]" + "C" * 20]
    assert assembler.flush_expired() == []


def test_missing_first_chunks(assembler: StatusTextAssembler, clock: FakeClock):
    chunks = split("X" * 150 + "end", id=7)
    assert assembler.add(chunks[2]) == []
    assert assembler.add(chunks[3]) == []

    clock.now = 2
    result = assembler.flush_expired()
    assert [item.text for item in result] == ["[...]" + "X" * 50 + "end"]


def test_missing_last_chunk(assembler: StatusTextAssembler, clock: FakeClock):
    first, second, _ = split(LONG_TEXT, id=7)
    assert assembler.add(first) == []
    assert assembler.add(second) == []

    clock.now = 2
    result = assembler.flush_expired()
    assert [item.text for item in result] == ["A" * 50 + "B" * 50 + "[...]"]


def test_length_is_multiple_of_chunk_size(assembler: StatusTextAssembler):
    text = "A" * 50 + "B" * 50
    first, second, terminator = split(text, id=7)
    assert assembler.add(first) == []
    assert assembler.add(second) == []
    result = assembler.add(terminator)
    assert [item.text for item in result] == [text]
    assert assembler.flush_expired() == []


def test_missing_terminator_chunk(assembler: StatusTextAssembler, clock: FakeClock):
    first, second, _ = split("A" * 50 + "B" * 50, id=7)
    assert assembler.add(first) == []
    assert assembler.add(second) == []

    clock.now = 2
    result = assembler.flush_expired()
    assert [item.text for item in result] == ["A" * 50 + "B" * 50 + "[...]"]


def test_reused_id_flushes_incomplete_message(assembler: StatusTextAssembler):
    first, _, _ = split(LONG_TEXT, id=7)
    assert assembler.add(first) == []

    result = assembler.add(make_message("new message", id=7))
    assert [item.text for item in result] == ["A" * 50 + "[...]", "new message"]


def test_senders_are_kept_apart(assembler: StatusTextAssembler):
    first, second, third = split(LONG_TEXT, id=7)
    other = make_message("Z" * 50, id=7, system_id=2)

    assert assembler.add(first) == []
    assert assembler.add(other) == []
    assert assembler.add(second) == []
    assert [item.text for item in assembler.add(third)] == [LONG_TEXT]
