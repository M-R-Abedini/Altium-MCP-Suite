# Electrical and PCB evidence

Apply these prompts to the requested circuit; do not expand every review into a complete board design.

## Intent and calculations

- Define supply range/transients, loads including startup, environment, interfaces, connector mapping and mechanics. Distinguish a bare camera sensor from a powered module.
- For each rail record consumer pins, required range, steady/peak/startup current and source rating. Calculate losses and relevant tolerances. Follow the selected regulator's stability, capacitor/ESR and inductor requirements. Derive sequencing/reset from exact devices; inspect shutdown and IO back-power paths.
- Separate IO domains/direction, open-drain pull-ups, boot straps, reset, clock and programming/recovery. Cite device-specific constraints to datasheet pages.
- Check component stress/temperature. Use analytical calculations first; simulate defined analog/power questions where suitable models exist. A generic SPICE model does not prove processor boot or camera/firmware compatibility.

TI's [processor checklist](https://www.ti.com/lit/pdf/spracu5) illustrates review of rail sizing, ramp/sequencing and capacitor ratings. Numeric requirements apply to its listed TI processors, not RV1106.

## Model acceptance

Compare `physical pin/ball -> datasheet function -> symbol pin -> mapped PCB pad -> intended net` with source revision/page. Include every supply, ground, exposed pad, reserved and NC pin. Record deliberate omissions/unused pins. For connectors establish the actual cable/mating view.

Check package suffix, pin-1 orientation, dimensions, pad pitch and mounting features. A 3D model is mechanical evidence, not electrical numbering. Verify custom pin mapping survives schematic-to-PCB transfer. Altium describes [symbol pin identifiers/electrical types](https://www.altium.com/documentation/altium-designer/components-libraries/creating-schematic-symbol), [footprints](https://www.altium.com/documentation/altium-designer/components-libraries/creating-pcb-footprint) and [custom mapping](https://www.altium.com/documentation/altium-designer/components-libraries/single-component-editing).

## Schematic acceptance

Compare actual compiled membership with the intended nets, repeated supply/ground pins and exact labels. Verify completeness/freshness. Connectivity equivalence cannot detect a wrong datasheet interpretation; review source-backed pin mapping separately.

Use suitable pin electrical types, connection matrix and project reporting. Investigate relevant errors/warnings and document justified exceptions. Altium's [validation guide](https://www.altium.com/documentation/altium-designer/schematic/design-validation) separates electrical and drafting checks. Specific No-ERC exceptions should reflect real intent; do not falsify pin behavior to remove warnings.

## PCB and manufacturing acceptance

Use actual fabricator materials/stackup, copper, width/spacing, drill/via and mask limits. Derive interface impedance and length/skew from selected devices; bind constraints to actual nets/classes. Do not guess MIPI/USB widths from generic boards. Altium's [stack manager](https://www.altium.com/documentation/altium-designer/pcb/defining-layer-stack) covers stack materials, impedance profiles and via definitions.

Check pad mapping, outline, connectors, keepouts, returns, sensitive nodes and decoupling loops. After routing/repour inspect remaining unrouted nets and copper connectivity. Record enabled scope and freshness of DRC. Altium documents [online and batch checks](https://www.altium.com/documentation/altium-designer/pcb#performing-a-design-rule-check); an empty cached result is not comprehensive proof.

For manufacturing output inspect revision consistency, copper/mask/silkscreen, drill/outline/slots, BOM variants and exact MPNs, placement origin/rotation and polarity against actual board-house requirements. These checks do not prove working hardware. For requested build readiness propose relevant rail startup, reset/clock, programming/recovery and camera-acquisition measurements.
