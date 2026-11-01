# RestoreLab design read

RestoreLab is a local PostgreSQL recovery training workstation. The user's established preference is a light, readable working application. The dashboard follows one drill from backup to incident to independently restored data.

ENERGY 2 / RHYTHM 2 / MOTION 1. The focal point is the active drill and its current native PostgreSQL phase. A compact header identifies the lab, the main work area contains the drill timeline and comparison, and a separate history list preserves earlier results. No landing-page hero or marketing metrics.

Use warm near-white paper, dark ink and a restrained dark teal action accent. Amber and red indicate running/incident/failure states, and green indicates verified evidence. Every state has a text label; color alone is insufficient. Use the system sans-serif stack, with monospace limited to identifiers, LSNs and digests. Buttons/inputs have 44px targets, visible focus and labels. Mobile stacks the work and history areas; long digests wrap. On tablets, comparison columns fit only when the content has room.

The visual motif is the ordered recovery ledger: phase number, real timestamp, outcome, and concise explanation. Whitespace separates operational decisions from technical evidence. Technical details use disclosure controls instead of dominating the first view.

No new logo, avatar, illustration, external font or icon library is needed. Screenshots must come from actual drills or explicitly labelled UI test fixtures. Final README application shots use the real PostgreSQL drill. Empty, running, interrupted, failed and verified states must remain readable and actionable. There is no custom theme toggle or decorative animation.
