# Design

An authenticated human owns one personal USD microcredit account. No project,
organization membership, machine credential, or hosted Runtime entitlement grants
access to this account. The Cloud terminates the user's JWT and calls PuppyPay
with its existing separate service credential and the verified acting user.

PuppyPay persists checkout identity before calling Polar. Signed paid/refunded
events must match the purchase's customer, product, amount and checkout. Unique
ledger references and account locks prevent duplicate credit or concurrent spend.

Before inference, Cloud validates the text/tool-only request against a fixed model
catalog, reserves a conservative budget and claims execution exactly once. It
stores the provider generation ID before forwarding content. Provider usage,
never a Desktop-supplied cost, settles the account. Expired unstarted reservations
are released; started generations are recovered through authenticated provider
metadata. Unknown outcomes remain held for investigation rather than becoming
free usage. Actual costs above a reservation or subsequent refunds may create
debt, which blocks further admission. Requests are time and size bounded.

Desktop Main retains the login credential. A per-lease random loopback capability
travels only through the existing private worker bootstrap; logout/account changes
revoke it and abort requests. Renderer receives only model metadata and balance.
Prompts and tool results reach the model provider; files and tools remain local.

The feature flag is independent of organization billing flags. Sandbox catalog
and Polar environment must agree. No Runtime billing or hosted Agent is enabled.
