# Module Direct-Dependant Highlighting

**Date:** 2026-08-16

Selecting a module in the assignment catalogue activates a relationship focus that helps an administrator discover modules which can be applied directly to it.

- A direct dependant is a module whose `requires` array contains the selected module ID.
- Only immediate dependants are highlighted; the relationship is not traversed recursively.
- Matching modules receive a violet relationship treatment and an explicit `Applies directly` badge.
- The active parent and its valid direct dependants are rendered together in a stable relationship group above the remaining catalogue.
- Each matching card states `Requires <selected module name>`.
- Unrelated modules remain in the catalogue but are subdued while focus is active.
- Incompatible or conflicting modules retain the red invalid treatment and are not presented as valid direct applications.
- A notice above the catalogue names the selected base module and provides a `Clear focus` button.
- Relationship focus starts inactive and activates on the first catalogue module click. After activation, normal card clicks only change the details inspector and do not replace or clear the active parent.
- Focus changes only through `Use as parent` in module details, `Clear relationship view`, or selecting another VM.
- Relationship focus does not change assignment data.

Assignment provenance colours and labels remain present. Dependency focus is a temporary browsing aid, not an assignment state.
