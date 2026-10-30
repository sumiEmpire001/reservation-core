import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from reservation_core import Reservations, CapacityError

A, B, C = "2030-01-01T10:00:00Z", "2030-01-01T11:00:00Z", "2030-01-01T12:00:00Z"


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 0
        self.path = Path(self.tmp.name) / "bookings.db"
        self.service = Reservations(self.path, clock=lambda: self.now)
        self.service.resource("room", 2)

    def hold(self, key="one", start=A, end=B, **options):
        return self.service.hold("room", "owner", start, end, command_key=key, **options)

    def test_disjoint_old_bookings_use_peak_not_total_overlap(self):
        self.hold("first", A, B)
        self.hold("second", B, C)
        self.assertEqual(self.service.availability("room", A, C), 1)
        self.hold("spanning", A, C)
        with self.assertRaises(CapacityError):
            self.hold("too-many", A, C)

    def test_concurrent_holds_never_exceed_capacity(self):
        def attempt(n):
            try:
                return self.hold(str(n))["id"]
            except CapacityError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(attempt, range(20)))
        self.assertEqual(sum(result is not None for result in results), 2)

    def test_cancel_and_repeat_cancel_release_capacity_once(self):
        booking = self.hold(amount=2)
        self.service.change(booking["id"], "owner", "cancel")
        self.service.change(booking["id"], "owner", "cancel")
        self.assertEqual(self.service.availability("room", A, B), 2)

    def test_expiry_boundary_and_confirmation(self):
        booking = self.hold(seconds=2, amount=2)
        self.now = 2
        self.assertEqual(self.service.availability("room", A, B), 2)
        with self.assertRaises(ValueError):
            self.service.change(booking["id"], "owner", "confirm")
        self.assertEqual(self.service.get(booking["id"], "owner")["state"], "expired")

    def test_confirmed_bookings_do_not_expire_with_original_hold(self):
        booking = self.hold(seconds=1)
        self.service.change(booking["id"], "owner", "confirm")
        self.now = 100
        self.assertEqual(self.service.availability("room", A, B), 1)

    def test_owner_checks_cover_inspection_and_mutation(self):
        booking = self.hold()
        for action in ("confirm", "cancel"):
            with self.assertRaises(PermissionError):
                self.service.change(booking["id"], "outsider", action)
        with self.assertRaises(PermissionError):
            self.service.get(booking["id"], "outsider")

    def test_command_keys_are_idempotent_but_conflicts_fail(self):
        first = self.hold()
        self.assertEqual(first["id"], self.hold()["id"])
        with self.assertRaises(ValueError):
            self.hold(amount=2)

    def test_equivalent_timezone_offsets_produce_same_interval(self):
        self.hold()
        self.assertEqual(self.service.availability("room", "2030-01-01T11:00:00+01:00", "2030-01-01T12:00:00+01:00"), 1)

    def test_naive_reversed_and_zero_intervals_are_rejected(self):
        for start, end in (("2030-01-01T10:00:00", B), (B, A), (A, A)):
            with self.assertRaises(ValueError):
                self.hold(start=start, end=end)
        for amount in (0, True, -1):
            with self.assertRaises(ValueError):
                self.hold(amount=amount)

    def test_restart_preserves_state_and_ordered_history(self):
        booking = self.hold()
        self.service.change(booking["id"], "owner", "confirm")
        again = Reservations(self.path)
        self.assertEqual(again.get(booking["id"], "owner")["state"], "confirmed")
        self.assertEqual([r["event"] for r in again.history(booking["id"], "owner")], ["held", "confirmed"])

    def test_adjacent_bookings_do_not_overlap(self):
        self.hold(amount=2)
        self.assertEqual(self.service.availability("room", B, C), 2)

    def test_concurrent_cancellation_is_idempotent(self):
        booking = self.hold(amount=2)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: self.service.change(booking["id"], "owner", "cancel"), range(10)))
        self.assertEqual(self.service.availability("room", A, B), 2)
