import sys

from flockwave.protocols.mavlink.dialects.v20.common import MAVLink, MAVLinkSigning
from flockwave.server.ext.mavlink.signing import SignatureTimestampSynchronizer


class TestSignatureTimestampSynchronizer:
    def test_wrapper_keeps_instances_separate(self):
        sync = SignatureTimestampSynchronizer()

        signer1 = MAVLinkSigning()
        signer1.link_id = 42

        signer2 = MAVLinkSigning()
        signer2.link_id = 43

        signer1 = sync.wrap(signer1)
        signer2 = sync.wrap(signer2)

        assert signer1.link_id == 42
        assert signer2.link_id == 43

        assert signer1.goodsig_count == 0
        assert signer2.goodsig_count == 0

        signer1.goodsig_count = 42

        assert signer1.goodsig_count == 42
        assert signer2.goodsig_count == 0

    def test_wrapper_keeps_timestamps_synchronized(self):
        sync = SignatureTimestampSynchronizer()
        signer1 = sync.wrap(MAVLinkSigning())
        signer2 = sync.wrap(MAVLinkSigning())

        assert signer1.timestamp == 0
        assert signer2.timestamp == 0

        signer1.timestamp += 5

        assert signer1.timestamp == 5
        assert signer2.timestamp == 5

        signer2.timestamp += 3

        assert signer1.timestamp == 8
        assert signer2.timestamp == 8

    def test_wrapper_does_not_allow_moving_timestamps_backward(self):
        sync = SignatureTimestampSynchronizer()
        signer1 = sync.wrap(MAVLinkSigning())
        signer2 = sync.wrap(MAVLinkSigning())

        signer1.timestamp += 5

        assert signer1.timestamp == 5
        assert signer2.timestamp == 5

        signer2.timestamp -= 3

        assert signer1.timestamp == 5
        assert signer2.timestamp == 5

    def test_wrapper_handles_concurrent_updates(self):
        sync = SignatureTimestampSynchronizer()
        signer1 = sync.wrap(MAVLinkSigning())
        signer2 = sync.wrap(MAVLinkSigning())

        signer1.timestamp += 5

        ts1 = signer1.timestamp
        ts2 = signer2.timestamp

        ts1 += 3
        ts2 += 2

        signer1.timestamp = ts1
        signer2.timestamp = ts2

        assert signer1.timestamp == 8 and signer2.timestamp == 8

        ts1 = signer1.timestamp
        ts2 = signer2.timestamp

        ts1 += 5
        ts2 += 3

        signer2.timestamp = ts2
        signer1.timestamp = ts1

        assert signer1.timestamp == 13 and signer2.timestamp == 13

    def test_wrapper_timestamp_is_updated_when_wrapping(self):
        sync = SignatureTimestampSynchronizer()
        assert sync.timestamp == 0

        signer1 = MAVLinkSigning()
        signer1.timestamp = 1234
        sync.wrap(signer1)

        assert sync.timestamp == 1234

        signer1 = MAVLinkSigning()
        signer1.timestamp = 532
        sync.wrap(signer1)

        assert sync.timestamp == 1234

    def test_patching(self):
        foo = MAVLink(sys.stdout)
        sync = SignatureTimestampSynchronizer()

        signer = foo.signing
        sync.patch(foo)  # type: ignore
        patched_signer = foo.signing

        assert signer is not patched_signer

        signer.link_id = 42
        assert patched_signer.link_id == 42

        patched_signer.timestamp = 84
        assert signer.timestamp == 0
        assert patched_signer.timestamp == 84

        signer.timestamp = 126
        assert signer.timestamp == 126
        assert patched_signer.timestamp == 84

        assert sync.wrap(MAVLinkSigning()).timestamp == 84
