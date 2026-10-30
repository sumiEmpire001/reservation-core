# Reservation Core

A persistent resource-booking domain service with capacity, expiring holds, confirmation, cancellation, owner checks and ordered history. Built for Lord_Sumi's portfolio.

## Test it

On GitHub: **Actions → Tests and demo → Run workflow → main → Run workflow**. Expand each job's test step and sample scenario. Locally, use Python 3.10+ with no third-party runtime dependencies:

```bash
python -m unittest discover -s tests -v
python demo.py
```

The demo uses a temporary database, a synthetic owner and a fixed clock. It creates a two-unit resource, holds and confirms one unit, then reports the remaining availability.

## Library

```python
from reservation_core import Reservations

service = Reservations("bookings.db")
service.resource("studio", 2)
booking = service.hold("studio", "demo-owner", "2030-01-01T10:00:00Z", "2030-01-01T11:00:00Z", amount=1, command_key="request-001")
service.change(booking["id"], "demo-owner", "confirm")
service.change(booking["id"], "demo-owner", "cancel")
```

## Behavior and architecture

Inputs require timezone-aware ISO datetimes, normalized to instants. Intervals are half-open: a booking ending at 11:00 does not overlap one beginning at 11:00. A sweep of interval boundaries calculates peak concurrent usage; summing all bookings that touch a requested window would wrongly reject some valid bookings.

SQLite write transactions serialize capacity checks and insertion, so concurrent requests cannot independently consume the same capacity. Holds expire after 1..3600 seconds (default 300); expiry is applied lazily during service operations. Confirmed bookings retain their capacity after their initial hold deadline. Cancellation is idempotent. An owner-scoped command key deduplicates an identical request and rejects conflicting reuse. A repeated key returns the original booking, even if it has expired or been cancelled; it does not create a fresh hold.

Bookings and their event records commit together. Inspection and mutations check the supplied owner identifier. **The embedding application must authenticate that identifier**; accepting an owner argument is not a login system. There is no public HTTP service or user interface in this version.

## Verification and limits

Local check on 31 October 2026: **12 tests passed**. Tests cover concurrent booking and cancellation races, peak capacity across disjoint intervals, exact hold expiry, confirmation, owner restrictions, idempotency conflicts, equivalent timezone offsets, adjacent boundaries and restart history. CI also runs the synthetic demo.

Single-host SQLite domain service; no recurring schedules, payment integration, notifications, capacity-editing UI, retention cleanup or distributed locks. New bookings cannot start in the past. Wall-clock changes affect hold deadlines. Callers must provide explicit offsets for ambiguous daylight-saving times. Availability is the minimum free capacity across the entire requested interval, not a time-slot recommendation. Queries load overlapping rows into memory, so high-scale performance remains unbenchmarked.

MIT license; see LICENSE.
