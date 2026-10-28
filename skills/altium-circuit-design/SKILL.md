---
name: altium-circuit-design
description: Design or review electronic circuits in Altium Designer using sourced component data, verified library models, structured schematic plans, connectivity checks and PCB constraints through MCP Altium Suite.
---

# Altium Circuit Design

Turn circuit requirements into an editable, traceable Altium project. Match the requested scope: a review, schematic, header board and manufacturing release need different amounts of work. Respond in the user's language.

## Discover the installed suite

Use the `mcp-altium-suite` connection, displayed as **mcp altium suite**. Its lean interface is `suite_status`, `suite_catalog`, and `suite_invoke`; the client may add a server prefix. Do not reconnect the three former MCPs separately.

- `suite_status()` reports backend configuration; it does not prove that Altium or a particular design is ready.
- Discover a capability with `suite_catalog(query="design_execute_plan", backend="eda", with_schema=true)`. Paginate with `limit`/`offset` when required.
- Invoke the exact returned name: `suite_invoke(name="eda__design_execute_plan", arguments={...})`. Namespaces are `eda__`, `legacy__`, `libraries__`, and `cloud__`. Backend guidance may contain old unprefixed names: rediscover and translate them through the suite.
- Read the schema before an unfamiliar call. An exported tool name is not proof of successful execution. Inspect nested results, errors, failure counts and verification fields.

For planning, discover and invoke `eda__design_get_discipline`: `schema` is the current DesignPlan contract and `requirement_schema` is the full DesignRequirement contract. A generic `requirement: object` in the tool catalog does not describe its nested fields. The returned guidance is project context; the user's scope remains authoritative. See [suite-workflow.md](references/suite-workflow.md) for tool choices and native limitations.

## Establish electrical intent

Inspect the explicit target and existing work first. Record actual full paths; the focused editor can belong to another project. For a new design choose a dedicated project folder. Preserve recoverable originals before broad edits and save only the intended project or library.

Capture inputs, outputs, interfaces, operating conditions, power source and mechanical constraints relevant to the requested work. Use existing facts and datasheets before asking questions. Record reasonable noncritical assumptions; ask for missing information that changes electrical correctness or the agreed deliverable while progressing independent work.

For critical ICs, regulators and connectors, record manufacturer, exact orderable part/package, authoritative datasheet revision/page and relevant pin table. Build a pin-to-net table and power tree before drawing. Derive supply range/current headroom, IO compatibility, sequencing/reset/boot requirements and protection from the selected parts. Keep calculations and tolerances reviewable. Do not transfer another processor's rail values or timing to this one. See [engineering-review.md](references/engineering-review.md).

An unavailable authoritative pinout blocks dependent connections, not all work. Mark that block unresolved and continue requirements, library searches or independent blocks. A plausible name, internet summary or similar package is insufficient evidence for pin numbering.

## Resolve symbols and footprints together

Prefer suitable existing verified models. Use cloud search when local inventory lacks a part; see [cloud-libraries.md](references/cloud-libraries.md). Keep source URLs, hashes, license/usage status and validation state with design evidence.

For each exact package compare datasheet numbering/orientation against symbol pins, footprint pads and any custom mapping. Check exposed pads, multiunit/hidden power pins, pitch/body/courtyard and connector mating view. Pad count, provider badges, rendered symbols and matching part names do not establish a correct pin map.

Read models through `libraries__` tools or native inspection before use. Keep downloaded originals intact; make corrections in a project copy and record them. Resolve `needs_creation` parts before execution; never relabel absent models as existing to pass validation. When authoring models use the actual batch tools and verify readback.

## Plan, preview, emit and verify

Construct one coherent DesignPlan with explicit part/library bindings and net-to-pin assignments. Use declared units; convert deliberately between millimetre plans, mil tools and native integers. Keep local signal paths legible and use ports/labels for deliberate cross-block connections.

Until a source-backed part/net block is available, capture an incomplete concept in a requirement/evidence artifact, not an empty or dummy DesignPlan. Never invent entries to satisfy minimum list lengths. In a real plan use `Part.zone` referencing a declared zone, and `Net.force_label` for a justified local-label override; put resolved rationale in the plan summary or evidence notes, not an invented Net comment.

Validate offline, review warnings and preview before native emission. Preview can read Altium libraries on a cache miss. Prefer one `eda__design_execute_plan` using `use_canvas=true`, explicit target and `result_detail="summary"` after models resolve. Retain `details_path`, placements, failures, `net_verification` and timings. For corrections use targeted batches, then recheck affected connectivity.

Compare fresh compiled net-to-pin data with intended membership: missing pins, shorts, opens, accidentally merged rails and intentional NCs. Run schematic validation with correct electrical pin types and project settings. Justify specific exceptions rather than suppressing fault classes. Visual neatness, offline validation and successful emission do not replace native connectivity/ERC evidence.

For requested PCB work establish fabrication capabilities, stackup and interface constraints before routing. Verify schematic-to-PCB mapping/connectivity, placement, returns, decoupling access, mechanics, clearances and unrouted nets. Record actual DRC coverage and unresolved findings. See [engineering-review.md](references/engineering-review.md); cached violations are not a fresh full DRC.

## Deliver useful evidence

Provide editable files plus concise status: completed blocks, exact part/library sources, assumptions, verification results, remaining issues and relevant physical checks. Distinguish a complete schematic, populated PCB, routed PCB, checked fabrication output and tested hardware. Use real timings by phase for speed investigations and avoid per-pin tool loops. Do not promise manufacturing readiness without evidence.

Research sources and the RV1106G3/SC3336 boundary are in [sources.md](references/sources.md). Read only references needed for the current phase.
