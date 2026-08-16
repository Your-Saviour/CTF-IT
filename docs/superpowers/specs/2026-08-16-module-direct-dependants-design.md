# Module Direct-Dependant Highlighting

**Date:** 2026-08-16

Selecting a module in the assignment catalogue activates a relationship focus that helps an administrator discover modules which can be applied directly to it.

- A direct dependant is a module whose `requires` array contains the selected module ID.
- Only immediate dependants are highlighted; the relationship is not traversed recursively.
- Matching modules receive a violet relationship treatment and an explicit `Applies directly` badge.
- Each matching card states `Requires <selected module name>`.
- Unrelated modules remain in the catalogue but are subdued while focus is active.
- Incompatible or conflicting modules retain the red invalid treatment and are not presented as valid direct applications.
- A notice above the catalogue names the selected base module and provides a `Clear focus` button.
- Relationship focus starts inactive, activates on an administrator click, clears when the VM changes, and does not change assignment data.

Assignment provenance colours and labels remain present. Dependency focus is a temporary browsing aid, not an assignment state.
