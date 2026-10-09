# License scope

This repository contains code under several licenses. The root [MIT license](LICENSE)
covers original suite coordination, setup, deployment and tests; it does not
relicense bundled sources or code adapted from them.

| Source | License | Included terms and attribution |
| --- | --- | --- |
| `eda-agent/` | Apache-2.0 | [LICENSE](eda-agent/LICENSE), [NOTICE](eda-agent/NOTICE) |
| `coffeenmusic/` | MIT, except the adaptation below | [LICENSE](coffeenmusic/LICENSE) |
| `altium-designer-mcp/` | GPL-3.0-or-later, as declared in `Cargo.toml` | [LICENCE](altium-designer-mcp/LICENCE) |

`coffeenmusic/server/AltiumScript/json_utils.pas` contains JSON helpers adapted
from `eda-agent/scripts/altium/Main.pas`. Its Apache-2.0 header and attribution
apply to those helpers; keep the linked EDA license and notice with distributions.
File-specific notices take precedence over a directory-level summary.

[UPSTREAM.json](UPSTREAM.json) records the imported upstream commits and license
locations. Those commits identify the original snapshots, not the current suite
revision. Local modifications are recorded in [MODIFICATIONS.md](MODIFICATIONS.md).
Retain upstream copyright notices and mark modified Apache-2.0 files.
The suite deployment includes these license texts, the EDA notice and this scope
document alongside the installed sources.

Changes to the Rust backend remain GPL-3.0-or-later. Distributing that backend,
including binaries, requires meeting its license's source and notice requirements;
the root MIT license does not remove them. Do not copy its implementation into
MIT files and relabel it. Separate processes and stdio alone do not establish
the licensing status of a combined distribution; the
[GNU FAQ](https://www.gnu.org/licenses/gpl-faq.en.html#MereAggregation) also
considers what the components communicate.

Dependencies retain their own licenses. This document identifies the bundled
source licenses; it does not grant rights to Altium Designer or certify every
possible downstream packaging arrangement.
