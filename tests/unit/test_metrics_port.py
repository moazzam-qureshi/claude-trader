import socket

from trading_sandwich._metrics_port import allocate_port


def test_allocate_returns_port_in_range_for_known_queue():
    p = allocate_port("features")
    assert 9101 <= p <= 9120


def test_allocate_avoids_occupied_port():
    # Bind 9101 in-process, then ask for a features port — allocator should skip it
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    sock.bind(("0.0.0.0", 9101))
    sock.listen(1)
    try:
        p = allocate_port("features")
        assert p != 9101
        assert 9102 <= p <= 9120
    finally:
        sock.close()


def test_returns_zero_for_unknown_queue():
    assert allocate_port("unknown") == 0
