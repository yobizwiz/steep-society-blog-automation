"""STEEP-only legacy manual mutations fail before credentials or transport."""
import test_regressions as base
import unittest
from unittest.mock import patch


class ManualGuards(unittest.TestCase):
    def test_fix_dates_stops_before_external_calls(self):
        import fix_publish_dates as target
        with patch.object(target,'load_env') as credentials,patch.object(target,'_gql') as transport:
            with self.assertRaises(base.gate.ReviewRequired):target.main()
            credentials.assert_not_called();transport.assert_not_called()

    def test_collection_fix_stops_before_external_calls(self):
        import collection_fix as target
        with patch.object(target,'load_env') as credentials,patch.object(target,'_gql') as transport:
            with self.assertRaises(base.gate.ReviewRequired):target.main()
            credentials.assert_not_called();transport.assert_not_called()


if __name__=='__main__':unittest.main()
