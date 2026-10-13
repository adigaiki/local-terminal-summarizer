# Cache incident review: stale prices during a node replacement

At 02:40 on 14 March the night-shift on-call engineer replaced a failing cache
node in the `pricing` cluster. The node had been reporting intermittent
timeouts for about twenty minutes, and the replacement was treated as routine.

During the swap the pricing API served roughly four percent of requests from a
replica that had not yet caught up. For about three and a half minutes some
customers saw prices that were up to an hour old. No orders were placed at the
stale prices: the checkout service re-quotes against the primary before
authorisation, and the seven orders that arrived during the window were priced
from fresh data.

The on-call engineer noticed a small rise in stale-read metrics at 02:52 and
rolled the replica back at 03:05. Error rates were normal by 03:20, and the
stale-read rate returned to its baseline shortly afterwards.

## Impact

Eleven customers viewed a stale price for at least one product. Two support
tickets were filed; both customers were told the price they saw at checkout was
the price charged. No refunds or credits were issued.

## What went wrong

The replacement procedure drained the old node and added the new one, but it
did not wait for the new node's replication lag to fall below the read
threshold before it began serving traffic. The health check reported the node
as ready as soon as it accepted connections, which happens well before its
data is current.

## Follow-up actions

1. Add a staleness check to the readiness probe so a replica is not marked
   ready while its replication lag exceeds 30 seconds.
2. Shorten the cache TTL during node replacements, and document the temporary
   value in the runbook.
3. Make the replacement procedure wait for replication lag explicitly instead
   of relying on the readiness probe.
4. Add a dashboard panel for replication lag next to the stale-read rate, so
   the two are read together during an incident.

The incident is rated low severity: no data was lost, no order was mispriced,
and the rollback was clean. The follow-up actions are owned by the pricing
platform team and are due within two weeks.
