# Research basis

Checked 2026-10-27. These primary sources support the workflow; device-specific rules still require the selected part's documentation. Recheck online provider capabilities before integrating credentials/APIs.

| Primary source | Use and boundary |
| --- | --- |
| [Altium symbols](https://www.altium.com/documentation/altium-designer/components-libraries/creating-schematic-symbol) | Pin identifiers, electrical types and model links; not manufacturer pinout |
| [Altium footprints](https://www.altium.com/documentation/altium-designer/components-libraries/creating-pcb-footprint) | Native physical models and pad designators; compare actual package drawing |
| [Altium component editor](https://www.altium.com/documentation/altium-designer/components-libraries/single-component-editing) | Custom model mapping; not always one-to-one |
| [Altium project validation](https://www.altium.com/documentation/altium-designer/schematic/design-validation) | Electrical/drafting checks, connection matrix and specific exceptions |
| [Altium stackup](https://www.altium.com/documentation/altium-designer/pcb/defining-layer-stack) | Layers/materials, impedance profiles, vias |
| [Altium PCB / DRC](https://www.altium.com/documentation/altium-designer/pcb#performing-a-design-rule-check) | Enabled rules and online/batch checking |
| [TI SPRACU5 checklist](https://www.ti.com/lit/pdf/spracu5) | Processor power/design-review example; no numeric transfer to Rockchip |
| [ChilaBoard](https://chilaboard.github.io/Altium-Library/) / [repository](https://github.com/chilaboard/Altium-Library) | Public metadata/native artifacts; inspect source license and provenance |
| [Altium Library Loader support](https://www.altium.com/documentation/knowledge-base/altium-designer/altium-library-loader-support) / [SamacSys](https://supplyframe.com/samacsys) | Third-party identity and official flow |
| [SnapMagic plugins](https://www.snapeda.com/plugins/) / [API access](https://www.snapeda.com/get-api/) | Official integration and access request; no public API inferred |
| [SmartSens product list](https://www.smartsenstech.com/en/products_list?products_label=sc) | SC3336 and SC3336P are distinct entries; list is not complete pinout |

## RV1106G3 + SC3336 boundary

A processor header board/camera needs exact processor variant/package, bare sensor versus module, sensor suffix/package and connector/cable mapping. SC3336 and SC3336P are not interchangeable based on a similar name. The SmartSens listing alone cannot support actual pin connections.

This research did not establish a complete authoritative RV1106G3 + SC3336 electrical reference set. Before binding pins obtain applicable manufacturer datasheets, hardware/reference documents and selected module schematic where relevant; record availability/revisions rather than claiming possession. Continue independent requirements, inventory, draft architecture and existing user-file review. Leave unsupported pin maps and device-specific supply/clock/timing values unresolved. Do not emit an invented production circuit to demonstrate this skill.

## Implementation basis

Examples were checked against `eda-agent/src/eda_agent/tools/design.py`, `project.py`, `pcb.py` and `generic.py` in suite source on the same date. Use the live gateway catalog for current names/schemas. Modal, target-save and transport limitations in this skill come from this implementation, distinct from general Altium documentation.
