// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
//
// The EasyEDA Pro half of the eda-agent bridge.
//
// EasyEDA cannot be driven from outside: the extension API runs inside
// the editor and reaches out. So this extension dials the eda-agent
// server and serves commands, which is the mirror image of the Altium
// bridge where Altium polls a request directory.
//
// Every API name below is taken from EasyEDA's published reference, not
// from recollection. The instance naming is theirs too: a class such as
// PCB_Drc is reached as eda.pcb_Drc, first three letters lowercased.
// That convention is easy to get wrong and silently yields undefined.
//
// WHAT IS NOT ESTABLISHED: none of this has run inside EasyEDA Pro. The
// Python side reports verified_live false for the same reason. Treat a
// clean load as the first test, not as confirmation.

// Every property EasyEDA assigns on its `eda` object, extracted from
// the constructor in the installed pro-api api.js rather than from a
// convention. Used by the capability probe to ask for each name by
// hand, because a key listing cannot tell a missing class from one
// that is simply not materialised yet.
const KNOWN_EDA_INSTANCES = [
  'dmt_Board', 'dmt_EditorControl', 'dmt_Folder', 'dmt_Panel',
  'dmt_Pcb', 'dmt_Project', 'dmt_Schematic', 'dmt_SelectControl',
  'dmt_Team', 'dmt_Workspace', 'lib_3DModel', 'lib_Cbb',
  'lib_Classification', 'lib_Device', 'lib_Footprint',
  'lib_LibrariesList', 'lib_PanelLibrary', 'lib_SelectControl',
  'lib_Symbol', 'pcb_Document', 'pcb_Drc', 'pcb_Event', 'pcb_Layer',
  'pcb_ManufactureData', 'pcb_MathPolygon', 'pcb_Net', 'pcb_Primitive',
  'pcb_PrimitiveArc', 'pcb_PrimitiveAttribute',
  'pcb_PrimitiveComponent', 'pcb_PrimitiveDimension',
  'pcb_PrimitiveFill', 'pcb_PrimitiveImage', 'pcb_PrimitiveLine',
  'pcb_PrimitiveObject', 'pcb_PrimitivePad', 'pcb_PrimitivePolyline',
  'pcb_PrimitivePour', 'pcb_PrimitivePoured', 'pcb_PrimitiveRegion',
  'pcb_PrimitiveString', 'pcb_PrimitiveVia', 'pcb_RayTracerEngine',
  'pcb_SelectControl', 'pnl_Document', 'sch_Document', 'sch_Drc',
  'sch_Event', 'sch_ManufactureData', 'sch_Net', 'sch_Netlist',
  'sch_Primitive', 'sch_PrimitiveArc', 'sch_PrimitiveAttribute',
  'sch_PrimitiveBus', 'sch_PrimitiveCircle', 'sch_PrimitiveComponent',
  'sch_PrimitiveObject', 'sch_PrimitivePin', 'sch_PrimitivePolygon',
  'sch_PrimitiveRectangle', 'sch_PrimitiveText', 'sch_PrimitiveWire',
  'sch_SelectControl', 'sch_SimulationEngine', 'sch_Utils',
  'sys_ClientUrl', 'sys_Dialog', 'sys_Environment', 'sys_FileManager',
  'sys_FileSystem', 'sys_FontManager', 'sys_FormatConversion',
  'sys_HeaderMenu', 'sys_I18n', 'sys_IFrame',
  'sys_LoadingAndProgressBar', 'sys_Log', 'sys_Message',
  'sys_MessageBox', 'sys_MessageBus', 'sys_PanelControl',
  'sys_RightClickMenu', 'sys_Setting', 'sys_ShortcutKey', 'sys_Storage',
  'sys_Timer', 'sys_ToastMessage', 'sys_Tool', 'sys_Unit',
  'sys_WebSocket', 'sys_Window',
];

//: The socket identity, renewed on every attach.
//:
//: sys_WebSocket.register takes an id, and reusing an id that has
//: already been registered does not reliably establish a new socket.
//: Reconnection after the server restarts therefore fails silently:
//: the close succeeds, register returns, and no connected callback
//: ever arrives.
//:
//: Each attach takes a fresh id and the previous one is closed
//: explicitly, so a reconnect is always a registration the runtime has
//: not seen before.
const WS_ID_BASE = 'eda-agent';
let wsSerial = 0;
let WS_ID = WS_ID_BASE;

//: How many times attach() has been entered since load.
//:
//: Counted separately from wsSerial because they fail apart: attach can
//: return before opening anything, so a rising attach count with a flat
//: socket serial says the retry loop is running and giving up before it
//: reaches the socket.
let attachAttempts = 0;

//: Whether an attach is already in progress. See attach() for why two
//: concurrent scans are fatal rather than merely wasteful.
let attaching = false;
//: When the in-flight attach began, so a stalled one can expire.
let attachingSince = 0;
//: How long an attach may hold the in-flight flag before another
//: is allowed to take over. Longer than a full port walk, short
//: enough that a wedged attach costs one retry window rather than
//: the rest of the session.
const ATTACH_STALL_MS = 30000;

//: When the retry tick last ran, so two armed timers cannot advance the
//: idle counter twice per interval.
let lastTickAt = 0;

//: What the last attach actually observed, in words, for the editor.
//:
//: WHY THIS EXISTS IN THE EDITOR AND NOT ONLY OVER THE WIRE. Every
//: other diagnostic here is reported in the ping reply, which is
//: readable exactly when a connection already works, and therefore
//: useless for the one question anybody asks: why is it not connected?
//: The operator sits in front of the editor with no console and no way
//: to see a scan that fails in silence.
//:
//: So the outcome of each scan is kept here and shown from the menu.
let lastOutcome = 'not attempted yet';
let lastOutcomeAt = 0;

//: What each port said when asked, kept from the last health scan.
//:
//: The entry that matters is a port that answers /health with SOME
//: OTHER service. Our range and path (49620-49629, /eda) are the ones
//: EasyEDA's own bridge server uses, so a port answering `easyeda-bridge`
//: means the official gateway is running there and the two are
//: competing for the same range. That is invisible from a bare "no
//: server found" and is the first thing worth ruling out.
let lastProbeNotes = [];

//: When a failure was last announced, so a 5 second retry loop cannot
//: bury the editor in toasts. The state is available on demand from the
//: menu; the toast only marks the transitions.
let lastFailureToastAt = 0;
//: How long to leave between unsolicited failure toasts.
const FAILURE_TOAST_MS = 60000;

function renewSocketId() {
  const previous = WS_ID;
  wsSerial += 1;
  WS_ID = `${WS_ID_BASE}-${wsSerial}`;
  return previous;
}

// Replaced by build.py with a hash of this file. An extension that is
// installed, enabled and MONTHS OLD looks exactly like a current one in
// the Extensions Manager: same name, same uuid, and a size nobody
// checks. That ambiguity cost a long time once. Reporting the build
// over the wire makes "is the editor running this code?" a question
// with an answer.
//
// Left as 'dev' in the repo so main.js stays loadable on its own, which
// is how the harnesses import it.
const BUILD_ID = 'dev';

// One handler per command. The Python side sends {id, command, params}
// and expects {id, result} or {id, error}. Keeping the envelope in one
// place means a new command cannot invent its own reply shape.
//: How long any ONE handler may take before the caller is told it did
//: not answer.
//:
//: Measured over 91 timed calls on a live 111-component board: the
//: slowest individual handler that succeeded took 0.35s, so 15s is a
//: factor of forty clear of anything observed to work. Every hang was
//: unbounded rather than merely slow, which is what makes a ceiling
//: safe here: there is no middle ground of commands that finish in
//: twenty seconds.
//:
//: The two calls in that sample that took a full minute were
//: easyeda_review_snapshot and easyeda_review_board, and neither is a
//: handler. They are Python-side aggregators that issue dozens of
//: editor commands, so their minute is a sum of sub-calls each of
//: which is separately subject to this ceiling. Reading their total as
//: a handler duration would argue for a timeout four times longer than
//: anything needs.
//: How long an export may take. Rendering a board is not a read and
//: cannot be held to a read's budget.
const EXPORT_TIMEOUT_MS = Number(
  (typeof process !== 'undefined' && process.env
    && process.env.EDA_EXPORT_TIMEOUT_MS) || 120000);

const HANDLER_TIMEOUT_MS = Number(
  (typeof process !== 'undefined' && process.env
    && process.env.EDA_HANDLER_TIMEOUT_MS) || 15000);

const handlers = {};

handlers['system.ping'] = async () => ({
  pong: true,
  api: 'easyeda-pro',
  // Which build of this extension is actually loaded. The smoke script
  // recomputes the same id from main.js and reports loudly when they
  // differ, which is the only cheap way to tell a months-old install
  // from a current one: EasyEDA's Extensions Manager shows the same
  // name, uuid and size either way.
  build: BUILD_ID,
  // Reported rather than assumed, so the Python side can tell which
  // document the answers refer to.
  document: await currentDocumentKind(),
  // Why the extension did or did not reconnect on its own.
  //
  // Auto-reconnect has now been wrong twice, both times because it was
  // built on an assumption about an API with no way to observe it: a
  // heartbeat that never detected a dead socket, then an idle reattach
  // that did not fire when it should have. Neither could be diagnosed
  // from outside, because the only symptom is a connection that is
  // not there.
  //
  // These fields cost nothing and turn the next connection into the
  // measurement. timer_kind says whether a retry loop is armed at all
  // and which timer API it got: null means startInterval found
  // neither, which would explain silence completely.
  retry: {
    timer_kind: retryTimer ? retryTimer.kind : null,
    idle_ticks: idleTicks,
    idle_limit: IDLE_REATTACH_TICKS,
    retry_ms: RETRY_MS,
    believes_connected: connected,
    // How many times a socket has been opened since the extension
    // loaded, and the id currently registered.
    //
    // idle_ticks cannot answer the question that matters, because the
    // receive callback zeroes it before this handler runs, so a ping
    // always reads zero however long the link sat idle. This counter
    // is not touched by receiving, so it survives to be read.
    //
    // It separates the two failures that look identical from outside.
    // After the server has been away and come back: a serial that has
    // CLIMBED means the retry loop ran and every connection attempt
    // failed, while a serial that has NOT MOVED means the loop never
    // fired at all. The fixes point in opposite directions.
    socket_serial: wsSerial,
    socket_id: WS_ID,
    attach_attempts: attachAttempts,
  },
});

// A generic call into the editor API, so new capability stops costing
// an extension re-import.
//
// THE PROBLEM THIS SOLVES. Every capability used to live in this file,
// so each new command meant new extension code, and EasyEDA installs
// BY VERSION: importing at a version already installed is a silent
// no-op. Adding one read therefore cost a version bump, a rebuild, a
// manual import and a reconnect, and getting any step wrong left the
// editor running old code while everything looked fine.
//
// With this, the Python side composes commands out of one primitive
// and this file stops changing. The existing named handlers stay
// exactly as they are: they are proven, several do real work beyond a
// single call, and replacing them wholesale would trade a friction
// problem for a correctness one.
//
// DESTRUCTIVE METHODS STILL NEED CONFIRM. Without this the shim would
// be a hole straight through every guard in the file: proj.delete_pcb
// asks for confirmation, and eda.dmt_Pcb.deletePcb through a generic
// invoke would not. The check is on the METHOD NAME because that is
// what a caller reaches for, and it is deliberately broad.
const DESTRUCTIVE_METHOD =
  /^(delete|remove|clear|destroy|reset|overwrite)/i;

// Methods that replace existing content wholesale without a name that
// says so. Listed one by one rather than by widening the prefixes
// above, and that restraint is the point: `set` and `import` cover
// dozens of harmless calls, and a guard that fires on setVisible
// teaches a caller to pass confirm=true reflexively, which is worse
// than having no guard at all.
//
// Found by enumerating all 675 methods the runtime exposes across its
// 92 classes and reading the ones whose names imply replacement. The
// prefix list alone missed every entry here.
//
//   setNetlist              replaces the whole connectivity
//   importAutoRoute*        replaces all routing
//   modify*Net / modify*Name  re-scope or orphan an existing constraint
const DESTRUCTIVE_EXACT = [
  'setNetlist',
  'importAutoRouteSesFile',
  'importAutoRouteJsonFile',
  // Replaces the PLACEMENT of the whole board, which is at least as
  // destructive as replacing its routing, and it sat unguarded beside
  // the two that were. Found by reading PCB_Document's import family
  // in the official reference rather than by anything going wrong.
  'importAutoLayoutJsonFile',
  // The automatic engines, none of which reads as destructive.
  // pcb_Document.autoRouting was guarded only by the easyeda_auto_route
  // TOOL; the generic shim reaches the same method and did not check,
  // so the documented guarantee held for one route in and not the other.
  'autoRouting',
  'autoLayout',
  'restoreDefault',
  // Constraint edits that read as harmless. Repointing a differential
  // pair onto another net silently re-scopes whatever is already routed
  // under it, and renaming a class or group orphans every rule that
  // referenced the old name. Neither carries a destructive-sounding
  // verb, so the prefix test above never sees them.
  'modifyDifferentialPairPositiveNet',
  'modifyDifferentialPairNegativeNet',
  'modifyDifferentialPairName',
  'modifyNetClassName',
  'modifyEqualLengthNetGroupName',
];

function looksDestructive(method) {
  const name = String(method || '');
  return DESTRUCTIVE_METHOD.test(name)
    || DESTRUCTIVE_EXACT.indexOf(name) !== -1;
}

function resolveApi(className, method) {
  if (typeof className !== 'string' || !className) {
    throw new Error('class_name is required');
  }
  if (typeof method !== 'string' || !method) {
    throw new Error('method is required');
  }
  const api = eda[className];
  if (!api) {
    throw new Error(
      `${className} is not present in this runtime. EasyEDA injects a `
      + 'different API surface per document type; call '
      + 'system.capabilities to see what is here.');
  }
  const fn = api[method];
  if (typeof fn !== 'function') {
    throw new Error(`${className}.${method} is not a function`);
  }
  return { api: api, fn: fn };
}

async function invokeOne(spec) {
  const className = spec.class_name;
  const method = spec.method;
  if (looksDestructive(method) && spec.confirm !== true) {
    throw new Error(
      `${className}.${method} looks destructive. Pass confirm=true if `
      + 'that is intended.');
  }
  const resolved = resolveApi(className, method);
  const args = Array.isArray(spec.args) ? spec.args : [];
  const value = await resolved.fn.apply(resolved.api, args);
  // The value is returned as-is, including null and false. Those are
  // how this API declines, and six handlers once reported work they
  // had not done by treating a falsey answer as success.
  return { class_name: className, method: method, value: value };
}

// The fields are read out here rather than passing params straight
// through. A handler that forwards the whole object hides which
// parameters it actually uses, from a reader and from the contract
// guard that checks the two sides agree.
handlers['system.invoke'] = async (params) => invokeOne({
  class_name: params.class_name,
  method: params.method,
  args: params.args,
  confirm: params.confirm,
});

handlers['system.batch'] = async (params) => {
  const calls = Array.isArray(params.calls) ? params.calls : [];
  if (!calls.length) throw new Error('calls must not be empty');
  // Each result is reported individually, in order. One failure in the
  // middle must not lose the answers either side of it: a partial
  // result that says which part failed is usable, and an exception is
  // not.
  // EACH CALL GETS ITS OWN CLOCK.
  //
  // Catching a throw per call is not enough: one
  // call that HANGS takes the whole batch past the dispatcher's
  // ceiling, and every result either side of it is lost. A batch of
  // three probes returned nothing at all because one of them was a
  // read already known to stall.
  //
  // That is the exact failure the per-call reporting exists to
  // prevent, so the budget is per call: a staller is recorded as
  // failed and the rest still run.
  const PER_CALL_MS = Math.max(
    1000, Math.floor(HANDLER_TIMEOUT_MS / Math.max(1, calls.length)));
  const out = [];
  for (let i = 0; i < calls.length; i += 1) {
    const spec = calls[i] || {};
    let timer = null;
    try {
      out.push(await Promise.race([
        invokeOne(spec),
        new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error(
            `did not answer within ${PER_CALL_MS}ms; the call was `
            + 'accepted and never returned')), PER_CALL_MS);
        }),
      ]).finally(() => { if (timer !== null) clearTimeout(timer); }));
    } catch (e) {
      out.push({
        class_name: spec.class_name,
        method: spec.method,
        failed: String((e && e.message) || e),
      });
    }
  }
  return { results: out, count: out.length,
           failed: out.filter((r) => r.failed !== undefined).length };
};

handlers['system.capabilities'] = async () => {
  // What the editor ACTUALLY injected, in this context, right now.
  //
  // EasyEDA loads its API per document type: its own pro-api manifest
  // declares separate services for default, sch, symbol, pcb and panel.
  // On the start page only the reduced default surface exists, so every
  // pcb_* and sch_* class is undefined and sixty-four read commands
  // fail with "Cannot read properties of undefined". Those failures
  // read as sixty-four bugs in the caller. They are not.
  //
  // One call answers what sixty-four probes only hint at: which classes
  // exist here, and what each one can do.
  // Enumerating Object.keys(eda) is NOT enough on its own. The first
  // live run reported exactly the six classes this file had already
  // touched, which is what a lazily-materialised object looks like as
  // well as what a restricted one looks like. Those need different
  // fixes, so the probe asks for each known name by hand too and
  // reports which way each one answers.
  const known = KNOWN_EDA_INSTANCES;
  const enumerated = Object.keys(eda);
  const probed = {};
  for (const name of known) {
    let present = false;
    try {
      present = eda[name] !== undefined && eda[name] !== null;
    } catch (e) { present = false; }
    probed[name] = present;
  }

  const classes = {};
  for (const name of Array.from(new Set(
      [...enumerated, ...known.filter((n) => probed[n])])).sort()) {
    const instance = eda[name];
    if (!instance || typeof instance !== 'object') continue;
    const methods = new Set();
    for (const key of Object.getOwnPropertyNames(instance)) {
      if (typeof instance[key] === 'function') methods.add(key);
    }
    // Instance methods usually live on the prototype, not the object.
    const proto = Object.getPrototypeOf(instance);
    if (proto && proto !== Object.prototype) {
      for (const key of Object.getOwnPropertyNames(proto)) {
        if (key === 'constructor') continue;
        try {
          if (typeof instance[key] === 'function') methods.add(key);
        } catch (e) { /* a getter that throws is not a method */ }
      }
    }
    classes[name] = Array.from(methods).sort();
  }
  return {
    document: await currentDocumentKind(),
    class_count: Object.keys(classes).length,
    classes,
    // The two lists differ when `eda` materialises a property only once
    // it is asked for. A name that is absent from `enumerated` and true
    // in `probed` was there all along and simply did not show up in a
    // key listing.
    enumerated: enumerated.slice().sort(),
    probed_present: known.filter((n) => probed[n]),
    probed_absent: known.filter((n) => !probed[n]),
    // EasyEDA's own in-app code reaches the full API through
    // `window._EXTAPI_ROOT_` (pro-ui does exactly `this.eda =
    // window._EXTAPI_ROOT_`). If the object an extension is handed is a
    // reduced one, that root may still hold the rest.
    //
    // Reported, not used. This says whether the root is reachable and
    // what it carries; moving call sites onto it should follow that
    // answer rather than an assumption.
    extapi_root: (() => {
      const out = { reachable: false, where: null, count: 0, sample: [] };
      const candidates = [
        ['globalThis', typeof globalThis !== 'undefined' ? globalThis : null],
        ['window', typeof window !== 'undefined' ? window : null],
        ['window.top',
         (typeof window !== 'undefined' && window.top) ? window.top : null],
      ];
      for (const [where, scope] of candidates) {
        if (!scope) continue;
        let root = null;
        try { root = scope._EXTAPI_ROOT_; } catch (e) { root = null; }
        if (!root || typeof root !== 'object') continue;
        const keys = Object.keys(root);
        const present = known.filter((n) => {
          try { return root[n] !== undefined && root[n] !== null; }
          catch (e) { return false; }
        });
        out.reachable = true;
        out.where = where;
        out.count = present.length;
        out.sample = present.slice(0, 12);
        out.enumerated_count = keys.length;
        break;
      }
      return out;
    })(),
  };
};

async function currentDocumentKind() {
  // A command aimed at the PCB is meaningless on a schematic tab, and
  // finding that out from a confusing error is worse than being told.
  try {
    const pcb = await eda.dmt_Pcb.getCurrentPcbInfo();
    if (pcb) return 'pcb';
  } catch (e) { /* not a PCB tab */ }
  try {
    const sch = await eda.dmt_Schematic.getCurrentSchematicInfo();
    if (sch) return 'schematic';
  } catch (e) { /* not a schematic tab */ }
  return 'unknown';
}

handlers['design.snapshot'] = async () => {
  const components = (await eda.pcb_PrimitiveComponent.getAll()) || [];
  const parts = [];
  const pins = [];
  const unreadable = [];

  for (const component of components) {
    const designator =
      component.designator || component.name || component.primitiveId;
    // A component has no `footprintName` and no `value`. `footprint`
    // and `component` are objects of the form
    // {libraryUuid, uuid, name}, so reading them as strings yields an
    // empty value for every part and leaves a footprint review with
    // nothing to examine and no way to say so.
    const footprintName =
      (component.footprint && component.footprint.name) ||
      component.footprintName || '';
    const deviceName =
      (component.component && component.component.name) || '';
    // The per-part parameters live in otherProperty. Its INNER shape is
    // not yet measured, so this reads the conventional keys and falls
    // back to the device name rather than inventing a structure.
    const props = component.otherProperty;
    const value =
      (props && typeof props === 'object' &&
        (props.Value || props.value || props.Comment)) ||
      component.value || deviceName || '';
    parts.push({
      designator: designator,
      footprint: footprintName,
      device: deviceName,
      value: value,
      layer: component.layer,
      x: component.x,
      y: component.y,
      rotation: component.rotation,
      // Whether the part belongs on the BOM. Measured on a live board
      // as a boolean. Passed through UNTOUCHED rather than defaulted,
      // because a reader has to be able to tell "ticked off the BOM"
      // from "this component did not say", and the two call for
      // opposite handling: the first excludes a part from a purchase
      // order, the second must never be allowed to.
      addIntoBom: component.addIntoBom,
    });

    // Pins come per component; a flat pad list would lose which part
    // each pad belongs to, and the snapshot is built from that pairing.
    let pads = [];
    try {
      pads =
        (await eda.pcb_PrimitiveComponent.getAllPinsByPrimitiveId(
          component.primitiveId,
        )) || [];
    } catch (e) {
      // A read failure is NOT a component without pins.
      //
      // Swallowing this put the part in the snapshot with no pads at
      // all, and the review engine reads that as a design fact: either
      // an unconnected part, or nothing to check because there is
      // nothing there. The snapshot is what every EDA-agnostic check is
      // built on, so a silent [] here becomes a silent wrong answer
      // several layers away from the cause.
      pads = [];
      unreadable.push({ designator: designator, error: String(e) });
    }
    for (const pad of pads) {
      pins.push({
        designator: designator,
        // padNumber FIRST because it is the measured name: every one of
        // 354 pads on a live board reported `padNumber`, and neither
        // `number` nor `pinNumber` appeared on any of them. With those
        // two alone every pin in the snapshot carried an empty number,
        // so nothing built on it could say WHICH pin a net reached.
        // The other two stay as fallbacks: the per-component pin call
        // is a different accessor from the flat pad list, and its shape
        // is not separately measured yet.
        pin: pad.padNumber || pad.number || pad.pinNumber || '',
        net: pad.net || '',
      });
    }
  }

  const unconnected = pins.filter((p) => !p.net).length;

  const out = {
    board_name: await boardName(),
    parts: parts,
    pins: pins,
    unconnected_pads: unconnected,
    stats: { footprints: parts.length, pads: pins.length },
  };
  if (unreadable.length) {
    out.components_without_readable_pins = unreadable;
    out.pins_incomplete = true;
    out.warning =
      `${unreadable.length} component(s) would not report their pins, so `
      + 'this snapshot understates the connectivity. They appear here '
      + 'with no pads, which is NOT the same as having none.';
  }
  return out;
};

async function boardName() {
  try {
    const info = await eda.dmt_Pcb.getCurrentPcbInfo();
    return (info && (info.name || info.title)) || '';
  } catch (e) {
    return '';
  }
}

// ASK FOR THE VIOLATION LIST. check() is overloaded on its third
// argument: with includeVerboseError false it returns Promise<boolean>,
// and with it true the return is ALWAYS an array. Called with no
// arguments at all, as this did, the boolean overload is what answers,
// which is why the checker appeared to report a bare status and why
// reportProblem below had to refuse it. The boolean was our own call
// selecting the wrong overload, not the editor declining to explain
// itself.
//
// strict=true asks for the full rule set rather than a reduced pass.
// userInterface=false keeps the editor from opening its own results
// panel, since this call answers a tool rather than a person.
const DRC_ARGS = [true, false, true];

handlers['design.run_drc'] = async () => {
  // The editor's own checker. This project does not reimplement an EDA
  // tool's rules: a second opinion that disagrees is worse than none.
  const report = await eda.pcb_Drc.check(
    DRC_ARGS[0], DRC_ARGS[1], DRC_ARGS[2]);
  // NOTHING and NO VIOLATIONS are different answers.
  //
  // normaliseViolations turns a null report into an empty list, so a
  // checker that did not run reported violation_count: 0 - a clean
  // board, on the last check before somebody orders one. The shape
  // handling below was written to avoid exactly that and only covered
  // the case where the report EXISTS in an unexpected shape.
  if (report === null || report === undefined) {
    return {
      ran: false,
      failed: 'the DRC checker returned nothing, so this is NOT a clean '
        + 'board: the check did not run. Open a PCB document and retry.',
    };
  }
  const drcProblem = reportProblem(report, 'DRC');
  if (drcProblem) return { ran: false, failed: drcProblem };
  const violations = normaliseViolations(report);
  return { ran: true, violation_count: violations.length,
    violations: violations };
};

handlers['design.run_erc'] = async () => {
  // Same overload rule as the PCB checker above; sch_Drc.check carries
  // the identical signature.
  const report = await eda.sch_Drc.check(
    DRC_ARGS[0], DRC_ARGS[1], DRC_ARGS[2]);
  if (report === null || report === undefined) {
    return {
      ran: false,
      failed: 'the ERC checker returned nothing, so this is NOT a clean '
        + 'schematic: the check did not run. Open a schematic and retry.',
    };
  }
  const ercProblem = reportProblem(report, 'ERC');
  if (ercProblem) return { ran: false, failed: ercProblem };
  const violations = normaliseViolations(report);
  return { ran: true, violation_count: violations.length,
    violations: violations };
};

// Whether a checker's answer is a REPORT at all, or something this
// cannot enumerate violations from.
//
// Returns null when it is a usable report, or an explanation when it is
// not. Measured: eda.sch_Drc.check() answered with the BOOLEAN false on
// a live schematic. That is not null or undefined, so it sailed past
// the guard above, and normaliseViolations turned it into an empty
// list. The reply was "ran: true, violation_count: 0" - a confident
// clean bill of health from a check that produced no report.
//
// The measurement was right and the cause was not. check() is
// overloaded on includeVerboseError, and calling it with no arguments
// selected the boolean overload; the editor was answering the question
// we asked. Both call sites now pass the array form. This guard stays
// anyway: it is the only thing standing between a status value and a
// clean bill of health if an editor build ever answers with one.
//
// A boolean is a STATUS, not a list of violations. Even `true` cannot
// be read as "clean": nothing in it enumerates what was checked, and a
// review that treats it as zero violations is asserting something it
// was never told.
function reportProblem(report, what) {
  if (typeof report === 'boolean') {
    return (
      `the ${what} checker answered with the boolean ${report} rather than `
      + `a report, so no violation list exists. This is NOT a clean result: `
      + `nothing was enumerated. Run the check from the editor's own user `
      + `interface to see its findings.`
    );
  }
  if (Array.isArray(report)) return null;
  if (report && typeof report === 'object') {
    if (report.violations || report.items || report.result) return null;
    return (
      `the ${what} checker returned an object with none of the fields a `
      + `violation list has been seen under (violations, items, result); `
      + `its keys are [${Object.keys(report).join(', ')}]. Reporting zero `
      + `violations from that would be a guess.`
    );
  }
  return (
    `the ${what} checker answered with ${typeof report}, which carries no `
    + `violation list. This is NOT a clean result.`
  );
}

function normaliseViolations(report) {
  // Shapes differ between the PCB and schematic checkers, and both may
  // wrap the list. Reading several shapes beats assuming one and
  // silently reporting zero violations on a board that has them.
  //
  // Only ever called once reportProblem has confirmed the answer is a
  // report, so the [] fallback here can no longer stand in for one.
  const list =
    (Array.isArray(report) && report) ||
    (report && (report.violations || report.items || report.result)) ||
    [];
  return (Array.isArray(list) ? list : []).map((v) => ({
    description: v.message || v.description || v.rule || String(v),
    net: v.net || '',
    designator: v.designator || '',
    layer: v.layer || '',
  }));
}

handlers['pcb.net_classes'] = async () => ({
  net_classes: (await eda.pcb_Drc.getAllNetClasses()) || [],
});

handlers['pcb.differential_pairs'] = async () => ({
  differential_pairs: (await eda.pcb_Drc.getAllDifferentialPairs()) || [],
});

// REUSABLE CIRCUIT BLOCKS, which is what EasyEDA has instead of
// hierarchy.
//
// There is no hierarchical sheet here: schematic pages are siblings
// with an order, and no class exposes a sheet symbol, a sheet entry or
// a parent link. What EasyEDA offers instead is the CBB, 复用模块, a
// saved block placed into a schematic. Reuse without containment.
//
// Placing one needs THREE uuids and only two are obvious:
//
//   placeCbbSchematicPage({libraryUuid, cbbUuid, uuid}, x, y)
//
// The third is the schematic page INSIDE the block, and the chain to
// it is all documented: lib_Cbb.get gives an ILIB_CbbItem, whose
// `boards` array holds IDMT_BoardItem, whose `schematic` is an
// IDMT_SchematicItem carrying the uuid. Resolved here rather than
// asked of the caller, who has no way to know it.
handlers['lib.search_cbb'] = async (params) => {
  const query = params.query || '';
  const found = (await eda.lib_Cbb.search(
    query, params.library_uuid || undefined, undefined,
    Number(params.items_of_page) > 0 ? Number(params.items_of_page) : 10,
    Number(params.page) > 0 ? Number(params.page) : 1)) || [];
  return { found: found, count: found.length };
};

// sch.place_cbb WAS HERE AND IS REMOVED.
//
// placeCbbSchematicPage({libraryUuid, cbbUuid, uuid}, x, y) is fully
// documented, and the third uuid is resolvable: lib_Cbb.get returns an
// ILIB_CbbItem whose boards each carry a schematic whose uuid is the
// one the call wants. The whole chain reads correctly on paper.
//
// It is NOT IN THE RUNTIME. The captured surface for editor 2.2.47.7
// has sch_PrimitiveComponent with placeComponentWithMouse and no
// placeCbbSchematicPage, and the reality guard caught the call the
// moment it was written.
//
// This is the inverse of the sys_Tool comparison methods, which exist
// at runtime and are absent from the docs. Documented and present are
// two different sets, and neither contains the other. Shipping a tool
// that cannot work on the only build anyone has measured is what this
// backend spent a long time recovering from, so the search half stays
// and the place half waits for a runtime that has it.

handlers['sch.netlist'] = async () => ({
  netlist: (await eda.sch_Netlist.getNetlist()) || null,
});

// REPLACING THE CONNECTIVITY, which is the counterpart to the read
// above and by far the most destructive thing on this backend.
//
// setNetlist takes a STRING, not a File:
//
//   pcb_Net.setNetlist(type: ESYS_NetlistType | undefined,
//                      netlist: string): Promise<boolean>
//   sch_Netlist.setNetlist(same arguments): Promise<void>
//
// It was recorded alongside the autorouter FILE imports as though it
// needed the same plumbing. It does not, and it lives on pcb_Net
// rather than pcb_Document.
//
// Note the return types differ: the board form answers boolean, the
// schematic form answers void. So a schematic call CANNOT be confirmed
// from its return value, and saying "done" there would be asserting
// something the API never told us.
const NETLIST_TYPES = ['Allegro', 'DISA', 'DSNET', 'EasyEDA', 'JLCEDA',
  'PADS'];

// THE AUTOROUTER ROUND TRIP, closing the loop that export_dsn opens.
//
// The classic flow: export a DSN, route it in an external autorouter,
// import the SES back. We had the outward half and not the return, so
// the round trip stopped halfway.
//
// All three take a File, which is why they sat unexposed rather than
// because the API lacked anything. Each is confirm-gated: they replace
// routing or placement wholesale, and the destructive name list covers
// the same methods for anyone reaching them through the generic shim.
const FILE_IMPORTS = {
  ses: {
    method: 'importAutoRouteSesFile',
    extension: '.ses',
    warns: 'importing a SES file REPLACES the routing on this board.',
  },
  route_json: {
    method: 'importAutoRouteJsonFile',
    extension: '.json',
    warns: 'importing an autoroute JSON REPLACES the routing on this board.',
  },
  layout_json: {
    method: 'importAutoLayoutJsonFile',
    extension: '.json',
    warns: 'importing an autolayout JSON REPLACES the PLACEMENT of every '
      + 'component on this board.',
  },
};

handlers['pcb.import_routing'] = async (params) => {
  const kind = String(params.kind || '');
  const spec = FILE_IMPORTS[kind];
  if (!spec) {
    throw new Error(
      `kind must be one of ${Object.keys(FILE_IMPORTS).join(', ')}`);
  }
  requireConfirm(params, spec.warns
    + ' Whatever is there now is discarded. Take a checkpoint first.');
  const file = unpackedFile(params.file, spec.extension);
  const answer = await eda.pcb_Document[spec.method](file);
  return {
    imported: answer === true,
    kind: kind,
    method: spec.method,
    bytes: file.size,
  };
};

handlers['pcb.set_netlist'] = async (params) => {
  requireConfirm(params, 'set_netlist REPLACES the entire connectivity '
    + 'of the board. Every net comes from the netlist you supply, and '
    + 'whatever the board had before is gone.');
  const text = params.netlist;
  if (typeof text !== 'string' || !text.trim()) {
    throw new Error('netlist must be a non-empty string');
  }
  const type = params.netlist_type;
  if (type !== undefined && NETLIST_TYPES.indexOf(type) === -1) {
    throw new Error(
      `netlist_type ${JSON.stringify(type)} is not one of `
      + `${NETLIST_TYPES.join(', ')}. Omit it to let the editor decide.`);
  }
  const answer = await eda.pcb_Net.setNetlist(type, text);
  // The board form returns a boolean, so read it rather than assuming.
  return { applied: answer === true, netlist_type: type || null,
    length: text.length };
};

handlers['pcb.components'] = async () => ({
  components: (await eda.pcb_PrimitiveComponent.getAll()) || [],
});

handlers['pcb.nets'] = async () => ({
  nets: (await eda.pcb_Net.getAllNets()) || [],
});

handlers['pcb.net_length'] = async (params) => {
  const net = params.net;
  if (!net) throw new Error('net is required');
  // Named rather than positional so a caller cannot silently pass the
  // wrong argument, which on a length query returns a plausible number.
  return { net: net, length: await eda.pcb_Net.getNetLength(net) };
};

handlers['pcb.highlight_net'] = async (params) => {
  const net = params.net;
  if (!net) throw new Error('net is required');
  await eda.pcb_Net.highlightNet(net);
  return { net: net, highlighted: true };
};

// Routed length for every net, in ONE round trip.
//
// The per-net call already exists, and asking it net by net is a
// request each: a board with two hundred nets would cost two hundred
// round trips over a socket, which is the difference between a check
// somebody runs and one they do not. The loop belongs on this side.
handlers['pcb.net_lengths'] = async () => {
  const nets = (await eda.pcb_Net.getAllNets()) || [];
  const lengths = [];
  for (const net of nets) {
    // Measured: getAllNets returns objects shaped
    // {color, length, net}. The NAME field is `net`, and the first
    // version of this handler read `.name`, skipped every entry, and
    // reported a clean empty that read as "no nets".
    const name = typeof net === 'string' ? net : (net && (net.net || net.name));
    if (!name) continue;
    let length = (net && typeof net.length === 'number') ? net.length : null;
    if (length === null) {
      try {
        length = await eda.pcb_Net.getNetLength(name);
      } catch (e) {
        // A net the editor cannot measure is reported as unmeasured
        // rather than as zero, which would read as unrouted.
        length = null;
      }
    }
    lengths.push({ net: name, length });
  }
  return { lengths, count: lengths.length };
};

handlers['pcb.layers'] = async () => ({
  layers: (await eda.pcb_Layer.getAllLayers()) || [],
});

handlers['pcb.list_boards'] = async () => ({
  boards: (await eda.dmt_Pcb.getAllPcbsInfo()) || [],
});

handlers['sch.components'] = async () => ({
  components: (await eda.sch_PrimitiveComponent.getAll()) || [],
});

// THE INVERSE OF packedFile, for the calls that take a File IN.
//
// importAutoRouteSesFile, importAutoRouteJsonFile and
// importAutoLayoutJsonFile all take a File, and the bridge carries
// JSON. Sending a file INTO the editor was recorded as the reason
// those three stayed unexposed, and the plumbing is the mirror of what
// already existed for getting one out: base64 in, bytes out, wrapped
// in a File the sandbox will accept.
//
// The name matters to the editor. An autorouter session file is
// identified by its extension, so a caller that omits one gets a
// refusal here rather than an unexplained rejection inside EasyEDA.
function unpackedFile(payload, expectedExtension) {
  if (!payload || typeof payload !== 'object') {
    throw new Error('file must be an object with base64 and name');
  }
  const { base64, name } = payload;
  if (typeof base64 !== 'string' || !base64) {
    throw new Error('file.base64 is required');
  }
  if (typeof name !== 'string' || !name) {
    throw new Error('file.name is required: the editor identifies the '
      + 'format by its extension');
  }
  if (expectedExtension
      && !name.toLowerCase().endsWith(expectedExtension)) {
    throw new Error(
      `file.name is ${JSON.stringify(name)} but this call expects a `
      + `${expectedExtension} file. Passing the wrong format is accepted `
      + `by the editor and produces a board nobody asked for.`);
  }
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new File([bytes], name,
    { type: payload.mime || 'application/octet-stream' });
}

// Exports return file content from the editor rather than writing to
// disk here: the extension runs in a sandbox and has no path the server
// can agree on. The server writes what comes back.
async function packedFile(value) {
  // EasyEDA's manufacture exports return FILE DATA, a Blob: their own
  // docs save the result with sys_FileSystem.saveFile(). This bridge
  // sends JSON, and JSON.stringify(blob) is {}, so every export used
  // to arrive as an empty object and read as a failed export. Measured
  // measured: gerber, bom, netlist and the rest all arrive empty.
  //
  // So the blob becomes base64 here, in chunks: String.fromCharCode
  // over a whole multi-megabyte buffer blows the argument limit.
  if (value === null || value === undefined) return null;
  if (typeof value === 'string') {
    return { kind: 'text', size: value.length, text: value };
  }
  if (typeof value.arrayBuffer === 'function') {
    const buffer = new Uint8Array(await value.arrayBuffer());
    let binary = '';
    const CHUNK = 0x8000;
    for (let i = 0; i < buffer.length; i += CHUNK) {
      binary += String.fromCharCode.apply(
        null, buffer.subarray(i, i + CHUNK));
    }
    return {
      kind: 'base64',
      size: buffer.length,
      name: typeof value.name === 'string' ? value.name : undefined,
      mime: typeof value.type === 'string' ? value.type : undefined,
      base64: btoa(binary),
    };
  }
  // Something else entirely; hand it over as-is so the caller can see
  // what it was rather than a silent null.
  return { kind: 'raw', value: value };
}

handlers['export.bom'] = async () => ({
  file: await packedFile(await eda.pcb_ManufactureData.getBomFile()),
});

handlers['export.dxf'] = async () => ({
  file: await packedFile(await eda.pcb_ManufactureData.getDxfFile()),
});

handlers['export.model_3d'] = async () => ({
  file: await packedFile(await eda.pcb_ManufactureData.get3DFile()),
});

handlers['pcb.vias'] = async () => ({
  vias: (await eda.pcb_PrimitiveVia.getAll()) || [],
});

handlers['pcb.lines'] = async () => ({
  lines: (await eda.pcb_PrimitiveLine.getAll()) || [],
});

handlers['pcb.pads'] = async () => ({
  pads: (await eda.pcb_PrimitivePad.getAll()) || [],
});

handlers['export.gerber'] = async () => ({
  file: await packedFile(await eda.pcb_ManufactureData.getGerberFile()),
});

handlers['export.ipc2581'] = async () => ({
  file: await packedFile(await eda.pcb_ManufactureData.getIpc2581CFile()),
});

handlers['export.ipcd356'] = async () => ({
  file: await packedFile(await eda.pcb_ManufactureData.getIpcD356AFile()),
});

handlers['export.netlist'] = async () => ({
  file: await packedFile(await eda.pcb_ManufactureData.getNetlistFile()),
});

handlers['export.altium'] = async () => ({
  file: await packedFile(await eda.pcb_ManufactureData.getAltiumDesignerFile()),
});

handlers['export.pdf'] = async () => ({
  file: await packedFile(await eda.pcb_ManufactureData.getPdfFile()),
});

handlers['export.pick_and_place'] = async () => ({
  file: await packedFile(await eda.pcb_ManufactureData.getPickAndPlaceFile()),
});

handlers['export.test_points'] = async () => ({
  file: await packedFile(await eda.pcb_ManufactureData.getTestPointFile()),
});

handlers['export.flying_probe'] = async () => ({
  file: await packedFile(await eda.pcb_ManufactureData.getFlyingProbeTestFile()),
});

handlers['export.dsn'] = async () => ({
  file: await packedFile(await eda.pcb_ManufactureData.getDsnFile()),
});

handlers['export.pads'] = async () => ({
  file: await packedFile(await eda.pcb_ManufactureData.getPadsFile()),
});

handlers['export.pcb_info'] = async () => ({
  file: await packedFile(await eda.pcb_ManufactureData.getPcbInfoFile()),
});

handlers['export.schematic_document'] = async () => ({
  file: await packedFile(await eda.sch_ManufactureData.getExportDocumentFile()),
});

handlers['export.schematic_netlist'] = async () => ({
  file: await packedFile(await eda.sch_ManufactureData.getNetlistFile()),
});

handlers['pcb.save'] = async () => {
  // Only an EXPLICIT false is treated as a decline.
  //
  // Hardcoding {saved: true} meant the caller was told it worked whatever
  // the editor answered, which is the same defect found in
  // pcb.modify_component: the result was discarded and success
  // asserted. Undefined is left alone rather than read as failure,
  // because a void API returns undefined and calling that a failure
  // would invent a decline that never happened. Whether these methods
  // return anything at all is unmeasured.
  const answer = await eda.pcb_Document.save();
  if (answer === false) {
    return { saved: false, failed: 'the editor declined to save' };
  }
  return { saved: true };
};

handlers['pcb.clear_routing'] = async (params) => {
  // Destructive and not undoable through this channel, so it refuses
  // unless the caller said so explicitly. The same reasoning as the
  // Altium side's confirm_delete_all: an agent that can erase every
  // track by accident will eventually do it.
  if (params.confirm !== true) {
    throw new Error(
      'clear_routing removes existing routing and is not undoable from ' +
        'here. Pass confirm=true if that is intended.',
    );
  }
  await eda.pcb_Document.clearRouting();
  return { cleared: true };
};

handlers['pcb.primitives_in_region'] = async (params) => {
  const { x1, y1, x2, y2 } = params;
  if ([x1, y1, x2, y2].some((v) => typeof v !== 'number')) {
    throw new Error('x1, y1, x2 and y2 are required and must be numbers');
  }
  // BOTH X VALUES, THEN BOTH Y VALUES. The signature is
  // getPrimitivesInRegion(left, right, top, bottom), so the corner-pair
  // order this handler accepts at its own boundary must be interleaved
  // before it goes in. Passing (x1, y1, x2, y2) put y1 into `right` and
  // x2 into `top`, which searched a different rectangle and returned a
  // perfectly plausible list of primitives from the wrong place. That
  // is why it was never noticed: the failure has no symptom to see.
  return {
    primitives:
      (await eda.pcb_Document.getPrimitivesInRegion(x1, x2, y1, y2)) || [],
  };
};

// ---- writing to the board -------------------------------------------
//
// Everything above reads. These create primitives, which is what makes
// this backend able to change a board rather than only describe one.
//
// Layers and alignment cross the wire as names, never numbers. EasyEDA's
// layer ids are a NUMERIC enum, and their own guidance is to use the
// members rather than the values, so the number is looked up from the
// runtime's enum at call time. A hardcoded table here would be a second
// copy of their numbering, silently wrong the day they insert a layer.

// Read an enum EasyEDA injects as a bare global, without assuming it is
// there. Passing the identifier directly to a function would throw
// ReferenceError at the call site before any check inside could run, and
// that error names neither the enum nor the fact that it takes out every
// placement command at once.
function injectedEnum(name) {
  const found = typeof globalThis !== 'undefined'
    ? globalThis[name] : undefined;
  if (found === undefined || found === null) {
    throw new Error(
      `${name} is not available in this EasyEDA runtime, so no name from `
        + 'it can be resolved to a value. Every command that needs one '
        + 'is affected, not this one alone.',
    );
  }
  return found;
}

function enumValue(enumObject, name, what) {
  if (typeof name !== 'string' || !name) {
    throw new Error(`${what} is required, as one of its names`);
  }
  const key = name.toUpperCase();
  const value = enumObject ? enumObject[key] : undefined;
  if (typeof value !== 'number') {
    const known = enumObject
      ? Object.keys(enumObject).filter((k) => Number.isNaN(Number(k)))
      : [];
    throw new Error(
      `${what} "${name}" is not a known value. Known: ${known.join(', ')}`,
    );
  }
  return value;
}

function requireNumbers(params, names) {
  for (const name of names) {
    if (typeof params[name] !== 'number' || !Number.isFinite(params[name])) {
      throw new Error(`${name} is required and must be a number`);
    }
  }
}

// The net a primitive belongs to. Silkscreen and outline lines have no
// net, so an empty string is legitimate rather than a missing argument.
function netOf(params) {
  return typeof params.net === 'string' ? params.net : '';
}

handlers['pcb.add_line'] = async (params) => {
  requireNumbers(params, ['start_x', 'start_y', 'end_x', 'end_y']);
  const layer = enumValue(injectedEnum('EPCB_LayerId'), params.layer, 'layer');
  const line = await eda.pcb_PrimitiveLine.create(
    netOf(params),
    layer,
    params.start_x,
    params.start_y,
    params.end_x,
    params.end_y,
    typeof params.width === 'number' ? params.width : undefined,
    params.locked === true,
  );
  return { created: line || null };
};

handlers['pcb.add_arc'] = async (params) => {
  requireNumbers(params, ['start_x', 'start_y', 'end_x', 'end_y', 'angle']);
  const layer = enumValue(injectedEnum('EPCB_LayerId'), params.layer, 'layer');
  const arc = await eda.pcb_PrimitiveArc.create(
    netOf(params),
    layer,
    params.start_x,
    params.start_y,
    params.end_x,
    params.end_y,
    params.angle,
    typeof params.width === 'number' ? params.width : undefined,
  );
  return { created: arc || null };
};

handlers['pcb.add_via'] = async (params) => {
  requireNumbers(params, ['x', 'y', 'hole_diameter', 'diameter']);
  if (params.diameter <= params.hole_diameter) {
    // The API would take it and produce a via with no annular ring,
    // which is a board that cannot be made rather than an error anyone
    // would notice on screen.
    throw new Error(
      `diameter (${params.diameter}) must exceed hole_diameter ` +
        `(${params.hole_diameter}), or the via has no annular ring`,
    );
  }
  const via = await eda.pcb_PrimitiveVia.create(
    netOf(params),
    params.x,
    params.y,
    params.hole_diameter,
    params.diameter,
  );
  return { created: via || null };
};

handlers['pcb.add_text'] = async (params) => {
  requireNumbers(params, ['x', 'y', 'font_size', 'width']);
  if (typeof params.text !== 'string' || !params.text) {
    throw new Error('text is required');
  }
  const layer = enumValue(injectedEnum('EPCB_LayerId'), params.layer, 'layer');
  const align = enumValue(
    injectedEnum('EPCB_PrimitiveStringAlignMode'),
    params.align || 'LEFT_BOTTOM',
    'align',
  );
  const string = await eda.pcb_PrimitiveString.create(
    layer,
    params.x,
    params.y,
    params.text,
    typeof params.font === 'string' && params.font ? params.font : 'NotoSans',
    params.font_size,
    params.width,
    align,
    typeof params.rotation === 'number' ? params.rotation : 0,
    params.reverse === true,
    typeof params.expansion === 'number' ? params.expansion : 0,
    params.mirror === true,
    params.locked === true,
  );
  return { created: string || null };
};

// Build the IPCB_Polygon that pours and polylines both take.
//
// EasyEDA's polygon source is one FLAT array: a start coordinate, then
// a command letter and its arguments, repeating. 'L' is a line segment.
// Their published example does not repeat the start point at the end,
// so a caller that closed the ring themselves would produce a
// zero-length segment; that trailing duplicate is dropped rather than
// passed on.
//
// Shared rather than written twice: the two callers would otherwise
// each carry their own copy of that closing rule, and the version that
// got it wrong would still draw a shape.
function polygonFrom(params, minimum) {
  const points = Array.isArray(params.points) ? params.points : [];
  if (points.length < minimum) {
    throw new Error(`points must be at least ${minimum} [x, y] pairs`);
  }
  for (const p of points) {
    if (!Array.isArray(p) || p.length !== 2
        || typeof p[0] !== 'number' || typeof p[1] !== 'number') {
      throw new Error('each point must be a pair of numbers, [x, y]');
    }
  }

  const ring = points.slice();
  const first = ring[0];
  const last = ring[ring.length - 1];
  if (ring.length > minimum && first[0] === last[0] && first[1] === last[1]) {
    ring.pop();
  }

  const source = [ring[0][0], ring[0][1]];
  for (const [x, y] of ring.slice(1)) {
    source.push('L', x, y);
  }

  const polygon = eda.pcb_MathPolygon.createPolygon(source);
  if (!polygon) {
    throw new Error('the editor rejected the polygon outline');
  }
  return polygon;
}

handlers['pcb.add_polyline'] = async (params) => {
  const layer = enumValue(injectedEnum('EPCB_LayerId'), params.layer, 'layer');
  const polyline = await eda.pcb_PrimitivePolyline.create(
    netOf(params),
    layer,
    polygonFrom(params, 2),
    typeof params.width === 'number' ? params.width : undefined,
    params.locked === true,
  );
  return { created: polyline || null };
};

handlers['pcb.select'] = async (params) => {
  const ids = Array.isArray(params.primitive_ids) ? params.primitive_ids : [];
  if (!ids.length) {
    throw new Error('primitive_ids is required and must not be empty');
  }
  return {
    selected: await eda.pcb_SelectControl.doSelectPrimitives(ids) === true,
    count: ids.length,
  };
};

// Pad shapes and holes are TUPLES, not objects: [shape, w, h] and
// [holeType, diameter]. The first element is an enum member read from
// the runtime, for the same reason the layer ids are.
const PAD_SHAPES = ['ELLIPSE', 'RECTANGLE', 'OBLONG', 'REGULAR_POLYGON'];

function padShape(params) {
  const name = String(params.shape || 'ELLIPSE').toUpperCase();
  if (!PAD_SHAPES.includes(name)) {
    throw new Error(`shape must be one of: ${PAD_SHAPES.join(', ')}`);
  }
  const shapes = injectedEnum('EPCB_PrimitivePadShapeType');
  const kind = shapes[name];
  if (kind === undefined) {
    throw new Error(
      `EPCB_PrimitivePadShapeType has no member ${name} in this runtime`);
  }
  requireNumbers(params, ['width']);
  if (name === 'REGULAR_POLYGON') {
    // Second number is a SIDE COUNT here, not a height. Passing a
    // height would silently make a polygon with that many sides.
    const sides = typeof params.sides === 'number' ? params.sides : 0;
    if (sides <= 2) {
      throw new Error('a regular polygon needs sides greater than 2');
    }
    return [kind, params.width, sides];
  }
  const height = typeof params.height === 'number'
    ? params.height : params.width;
  if (name === 'RECTANGLE') {
    return [kind, params.width, height,
      typeof params.corner_radius === 'number' ? params.corner_radius : 0];
  }
  return [kind, params.width, height];
}

function padHole(params) {
  const diameter = params.hole_diameter;
  if (typeof diameter !== 'number' || diameter <= 0) {
    return null;   // a surface-mount pad: no hole is the normal case
  }
  const holes = injectedEnum('EPCB_PrimitivePadHoleType');
  const length = params.hole_length;
  if (typeof length === 'number' && length > diameter) {
    return [holes.SLOT, diameter, length];
  }
  return [holes.ROUND, diameter];
}

handlers['pcb.add_pads'] = async (params) => {
  const pads = Array.isArray(params.pads) ? params.pads : [];
  if (!pads.length) {
    throw new Error('pads must not be empty');
  }
  const results = [];
  let placed = 0;
  let stopped = false;
  for (const pad of pads) {
    if (stopped) {
      results.push({ pad_number: pad && pad.pad_number, ok: false,
        skipped: true, error: 'an earlier pad in this batch failed' });
      continue;
    }
    if (typeof (pad && pad.pad_number) !== 'string' || !pad.pad_number) {
      results.push({ pad_number: null, ok: false,
        error: 'pad_number is required' });
      stopped = true;
      continue;
    }
    try {
      // Built through the SAME helpers the single-pad handler uses.
      // Spelling the create call out again here would be a second copy
      // of an eight-argument signature, and the shape and hole
      // arguments are themselves argument LISTS whose length varies by
      // shape: a rectangle carries a corner radius, a regular polygon
      // carries a side count where a height would go.
      requireNumbers(pad, ['x', 'y']);
      const created = await eda.pcb_PrimitivePad.create(
        enumValue(injectedEnum('EPCB_LayerId'), pad.layer || 'TOP', 'layer'),
        pad.pad_number,
        pad.x,
        pad.y,
        typeof pad.rotation === 'number' ? pad.rotation : 0,
        padShape(pad),
        netOf(pad),
        padHole(pad),
      );
      results.push({ pad_number: pad.pad_number, ok: Boolean(created) });
      if (created) placed += 1;
      else stopped = true;
    } catch (e) {
      results.push({ pad_number: pad && pad.pad_number, ok: false,
        error: String(e) });
      stopped = true;
    }
  }
  return { placed, of: pads.length, results, stopped };
};

handlers['pcb.add_pad'] = async (params) => {
  requireNumbers(params, ['x', 'y']);
  if (typeof params.pad_number !== 'string' || !params.pad_number) {
    // Numbered by string, and it is what ties the pad to a symbol pin.
    // An unnumbered pad is copper the netlist cannot reach.
    throw new Error('pad_number is required');
  }
  const layer = enumValue(
    injectedEnum('EPCB_LayerId'), params.layer || 'TOP', 'layer');
  const pad = await eda.pcb_PrimitivePad.create(
    layer,
    params.pad_number,
    params.x,
    params.y,
    typeof params.rotation === 'number' ? params.rotation : 0,
    padShape(params),
    netOf(params),
    padHole(params),
  );
  return { created: pad || null };
};

// What a region forbids. A region with no rule is just an outline: it
// draws, it constrains nothing, and the board routes straight through
// the area somebody meant to protect.
const REGION_RULES = ['NO_COMPONENTS', 'NO_WIRES', 'NO_FILLS', 'NO_POURS',
  'NO_INNER_ELECTRICAL_LAYERS', 'FOLLOW_REGION_RULE'];

// Each dimension type wants a DIFFERENT number of points, and they are
// not interchangeable: a length needs four, a radius and an angle need
// three, and the meaning of each point differs per type. Passing the
// wrong count is the failure worth catching here, because a dimension
// drawn from the wrong points still draws.
const DIMENSION_POINTS = { LENGTH: 4, RADIUS: 3, ANGLE: 3 };

handlers['pcb.add_dimension'] = async (params) => {
  const typeName = String(params.dimension_type || '').toUpperCase();
  const wanted = DIMENSION_POINTS[typeName];
  if (!wanted) {
    throw new Error(
      `dimension_type must be one of: ${Object.keys(DIMENSION_POINTS).join(', ')}`,
    );
  }
  const points = Array.isArray(params.points) ? params.points : [];
  if (points.length !== wanted) {
    throw new Error(
      `a ${typeName} dimension takes exactly ${wanted} [x, y] points, `
        + `and ${points.length} were given`,
    );
  }
  const flat = [];
  for (const p of points) {
    if (!Array.isArray(p) || p.length !== 2
        || typeof p[0] !== 'number' || typeof p[1] !== 'number') {
      throw new Error('each point must be a pair of numbers, [x, y]');
    }
    flat.push(p[0], p[1]);
  }
  const types = injectedEnum('EPCB_PrimitiveDimensionType');
  const kind = types[typeName];
  if (kind === undefined) {
    throw new Error(
      `EPCB_PrimitiveDimensionType has no member ${typeName} here`);
  }
  const layer = enumValue(
    injectedEnum('EPCB_LayerId'), params.layer || 'DOCUMENT', 'layer');
  const dimension = await eda.pcb_PrimitiveDimension.create(
    kind, flat, layer, undefined,
    typeof params.width === 'number' ? params.width : undefined,
    typeof params.precision === 'number' ? params.precision : undefined,
  );
  return { created: dimension || null };
};

handlers['pcb.add_fill'] = async (params) => {
  const layer = enumValue(injectedEnum('EPCB_LayerId'), params.layer, 'layer');
  const fill = await eda.pcb_PrimitiveFill.create(
    layer,
    polygonFrom(params, 3),
    netOf(params),
    undefined,
    typeof params.width === 'number' ? params.width : undefined,
    params.locked === true,
  );
  return { created: fill || null };
};

handlers['pcb.add_region'] = async (params) => {
  const layer = enumValue(injectedEnum('EPCB_LayerId'), params.layer, 'layer');
  const wanted = Array.isArray(params.rules) ? params.rules : [];
  if (!wanted.length) {
    throw new Error(
      `rules is required, as one or more of: ${REGION_RULES.join(', ')}. `
        + 'A region with no rule constrains nothing.',
    );
  }
  const kinds = injectedEnum('EPCB_PrimitiveRegionRuleType');
  const ruleTypes = wanted.map((name) => {
    const key = String(name).toUpperCase();
    if (!REGION_RULES.includes(key) || kinds[key] === undefined) {
      throw new Error(`rules must be from: ${REGION_RULES.join(', ')}`);
    }
    return kinds[key];
  });
  const region = await eda.pcb_PrimitiveRegion.create(
    layer,
    polygonFrom(params, 3),
    ruleTypes,
    typeof params.name === 'string' && params.name ? params.name : undefined,
    typeof params.width === 'number' ? params.width : undefined,
    params.locked === true,
  );
  return { created: region || null };
};

handlers['pcb.add_zone'] = async (params) => {
  const layer = enumValue(injectedEnum('EPCB_LayerId'), params.layer, 'layer');
  const pour = await eda.pcb_PrimitivePour.create(
    netOf(params),
    layer,
    polygonFrom(params, 3),
    undefined,
    params.preserve_islands === true,
    typeof params.name === 'string' && params.name ? params.name : undefined,
    typeof params.priority === 'number' ? params.priority : undefined,
    typeof params.width === 'number' ? params.width : undefined,
  );
  return { created: pour || null };
};

handlers['pcb.import_changes'] = async (params) => {
  // The schematic-to-board update: EasyEDA's equivalent of an ECO. It
  // can remove components the schematic no longer has, and their
  // routing with them, so it is not a read.
  if (params.confirm !== true) {
    throw new Error(
      'import_changes applies the schematic to the board, which can '
        + 'remove components and their routing. Pass confirm=true if '
        + 'that is intended.',
    );
  }
  const uuid = typeof params.schematic_uuid === 'string'
    && params.schematic_uuid ? params.schematic_uuid : undefined;
  return { imported: await eda.pcb_Document.importChanges(uuid) === true };
};

handlers['pcb.zoom_to_board'] = async () => ({
  zoomed: await eda.pcb_Document.zoomToBoardOutline() === true,
});

const SCH_DELETERS = {
  wire: () => eda.sch_PrimitiveWire,
  text: () => eda.sch_PrimitiveText,
  rectangle: () => eda.sch_PrimitiveRectangle,
  component: () => eda.sch_PrimitiveComponent,
  attribute: () => eda.sch_PrimitiveAttribute,
};

handlers['sch.delete_primitives'] = async (params) => {
  if (params.confirm !== true) {
    // The Python tool checks this too. Both halves check because this
    // channel is reachable by anything speaking the protocol, so it
    // cannot assume a caller already asked.
    throw new Error(
      'delete_primitives removes objects and is not undoable from here. '
        + 'Pass confirm=true if that is intended.',
    );
  }
  const kind = String(params.kind || '').toLowerCase();
  if (!Object.prototype.hasOwnProperty.call(SCH_DELETERS, kind)) {
    throw new Error(
      `kind must be one of: ${Object.keys(SCH_DELETERS).join(', ')}`,
    );
  }
  const ids = Array.isArray(params.primitive_ids) ? params.primitive_ids : [];
  if (!ids.length) {
    throw new Error('primitive_ids is required and must not be empty');
  }
  const deleted = await SCH_DELETERS[kind]().delete(ids);
  return { deleted: deleted === true, count: ids.length, kind };
};

// ---- layers ---------------------------------------------------------

// Every copper layer count EasyEDA accepts. Stated by their signature as
// a union of literals, so an unlisted number is rejected here with the
// list rather than sent and refused with nothing useful said.
const COPPER_LAYER_COUNTS = [
  2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32,
];

function layerList(params) {
  const names = Array.isArray(params.layers) ? params.layers : [];
  if (!names.length) {
    throw new Error('layers is required and must not be empty');
  }
  const layerEnum = injectedEnum('EPCB_LayerId');
  return names.map((n) => enumValue(layerEnum, n, 'layer'));
}

handlers['pcb.set_copper_layer_count'] = async (params) => {
  const count = params.count;
  if (!COPPER_LAYER_COUNTS.includes(count)) {
    throw new Error(
      `count must be one of: ${COPPER_LAYER_COUNTS.join(', ')}`,
    );
  }
  // Reducing the count discards what was on the layers that go away.
  if (params.confirm !== true) {
    throw new Error(
      'changing the copper layer count restructures the stackup and '
        + 'discards anything on a layer that is removed. Pass '
        + 'confirm=true if that is intended.',
    );
  }
  return {
    set: await eda.pcb_Layer.setTheNumberOfCopperLayers(count) === true,
    count,
  };
};

handlers['pcb.set_layer_visibility'] = async (params) => {
  const layers = layerList(params);
  const visible = params.visible !== false;
  const exclusive = params.exclusive === true;
  const ok = visible
    ? await eda.pcb_Layer.setLayerVisible(layers, exclusive)
    : await eda.pcb_Layer.setLayerInvisible(layers, exclusive);
  return { applied: ok === true, visible, exclusive };
};

handlers['pcb.set_layer_lock'] = async (params) => {
  const layers = layerList(params);
  const locked = params.locked !== false;
  const ok = locked
    ? await eda.pcb_Layer.lockLayer(layers)
    : await eda.pcb_Layer.unlockLayer(layers);
  return { applied: ok === true, locked };
};

handlers['pcb.select_layer'] = async (params) => {
  const layer = enumValue(
    injectedEnum('EPCB_LayerId'), params.layer, 'layer');
  return { selected: await eda.pcb_Layer.selectLayer(layer) === true };
};

handlers['pcb.modify_layer'] = async (params) => {
  const layer = enumValue(
    injectedEnum('EPCB_LayerId'), params.layer, 'layer');
  const property = {};
  if (typeof params.name === 'string' && params.name) {
    property.name = params.name;
  }
  if (typeof params.color === 'string' && params.color) {
    property.color = params.color;
  }
  if (typeof params.transparency === 'number') {
    property.transparency = params.transparency;
  }
  if (!Object.keys(property).length) {
    throw new Error(
      'give at least one of name, color or transparency; an empty '
        + 'change reports success while doing nothing',
    );
  }
  return {
    modified: await eda.pcb_Layer.modifyLayer(layer, property) === true,
  };
};

// ---- design rules ---------------------------------------------------

// The colour a group is drawn in. EasyEDA takes { r, g, b, alpha } or
// null, and null means "you choose". Defaulting to null rather than to
// some colour picked here keeps this from quietly restyling a board.
function groupColour(params) {
  const c = params.color;
  if (!c || typeof c !== 'object') return null;
  const { r, g, b } = c;
  if ([r, g, b].some((v) => typeof v !== 'number')) {
    throw new Error('color must be {r, g, b} with an optional alpha');
  }
  return { r, g, b, alpha: typeof c.alpha === 'number' ? c.alpha : 1 };
}

function requireNets(params) {
  const nets = Array.isArray(params.nets) ? params.nets : [];
  if (!nets.length || nets.some((n) => typeof n !== 'string' || !n)) {
    throw new Error('nets is required and must be non-empty net names');
  }
  return nets;
}

function requireName(params) {
  if (typeof params.name !== 'string' || !params.name) {
    throw new Error('name is required');
  }
  return params.name;
}

handlers['pcb.create_net_class'] = async (params) => ({
  created: await eda.pcb_Drc.createNetClass(
    requireName(params), requireNets(params), groupColour(params)) === true,
});

handlers['pcb.add_nets_to_net_class'] = async (params) => ({
  added: await eda.pcb_Drc.addNetToNetClass(
    requireName(params), requireNets(params)) === true,
});

handlers['pcb.create_differential_pair'] = async (params) => {
  const positive = params.positive_net;
  const negative = params.negative_net;
  if (typeof positive !== 'string' || !positive
      || typeof negative !== 'string' || !negative) {
    throw new Error('positive_net and negative_net are both required');
  }
  if (positive === negative) {
    // The editor would take it and produce a pair of one net with
    // itself, which routes as a pair and is not one.
    throw new Error('positive_net and negative_net must differ');
  }
  return {
    created: await eda.pcb_Drc.createDifferentialPair(
      requireName(params), positive, negative) === true,
  };
};

handlers['pcb.create_length_match_group'] = async (params) => ({
  created: await eda.pcb_Drc.createEqualLengthNetGroup(
    requireName(params), requireNets(params), groupColour(params)) === true,
});

handlers['pcb.add_nets_to_length_match_group'] = async (params) => ({
  added: await eda.pcb_Drc.addNetToEqualLengthNetGroup(
    requireName(params), requireNets(params)) === true,
});

handlers['pcb.net_rules'] = async () => ({
  rules: (await eda.pcb_Drc.getNetRules()) || [],
});

handlers['pcb.rule_configurations'] = async () => ({
  configurations: (await eda.pcb_Drc.getAllRuleConfigurations()) || [],
  current: await eda.pcb_Drc.getCurrentRuleConfigurationName(),
});

handlers['pcb.length_match_groups'] = async () => ({
  groups: (await eda.pcb_Drc.getAllEqualLengthNetGroups()) || [],
});

// ---- placing library parts ------------------------------------------
//
// A part is identified by the pair EasyEDA's own search returns,
// { libraryUuid, uuid }. Nothing here looks a part up by name: two
// libraries can hold the same name, and picking one silently is how a
// board ends up with the wrong footprint on a part that reads correctly
// in the BOM.

function libraryRef(params) {
  const libraryUuid = params.library_uuid;
  const uuid = params.uuid;
  if (typeof libraryUuid !== 'string' || !libraryUuid
      || typeof uuid !== 'string' || !uuid) {
    throw new Error(
      'library_uuid and uuid are both required. Both come from a search '
        + 'result: nothing resolves a bare name straight to a part, so '
        + 'call lib.search_devices (or search_symbols / search_footprints) '
        + 'first and pass the pair it returns. Searching BY name is fine, '
        + 'it is the direct name-to-uuid lookup that does not exist.',
    );
  }
  return { libraryUuid, uuid };
}

// Place many parts in ONE round trip.
//
// Each placement is reported individually. A batch that half-succeeds
// is the case worth designing for: the parts that landed are on the
// sheet, and a caller needs to know which so the retry does not double
// them up.
handlers['sch.place_components'] = async (params) => {
  const items = Array.isArray(params.components) ? params.components : [];
  if (!items.length) {
    throw new Error('components must not be empty');
  }
  const results = [];
  let placed = 0;
  let stopped = false;
  for (const item of items) {
    if (stopped) {
      // STOPS at the first failure, and says the rest were not tried.
      //
      // This batch replaces a sequence of individual calls, and that
      // sequence stopped on failure. Carrying on here would place parts
      // around the hole where the failed one belongs and report a count,
      // which is the outcome batching was supposed to make no worse.
      results.push({ uuid: item && item.uuid, ok: false,
        skipped: true, error: 'an earlier placement in this batch failed' });
      continue;
    }
    try {
      const created = await eda.sch_PrimitiveComponent.create(
        libraryRef(item),
        item.x,
        item.y,
        undefined,
        typeof item.rotation === 'number' ? item.rotation : 0,
        item.mirror === true,
        item.add_to_bom !== false,
        item.add_to_pcb !== false,
      );
      results.push({ uuid: item.uuid, ok: Boolean(created),
        created: created || null });
      if (created) placed += 1;
      else stopped = true;
    } catch (e) {
      results.push({ uuid: item && item.uuid, ok: false,
        error: String(e) });
      stopped = true;
    }
  }
  return { placed, of: items.length, results, stopped };
};

handlers['sch.place_component'] = async (params) => {
  requireNumbers(params, ['x', 'y']);
  const component = await eda.sch_PrimitiveComponent.create(
    libraryRef(params),
    params.x,
    params.y,
    typeof params.sub_part === 'string' && params.sub_part
      ? params.sub_part : undefined,
    typeof params.rotation === 'number' ? params.rotation : 0,
    params.mirror === true,
    params.add_to_bom !== false,
    params.add_to_pcb !== false,
  );
  return { created: component || null };
};

handlers['pcb.place_components'] = async (params) => {
  const items = Array.isArray(params.components) ? params.components : [];
  if (!items.length) {
    throw new Error('components must not be empty');
  }
  const layerEnum = injectedEnum('EPCB_LayerId');
  const results = [];
  let placed = 0;
  let stopped = false;
  for (const item of items) {
    if (stopped) {
      results.push({ uuid: item && item.uuid, ok: false, skipped: true,
        error: 'an earlier placement in this batch failed' });
      continue;
    }
    try {
      const created = await eda.pcb_PrimitiveComponent.create(
        libraryRef(item),
        enumValue(layerEnum, item.layer || 'TOP', 'layer'),
        item.x,
        item.y,
        typeof item.rotation === 'number' ? item.rotation : 0,
        item.locked === true,
      );
      results.push({ uuid: item.uuid, ok: Boolean(created),
        created: created || null });
      if (created) placed += 1;
      else stopped = true;
    } catch (e) {
      results.push({ uuid: item && item.uuid, ok: false, error: String(e) });
      stopped = true;
    }
  }
  return { placed, of: items.length, results, stopped };
};

handlers['pcb.place_component'] = async (params) => {
  requireNumbers(params, ['x', 'y']);
  const layer = enumValue(
    injectedEnum('EPCB_LayerId'), params.layer || 'TOP', 'layer');
  const component = await eda.pcb_PrimitiveComponent.create(
    libraryRef(params),
    layer,
    params.x,
    params.y,
    typeof params.rotation === 'number' ? params.rotation : 0,
    params.locked === true,
  );
  return { created: component || null };
};

// Modify many components in ONE round trip.
//
// The per-component call already exists, and looping it from the server
// costs a request each: renumbering forty parts is forty round trips
// over a socket, which is the difference between a batch edit and a
// pause. The loop belongs on this side, the same way the net-length
// sweep does.
//
// Each change is reported individually rather than as one verdict. A
// partial failure is the interesting case: knowing THAT something
// failed is no use without knowing which, since the rest did apply and
// the design is now half-edited.
async function modifyEach(modify, changes) {
  const results = [];
  let applied = 0;
  for (const change of changes) {
    const id = change && change.primitive_id;
    const properties = change && change.changes;
    if (typeof id !== 'string' || !id) {
      results.push({ primitive_id: id || null, ok: false,
        error: 'primitive_id is required' });
      continue;
    }
    if (!properties || typeof properties !== 'object'
        || Array.isArray(properties) || !Object.keys(properties).length) {
      results.push({ primitive_id: id, ok: false,
        error: 'changes must name at least one property' });
      continue;
    }
    try {
      const out = await modify(id, properties);
      results.push({ primitive_id: id, ok: Boolean(out) });
      if (out) applied += 1;
    } catch (e) {
      results.push({ primitive_id: id, ok: false, error: String(e) });
    }
  }
  return { applied, of: changes.length, results };
}

handlers['sch.modify_components'] = async (params) => {
  const changes = Array.isArray(params.changes) ? params.changes : [];
  if (!changes.length) {
    throw new Error('changes must not be empty');
  }
  return modifyEach(
    (id, properties) => eda.sch_PrimitiveComponent.modify(id, properties),
    changes);
};

handlers['pcb.modify_components'] = async (params) => {
  const changes = Array.isArray(params.changes) ? params.changes : [];
  if (!changes.length) {
    throw new Error('changes must not be empty');
  }
  return modifyEach(
    (id, properties) => eda.pcb_PrimitiveComponent.modify(id, properties),
    changes);
};

handlers['sch.set_component_properties'] = async (params) => {
  if (typeof params.primitive_id !== 'string' || !params.primitive_id) {
    throw new Error('primitive_id is required');
  }
  const property = params.changes;
  if (!property || typeof property !== 'object' || Array.isArray(property)) {
    throw new Error('changes is required and must be an object');
  }
  const modified = await eda.sch_PrimitiveComponent.modify(
    params.primitive_id, property);
  return { modified: modified || null };
};

// ---- writing to the schematic ---------------------------------------
//
// No layer here: a schematic sheet has none, so these take no layer name
// and the enum lookup above does not apply.

// A WIRE OR BUS IS A LIST OF SEGMENTS, NOT A LIST OF POINTS.
//
// sch_PrimitiveWire.create and sch_PrimitiveBus.create take
// [[x1,y1,x2,y2], ...]: each entry is one whole segment as four flat
// numbers. Three call sites passed [[x,y],[x,y]] instead, which is a
// list of malformed segments, and create returned null every time. So
// neither the single wire, the bulk wires, nor the bus had ever drawn
// anything.
//
// Measured, not reasoned: a wire already on a live sheet reports
// line: [[400,-200,300,-200],[300,-200,200,-200]], and sending that
// same shape produced a real wire whose readback held exactly the
// values sent.
//
// Returns segments, or throws with the reason. Whole segments pass
// through untouched, because that is the form the editor reports and
// geometry read back should be able to go straight in again.
function polylineToSegments(points, what) {
  const isSegment = (p) => Array.isArray(p) && p.length === 4
    && p.every((n) => typeof n === 'number');
  const isPoint = (p) => Array.isArray(p) && p.length === 2
    && p.every((n) => typeof n === 'number');

  if (!Array.isArray(points) || points.length === 0) {
    throw new Error(`${what} is required`);
  }
  if (points.every(isSegment)) {
    return points.map((p) => [p[0], p[1], p[2], p[3]]);
  }
  if (points.length < 2) {
    throw new Error(
      `${what} needs at least 2 [x, y] points, or whole `
        + '[x1, y1, x2, y2] segments',
    );
  }
  for (const p of points) {
    if (!isPoint(p)) {
      throw new Error(
        `each entry of ${what} must be an [x, y] pair of numbers, or a `
          + 'whole [x1, y1, x2, y2] segment',
      );
    }
  }
  const segments = [];
  for (let i = 0; i + 1 < points.length; i += 1) {
    segments.push([points[i][0], points[i][1],
      points[i + 1][0], points[i + 1][1]]);
  }
  return segments;
}

handlers['sch.add_wires'] = async (params) => {
  const wires = Array.isArray(params.wires) ? params.wires : [];
  if (!wires.length) {
    throw new Error('wires must not be empty');
  }
  const results = [];
  let drawn = 0;
  let stopped = false;
  for (const wire of wires) {
    if (stopped) {
      results.push({ net: wire && wire.net, ok: false, skipped: true,
        error: 'an earlier wire in this batch failed' });
      continue;
    }
    let segments;
    try {
      segments = polylineToSegments(wire && wire.points, 'points');
    } catch (e) {
      results.push({ net: wire && wire.net, ok: false,
        error: (e && e.message) || String(e) });
      stopped = true;
      continue;
    }
    try {
      const created = await eda.sch_PrimitiveWire.create(
        segments,
        typeof wire.net === 'string' && wire.net ? wire.net : undefined,
      );
      results.push({ net: wire.net, ok: Boolean(created) });
      if (created) drawn += 1;
      else stopped = true;
    } catch (e) {
      results.push({ net: wire && wire.net, ok: false, error: String(e) });
      stopped = true;
    }
  }
  return { drawn, of: wires.length, results, stopped };
};

handlers['sch.add_wire'] = async (params) => {
  // See polylineToSegments: a wire is segments, not points, and this
  // call site was one of the three that had never drawn anything.
  const segments = polylineToSegments(params.points, 'points');
  const wire = await eda.sch_PrimitiveWire.create(
    segments,
    typeof params.net === 'string' && params.net ? params.net : undefined,
  );
  return { created: wire || null, segments: segments.length };
};

handlers['sch.add_text'] = async (params) => {
  requireNumbers(params, ['x', 'y']);
  if (typeof params.text !== 'string' || !params.text) {
    throw new Error('text is required');
  }
  const text = await eda.sch_PrimitiveText.create(
    params.x,
    params.y,
    params.text,
    typeof params.rotation === 'number' ? params.rotation : 0,
    null,
    typeof params.font === 'string' && params.font ? params.font : null,
    typeof params.font_size === 'number' ? params.font_size : null,
    params.bold === true,
    params.italic === true,
    params.underline === true,
  );
  return { created: text || null };
};

// Point pairs to the FLAT [x1, y1, x2, y2, ...] array the polygon call
// takes. The wire call accepts either form, so a helper shared between
// them would work on one and be silently reinterpreted by the other.
function flatPoints(params, minimum) {
  const points = Array.isArray(params.points) ? params.points : [];
  if (points.length < minimum) {
    throw new Error(`points must be at least ${minimum} [x, y] pairs`);
  }
  const flat = [];
  for (const p of points) {
    if (!Array.isArray(p) || p.length !== 2
        || typeof p[0] !== 'number' || typeof p[1] !== 'number') {
      throw new Error('each point must be a pair of numbers, [x, y]');
    }
    flat.push(p[0], p[1]);
  }
  return flat;
}

// The electrical character of a pin, which is what ERC checks. Getting
// it wrong does not draw differently: two outputs tied together look
// exactly like an output driving an input, and only ERC can tell.
const PIN_TYPES = ['BI', 'GROUND', 'HIZ', 'IN', 'OPEN_COLLECTOR',
  'OPEN_EMITTER', 'OUT', 'PASSIVE', 'POWER', 'TERMINATOR', 'UNDEFINED'];

handlers['sch.add_bus'] = async (params) => {
  // The bus NAME is what carries its members, e.g. D[0..7]. A bus drawn
  // without one is a thick line: it looks like a bus, groups nothing,
  // and the signals a reader assumes are in it are not.
  if (typeof params.name !== 'string' || !params.name) {
    throw new Error('name is required, e.g. "D[0..7]"');
  }
  const segments = polylineToSegments(params.points, 'points');
  const bus = await eda.sch_PrimitiveBus.create(params.name, segments);
  return { created: bus || null, segments: segments.length };
};

handlers['sch.add_pins'] = async (params) => {
  const pins = Array.isArray(params.pins) ? params.pins : [];
  if (!pins.length) {
    throw new Error('pins must not be empty');
  }
  const types = injectedEnum('ESCH_PrimitivePinType');
  const results = [];
  let placed = 0;
  let stopped = false;
  for (const pin of pins) {
    if (stopped) {
      results.push({ pin_number: pin && pin.pin_number, ok: false,
        skipped: true, error: 'an earlier pin in this batch failed' });
      continue;
    }
    const typeName = String((pin && pin.pin_type) || 'UNDEFINED')
      .toUpperCase();
    if (!PIN_TYPES.includes(typeName) || types[typeName] === undefined) {
      results.push({ pin_number: pin && pin.pin_number, ok: false,
        error: `pin_type must be one of: ${PIN_TYPES.join(', ')}` });
      stopped = true;
      continue;
    }
    try {
      const created = await eda.sch_PrimitivePin.create(
        pin.x,
        pin.y,
        pin.pin_number,
        typeof pin.name === 'string' ? pin.name : undefined,
        typeof pin.rotation === 'number' ? pin.rotation : 0,
        typeof pin.length === 'number' ? pin.length : undefined,
        null,
        undefined,
        types[typeName],
      );
      results.push({ pin_number: pin.pin_number, ok: Boolean(created) });
      if (created) placed += 1;
      else stopped = true;
    } catch (e) {
      results.push({ pin_number: pin && pin.pin_number, ok: false,
        error: String(e) });
      stopped = true;
    }
  }
  return { placed, of: pins.length, results, stopped };
};

handlers['sch.add_pin'] = async (params) => {
  requireNumbers(params, ['x', 'y']);
  if (typeof params.pin_number !== 'string' || !params.pin_number) {
    // The pin number is what ties a symbol to its footprint's pads.
    // Without it the part draws and cannot be matched to a package.
    throw new Error('pin_number is required');
  }
  const typeName = String(params.pin_type || 'UNDEFINED').toUpperCase();
  if (!PIN_TYPES.includes(typeName)) {
    throw new Error(`pin_type must be one of: ${PIN_TYPES.join(', ')}`);
  }
  const types = injectedEnum('ESCH_PrimitivePinType');
  const pinType = types[typeName];
  if (pinType === undefined) {
    throw new Error(
      `ESCH_PrimitivePinType has no member ${typeName} in this runtime`);
  }
  const pin = await eda.sch_PrimitivePin.create(
    params.x,
    params.y,
    params.pin_number,
    typeof params.name === 'string' ? params.name : undefined,
    typeof params.rotation === 'number' ? params.rotation : 0,
    typeof params.length === 'number' ? params.length : undefined,
    null,
    undefined,
    pinType,
  );
  return { created: pin || null };
};

handlers['sch.add_arc'] = async (params) => {
  // Three points, not a centre and a sweep: start, a REFERENCE point
  // the arc passes through, and end. Feeding a centre as the middle
  // pair draws an arc through the centre, which is a plausible-looking
  // curve in the wrong place.
  requireNumbers(params, [
    'start_x', 'start_y', 'reference_x', 'reference_y', 'end_x', 'end_y',
  ]);
  const arc = await eda.sch_PrimitiveArc.create(
    params.start_x, params.start_y,
    params.reference_x, params.reference_y,
    params.end_x, params.end_y);
  return { created: arc || null };
};

handlers['sch.add_circle'] = async (params) => {
  requireNumbers(params, ['x', 'y', 'radius']);
  if (params.radius <= 0) {
    throw new Error('radius must be greater than zero');
  }
  const circle = await eda.sch_PrimitiveCircle.create(
    params.x, params.y, params.radius);
  return { created: circle || null };
};

handlers['sch.add_polygon'] = async (params) => {
  const polygon = await eda.sch_PrimitivePolygon.create(
    flatPoints(params, 3));
  return { created: polygon || null };
};

handlers['sch.selection'] = async () => ({
  primitives: (await eda.sch_SelectControl.getAllSelectedPrimitives()) || [],
});

handlers['sch.select'] = async (params) => {
  const ids = Array.isArray(params.primitive_ids) ? params.primitive_ids : [];
  if (!ids.length) {
    throw new Error('primitive_ids is required and must not be empty');
  }
  return {
    selected: await eda.sch_SelectControl.doSelectPrimitives(ids) === true,
    count: ids.length,
  };
};

handlers['sch.clear_selection'] = async () => ({
  cleared: await eda.sch_SelectControl.clearSelected() === true,
});

handlers['sch.add_rectangle'] = async (params) => {
  requireNumbers(params, ['x', 'y', 'width', 'height']);
  // x, y is the TOP-LEFT corner, not a centre and not a bottom-left
  // one. Getting that wrong puts the rectangle a full height away from
  // where it was asked for, which still looks like a plausible drawing.
  const rect = await eda.sch_PrimitiveRectangle.create(
    params.x,
    params.y,
    params.width,
    params.height,
    typeof params.corner_radius === 'number' ? params.corner_radius : 0,
    typeof params.rotation === 'number' ? params.rotation : 0,
  );
  return { created: rect || null };
};

// Each primitive class deletes only its own kind, so the caller says
// which. Dispatching on a name keeps the id-to-class question with the
// caller, who knows what they created, instead of guessing here from
// the shape of an id.
const DELETERS = {
  line: () => eda.pcb_PrimitiveLine,
  arc: () => eda.pcb_PrimitiveArc,
  via: () => eda.pcb_PrimitiveVia,
  text: () => eda.pcb_PrimitiveString,
  pad: () => eda.pcb_PrimitivePad,
  fill: () => eda.pcb_PrimitiveFill,
  region: () => eda.pcb_PrimitiveRegion,
  pour: () => eda.pcb_PrimitivePour,
  component: () => eda.pcb_PrimitiveComponent,
};

handlers['pcb.delete_primitives'] = async (params) => {
  if (params.confirm !== true) {
    // The Python tool checks this too. Both halves check because this
    // channel is reachable by anything speaking the protocol, so it
    // cannot assume a caller already asked.
    throw new Error(
      'delete_primitives removes objects and is not undoable from here. '
        + 'Pass confirm=true if that is intended.',
    );
  }
  const kind = String(params.kind || '').toLowerCase();
  if (!Object.prototype.hasOwnProperty.call(DELETERS, kind)) {
    throw new Error(
      `kind must be one of: ${Object.keys(DELETERS).join(', ')}`,
    );
  }
  const ids = Array.isArray(params.primitive_ids) ? params.primitive_ids : [];
  if (!ids.length) {
    throw new Error('primitive_ids is required and must not be empty');
  }
  const deleted = await DELETERS[kind]().delete(ids);
  return { deleted: deleted === true, count: ids.length, kind };
};

handlers['pcb.navigate'] = async (params) => {
  const { x, y } = params;
  if (typeof x !== 'number' || typeof y !== 'number') {
    throw new Error('x and y are required and must be numbers');
  }
  await eda.pcb_Document.navigateToCoordinates(x, y);
  return { x: x, y: y };
};

handlers['pcb.auto_route'] = async () => {
  // THERE IS NO AUTOROUTE METHOD. This called
  // eda.pcb_Document.autoRouting(), which does not exist: the live
  // runtime lists nineteen methods on pcb_Document and that is not one
  // of them, so every call died on "is not a function" after passing
  // the confirm gate. The handler then returned {routed: true}, which
  // is what it would have reported had the call succeeded.
  //
  // What the API does expose is the other half of the round trip:
  // importAutoRouteSesFile and importAutoRouteJsonFile take routing
  // produced by an external router. So the capability is import, not
  // run, and saying so is more useful than a TypeError.
  throw new Error(
    'EasyEDA does not expose an autorouter to extensions. pcb_Document '
      + 'has no autoRouting method; what it has is '
      + 'importAutoRouteSesFile and importAutoRouteJsonFile, which load '
      + 'routing produced elsewhere. Route with the editor\'s own '
      + 'autorouter, or route externally and import the result.',
  );
};

const PORT_DIRECTIONS = ['IN', 'OUT', 'BI'];

handlers['sch.create_net_port'] = async (params) => {
  const name = params.name;
  if (!name) throw new Error('name is required');
  requireNumbers(params, ['x', 'y']);
  const direction = String(params.direction || 'BI').toUpperCase();
  if (!PORT_DIRECTIONS.includes(direction)) {
    throw new Error(
      `direction must be one of: ${PORT_DIRECTIONS.join(', ')}`,
    );
  }
  const port = await eda.sch_PrimitiveComponent.createNetPort(
    direction, name, params.x, params.y,
    typeof params.rotation === 'number' ? params.rotation : 0,
    params.mirror === true,
  );
  return { name, created: port || null };
};

// Power and ground glyphs. EasyEDA calls these net FLAGS, and they are a
// different call from a net port: a port is a sheet-level connector,
// a flag is the rail symbol. Using a port where the convention wants a
// flag draws a schematic that reads wrong to anyone used to the
// convention, and connects correctly, so nothing catches it.
const NET_FLAGS = ['Power', 'Ground', 'AnalogGround', 'ProtectGround'];

handlers['sch.create_net_flag'] = async (params) => {
  const name = params.name;
  if (!name) throw new Error('name is required');
  requireNumbers(params, ['x', 'y']);
  const kind = String(params.kind || 'Power');
  const match = NET_FLAGS.find(
    (f) => f.toLowerCase() === kind.toLowerCase());
  if (!match) {
    throw new Error(`kind must be one of: ${NET_FLAGS.join(', ')}`);
  }
  const flag = await eda.sch_PrimitiveComponent.createNetFlag(
    match, name, params.x, params.y,
    typeof params.rotation === 'number' ? params.rotation : 0,
    params.mirror === true,
  );
  return { name, kind: match, created: flag || null };
};

// ---- authoring library items ----------------------------------------
//
// Three separate objects, and the order matters: a symbol and a
// footprint are drawings, and a DEVICE is what binds them into
// something placeable. Creating the two drawings and stopping leaves a
// library nobody can place from, which looks like progress.

function libraryUuidOf(params) {
  if (typeof params.library_uuid !== 'string' || !params.library_uuid) {
    throw new Error(
      'library_uuid is required. It comes from lib.list_libraries; '
        + 'there is no default library to fall back on.',
    );
  }
  return params.library_uuid;
}

function itemNameOf(params) {
  if (typeof params.name !== 'string' || !params.name) {
    throw new Error('name is required');
  }
  return params.name;
}

handlers['lib.create_symbol'] = async (params) => {
  const uuid = await eda.lib_Symbol.create(
    libraryUuidOf(params),
    itemNameOf(params),
    undefined,
    undefined,
    typeof params.description === 'string' ? params.description : undefined,
  );
  return { uuid: uuid || null, created: Boolean(uuid) };
};

handlers['lib.create_footprint'] = async (params) => {
  const uuid = await eda.lib_Footprint.create(
    libraryUuidOf(params),
    itemNameOf(params),
    undefined,
    typeof params.description === 'string' ? params.description : undefined,
  );
  return { uuid: uuid || null, created: Boolean(uuid) };
};

handlers['lib.create_device'] = async (params) => {
  const association = {};
  if (params.symbol_uuid) {
    association.symbol = {
      uuid: params.symbol_uuid,
      libraryUuid: params.symbol_library_uuid || params.library_uuid,
    };
  }
  if (params.footprint_uuid) {
    association.footprint = {
      uuid: params.footprint_uuid,
      libraryUuid: params.footprint_library_uuid || params.library_uuid,
    };
  }
  if (params.model_3d_uuid) {
    association.model3D = {
      uuid: params.model_3d_uuid,
      libraryUuid: params.model_3d_library_uuid || params.library_uuid,
    };
  }
  // A device with neither a symbol nor a footprint places nothing. The
  // API would accept it and report a uuid, so the empty shell would
  // read as a created part until somebody tried to use it.
  if (!association.symbol && !association.footprint) {
    throw new Error(
      'give at least symbol_uuid or footprint_uuid; a device bound to '
        + 'neither cannot be placed and would still report success',
    );
  }
  const uuid = await eda.lib_Device.create(
    libraryUuidOf(params),
    itemNameOf(params),
    undefined,
    association,
    typeof params.description === 'string' ? params.description : undefined,
  );
  return { uuid: uuid || null, created: Boolean(uuid) };
};

// Opening a library item makes it the ACTIVE document, after which the
// ordinary drawing commands apply to it. That is how a symbol or
// footprint gets its geometry here: there is no separate library
// drawing API, and inventing one would be a second way to draw the same
// shapes.
//
// The uuid pair is (item, library) in that order for these calls, which
// is the reverse of lib.create_* where the library comes first. Getting
// it backwards finds nothing and reports no error.

function itemAndLibrary(params) {
  const uuid = params.uuid;
  const libraryUuid = params.library_uuid;
  if (typeof uuid !== 'string' || !uuid
      || typeof libraryUuid !== 'string' || !libraryUuid) {
    throw new Error('uuid and library_uuid are both required');
  }
  return [uuid, libraryUuid];
}

// Which kind of library thing a call is about. EasyEDA's own enum, read
// from the runtime rather than copied, for the same reason the layer
// ids are: a table here would be a second copy of their numbering.
const LIBRARY_KINDS = ['CBB', 'SYMBOL', 'DEVICE', 'FOOTPRINT', 'MODEL',
  'PANEL_LIBRARY'];

function libraryKind(params) {
  const name = String(params.kind || 'SYMBOL').toUpperCase();
  if (!LIBRARY_KINDS.includes(name)) {
    throw new Error(`kind must be one of: ${LIBRARY_KINDS.join(', ')}`);
  }
  const kinds = injectedEnum('ELIB_LibraryType');
  const value = kinds[name];
  if (value === undefined) {
    throw new Error(
      `ELIB_LibraryType has no member ${name} in this runtime`);
  }
  return value;
}

// ---- creating documents ---------------------------------------------
//
// The from-scratch path: a project, then a schematic and a board inside
// it. Without these the backend can only work on something a human made
// first, which is the difference between editing a design and authoring
// one.

handlers['proj.create_schematic'] = async (params) => {
  const uuid = await eda.dmt_Schematic.createSchematic(
    typeof params.name === 'string' && params.name ? params.name : undefined);
  return { uuid: uuid || null, created: Boolean(uuid) };
};

handlers['proj.create_schematic_page'] = async (params) => {
  if (typeof params.uuid !== 'string' || !params.uuid) {
    throw new Error('uuid of the schematic is required');
  }
  const uuid = await eda.dmt_Schematic.createSchematicPage(params.uuid);
  return { uuid: uuid || null, created: Boolean(uuid) };
};

handlers['proj.create_pcb'] = async (params) => {
  const uuid = await eda.dmt_Pcb.createPcb(
    typeof params.name === 'string' && params.name ? params.name : undefined);
  return { uuid: uuid || null, created: Boolean(uuid) };
};

handlers['sch.set_title_block'] = async (params) => {
  const fields = params.fields;
  const show = params.show !== false;
  if (fields !== undefined
      && (typeof fields !== 'object' || fields === null
          || Array.isArray(fields))) {
    throw new Error('fields must be an object of {name: {value}}');
  }
  const applied = await eda.dmt_Schematic.modifySchematicPageTitleBlock(
    show, fields || undefined);
  return { applied: applied === true, show };
};

handlers['sys.workspaces'] = async () => ({
  workspaces: (await eda.dmt_Workspace.getAllWorkspacesInfo()) || [],
  current: (await eda.dmt_Workspace.getCurrentWorkspaceInfo()) || null,
});

// The whole active document, as text. This is what makes a checkpoint
// possible on a backend with no filesystem the server can reach: the
// document travels as a string rather than as a path.
handlers['sys.document_source'] = async () => ({
  source: await eda.sys_FileManager.getDocumentSource(),
  document: await currentDocumentKind(),
  name: await boardName(),
});

handlers['sys.set_document_source'] = async (params) => {
  if (typeof params.source !== 'string' || !params.source) {
    throw new Error('source is required');
  }
  if (params.confirm !== true) {
    throw new Error(
      'set_document_source REPLACES the whole open document. Pass '
        + 'confirm=true if that is intended.',
    );
  }
  // Returns false on a source it cannot parse, which is a refusal
  // rather than a throw, so it is reported as one.
  const applied = await eda.sys_FileManager.setDocumentSource(params.source);
  return { restored: applied === true };
};

handlers['lib.classifications'] = async (params) => {
  const tree = await eda.lib_Classification.getAllClassificationTree(
    libraryUuidOf(params), libraryKind(params));
  return { classifications: tree || [] };
};

handlers['lib.open_symbol'] = async (params) => {
  const [uuid, libraryUuid] = itemAndLibrary(params);
  const opened = await eda.lib_Symbol.openInEditor(uuid, libraryUuid);
  return { opened: opened || null };
};

handlers['lib.open_footprint'] = async (params) => {
  const [uuid, libraryUuid] = itemAndLibrary(params);
  const opened = await eda.lib_Footprint.openInEditor(uuid, libraryUuid);
  return { opened: opened || null };
};

handlers['lib.modify_symbol'] = async (params) => {
  const [uuid, libraryUuid] = itemAndLibrary(params);
  if (!params.name && !params.description) {
    throw new Error(
      'give a name or a description; an empty change reports success '
        + 'while doing nothing',
    );
  }
  return {
    modified: await eda.lib_Symbol.modify(
      uuid, libraryUuid,
      typeof params.name === 'string' && params.name
        ? params.name : undefined,
      undefined,
      typeof params.description === 'string' && params.description
        ? params.description : undefined) === true,
  };
};

handlers['lib.modify_footprint'] = async (params) => {
  const [uuid, libraryUuid] = itemAndLibrary(params);
  if (!params.name && !params.description) {
    throw new Error(
      'give a name or a description; an empty change reports success '
        + 'while doing nothing',
    );
  }
  return {
    modified: await eda.lib_Footprint.modify(
      uuid, libraryUuid,
      typeof params.name === 'string' && params.name
        ? params.name : undefined,
      undefined,
      typeof params.description === 'string' && params.description
        ? params.description : undefined) === true,
  };
};

handlers['lib.get_device'] = async (params) => {
  const [uuid, libraryUuid] = itemAndLibrary(params);
  const device = (await eda.lib_Device.get(uuid, libraryUuid)) || null;
  if (!device) return { device: null, model_3d: null, model_3d_source: 'absent' };

  // lib_Device.get DROPS THE 3D MODEL. Measured on one uuid: search
  // reports model3DUuid and model3DName for it, and get returns an
  // association holding only symbol, footprint and images. So a caller
  // reading get concludes the part has no 3D model, which is a false
  // negative rather than a missing field, and an audit built on it
  // would report every device as unmodelled.
  //
  // Backfilled from search and matched on uuid. Searches cap at ten, so
  // a common name can hide the row: that case is reported as
  // UNRESOLVED rather than as absent, because "we could not see it" and
  // "it is not there" call for different next steps.
  const assoc = device.association || {};
  if (assoc.model3D || assoc.model3DUuid) {
    return {
      device: device,
      model_3d: assoc.model3D || { uuid: assoc.model3DUuid },
      model_3d_source: 'get',
    };
  }
  if (!device.name) {
    return { device: device, model_3d: null, model_3d_source: 'unresolved' };
  }
  try {
    const rows = (await eda.lib_Device.search(device.name)) || [];
    const row = rows.find((r) => r && r.uuid === uuid);
    if (!row) {
      return { device: device, model_3d: null, model_3d_source: 'unresolved' };
    }
    return {
      device: device,
      model_3d: row.model3DUuid
        ? { uuid: row.model3DUuid, name: row.model3DName || null }
        : null,
      model_3d_source: 'search',
    };
  } catch (e) {
    return { device: device, model_3d: null, model_3d_source: 'unresolved' };
  }
};

handlers['lib.copy_device'] = async (params) => {
  const [uuid, libraryUuid] = itemAndLibrary(params);
  if (typeof params.target_library_uuid !== 'string'
      || !params.target_library_uuid) {
    throw new Error('target_library_uuid is required');
  }
  const created = await eda.lib_Device.copy(
    uuid, libraryUuid, params.target_library_uuid, undefined,
    typeof params.new_name === 'string' && params.new_name
      ? params.new_name : undefined);
  return { uuid: created || null, copied: Boolean(created) };
};

handlers['lib.delete_symbol'] = async (params) => {
  const [uuid, libraryUuid] = itemAndLibrary(params);
  if (params.confirm !== true) {
    throw new Error(
      'delete_symbol removes the drawing from the library. Pass '
        + 'confirm=true if that is intended.',
    );
  }
  return {
    deleted: await eda.lib_Symbol.delete(uuid, libraryUuid) === true,
  };
};

handlers['lib.delete_footprint'] = async (params) => {
  const [uuid, libraryUuid] = itemAndLibrary(params);
  if (params.confirm !== true) {
    throw new Error(
      'delete_footprint removes the land pattern from the library. '
        + 'Pass confirm=true if that is intended.',
    );
  }
  return {
    deleted: await eda.lib_Footprint.delete(uuid, libraryUuid) === true,
  };
};

// BINDING A DEVICE TO ITS PARTS, including the 3D model.
//
// lib_Device.modify's fifth parameter is an `association` object:
//
//   { symbol?: {uuid, libraryUuid}, footprint?: {uuid, libraryUuid},
//     model3D?: {uuid, libraryUuid}, imageData? }
//
// which is how a 3D model reaches a part on this backend, matching what
// lib_link_3d_model does on the Altium side. It was recorded as a gap
// on the assumption the extension API might not expose it; it does, and
// LIB_3DModel carries a full create/get/search/modify/copy/delete
// surface besides.
//
// Each binding is optional and only what was asked for is sent, because
// passing null CLEARS a binding: footprint and model3D are typed
// `| null` explicitly. Sending a blank one would unbind the part while
// reporting success.
handlers['lib.link_device_parts'] = async (params) => {
  const [uuid, libraryUuid] = itemAndLibrary(params);
  const pair = (item) => {
    if (!item || !item.uuid) return null;
    return {
      uuid: item.uuid,
      libraryUuid: item.library_uuid || libraryUuid,
    };
  };
  const association = {};
  const model3d = pair(params.model_3d);
  const footprint = pair(params.footprint);
  const symbol = pair(params.symbol);
  if (model3d) association.model3D = model3d;
  if (footprint) association.footprint = footprint;
  if (symbol) association.symbol = symbol;
  if (!Object.keys(association).length) {
    throw new Error(
      'give model_3d, footprint or symbol, each as {uuid, library_uuid}. '
      + 'An empty association would report success while binding nothing.');
  }
  const ok = await eda.lib_Device.modify(
    uuid, libraryUuid, undefined, undefined, association) === true;
  return { linked: ok, bound: Object.keys(association) };
};

handlers['lib.modify_device'] = async (params) => {
  const [uuid, libraryUuid] = itemAndLibrary(params);
  if (!params.name && !params.description) {
    throw new Error(
      'give a name or a description; an empty change reports success '
        + 'while doing nothing',
    );
  }
  // DEVICE TAKES SEVEN, SYMBOL AND FOOTPRINT TAKE FIVE. The three
  // modify methods are NOT the same shape, so the call cannot be shared
  // between them:
  //
  //   lib_Device.modify(uuid, libraryUuid, name, classification,
  //                     association, description, property)
  //   lib_Symbol.modify(uuid, libraryUuid, name, classification, description)
  //   lib_Footprint.modify(same as symbol)
  //
  // Device carries an `association` slot at position 5 that the other
  // two do not, so passing description there put it where the symbol,
  // footprint and 3D model bindings live. The neighbouring symbol and
  // footprint handlers are correct as they stand; do not align them.
  return {
    modified: await eda.lib_Device.modify(
      uuid, libraryUuid,
      typeof params.name === 'string' && params.name
        ? params.name : undefined,
      undefined,   // classification
      undefined,   // association: symbol / footprint / 3D bindings
      typeof params.description === 'string' && params.description
        ? params.description : undefined) === true,
  };
};

handlers['lib.delete_device'] = async (params) => {
  const [uuid, libraryUuid] = itemAndLibrary(params);
  if (params.confirm !== true) {
    throw new Error(
      'delete_device removes the part from the library. Pass '
        + 'confirm=true if that is intended.',
    );
  }
  return {
    deleted: await eda.lib_Device.delete(uuid, libraryUuid) === true,
  };
};

// ---- removing documents and projects --------------------------------
//
// dmt_Schematic.deleteSchematic and deleteSchematicPage, dmt_Pcb
// .deletePcb and dmt_Project.deleteProject all exist, which is what
// makes these handlers possible. Four other project-level operations
// (annotate, variant management, replace-component and project
// parameters) have no method on any class, so they are unavailable
// rather than merely unwritten.
//
// Every one is destructive and refuses without confirm, matching the
// library deletes. The result is read rather than assumed: these
// answer falsey when the editor declines, exactly as modify does.

function requireConfirm(params, what) {
  if (params.confirm !== true) {
    throw new Error(
      `${what} Pass confirm=true if that is intended.`);
  }
}

handlers['proj.delete_schematic'] = async (params) => {
  if (!params.uuid) throw new Error('uuid is required');
  requireConfirm(params, 'delete_schematic removes the schematic and '
    + 'every page in it.');
  return {
    deleted: await eda.dmt_Schematic.deleteSchematic(params.uuid) === true,
  };
};

handlers['proj.delete_schematic_page'] = async (params) => {
  if (!params.uuid) throw new Error('uuid is required');
  requireConfirm(params, 'delete_schematic_page removes the page and '
    + 'everything drawn on it.');
  return {
    deleted:
      await eda.dmt_Schematic.deleteSchematicPage(params.uuid) === true,
  };
};

handlers['proj.delete_pcb'] = async (params) => {
  if (!params.uuid) throw new Error('uuid is required');
  requireConfirm(params, 'delete_pcb removes the board, including its '
    + 'routing.');
  return { deleted: await eda.dmt_Pcb.deletePcb(params.uuid) === true };
};

// NOT POSSIBLE, and it never was. dmt_Project exposes createProject,
// getAllProjectsUuid, getCurrentProjectInfo, getProjectInfo,
// moveProjectToFolder and openProject. There is no deleteProject, and
// no other class offers one: the document tree can delete a board, a
// folder, a panel, a PCB, a schematic and a schematic page, but never
// the project itself.
//
// This used to call eda.dmt_Project.deleteProject, which is undefined,
// so the call threw a TypeError AFTER the caller had confirmed a
// destructive operation. Refusing outright is the honest answer, and it
// names what the API can actually delete so the caller is not left
// guessing whether they mistyped something.
handlers['proj.delete_project'] = async () => {
  throw new Error(
    'EasyEDA has no project-delete API. dmt_Project offers create, open, '
    + 'move and the info reads, and nothing anywhere deletes a project. '
    + 'You can delete the parts: proj.delete_schematic, proj.delete_pcb, '
    + 'proj.delete_panel, or the folder through the document tree. '
    + 'Delete the project itself from the EasyEDA UI.');
};

handlers['editor.close_document'] = async (params) => {
  if (!params.uuid) throw new Error('uuid is required');
  // Closing is not destructive: nothing is deleted and an unsaved
  // document is the editor's business, so no confirm here.
  const answer = await eda.dmt_EditorControl.closeDocument(params.uuid);
  if (answer === false) {
    return { uuid: params.uuid, closed: false,
      failed: 'the editor declined to close that document' };
  }
  return { uuid: params.uuid, closed: true };
};

handlers['lib.copy_symbol'] = async (params) => {
  const [uuid, libraryUuid] = itemAndLibrary(params);
  if (typeof params.target_library_uuid !== 'string'
      || !params.target_library_uuid) {
    throw new Error('target_library_uuid is required');
  }
  const created = await eda.lib_Symbol.copy(
    uuid, libraryUuid, params.target_library_uuid, undefined,
    typeof params.new_name === 'string' && params.new_name
      ? params.new_name : undefined);
  return { uuid: created || null, copied: Boolean(created) };
};

handlers['lib.copy_footprint'] = async (params) => {
  const [uuid, libraryUuid] = itemAndLibrary(params);
  if (typeof params.target_library_uuid !== 'string'
      || !params.target_library_uuid) {
    throw new Error('target_library_uuid is required');
  }
  const created = await eda.lib_Footprint.copy(
    uuid, libraryUuid, params.target_library_uuid, undefined,
    typeof params.new_name === 'string' && params.new_name
      ? params.new_name : undefined);
  return { uuid: created || null, copied: Boolean(created) };
};

handlers['lib.list_libraries'] = async () => {
  // getAllLibrariesList RETURNS AN EMPTY ARRAY, measured against a live
  // editor holding a populated system library. Reporting that as the
  // answer says there are no libraries, which is a different claim from
  // "the enumeration is not implemented" and sends a caller looking for
  // a workspace problem that does not exist.
  //
  // The four named getters do answer, so the uuids a search can be
  // scoped to are reachable even though listing them is not.
  const enumerated = (await eda.lib_LibrariesList.getAllLibrariesList()) || [];
  const named = {};
  const getters = {
    system: 'getSystemLibraryUuid',
    personal: 'getPersonalLibraryUuid',
    project: 'getProjectLibraryUuid',
    favorite: 'getFavoriteLibraryUuid',
  };
  for (const key of Object.keys(getters)) {
    try {
      named[key] = (await eda.lib_LibrariesList[getters[key]]()) || null;
    } catch (e) { named[key] = null; }
  }
  return {
    libraries: enumerated,
    enumeration_empty: enumerated.length === 0,
    known_library_uuids: named,
  };
};

// TEN IS THE DEFAULT PAGE, NOT A CEILING. This previously recorded
// that EasyEDA caps every library search at ten with no way past, on
// the strength of the SECOND argument matching nothing. The second
// argument is a library uuid, and paging lives further along:
//
//   lib_Symbol.search   (key, libraryUuid, classification, symbolType,
//                        itemsOfPage, page)
//   lib_Device.search   (same six)
//   lib_Footprint.search(key, libraryUuid, classification,
//                        itemsOfPage, page)
//   lib_3DModel.search  (same five)
//
// Note the arity differs: symbolType exists on Symbol and Device only,
// so itemsOfPage sits at position 5 for those two and position 4 for
// the other two. Passing the page size into the wrong slot is how a
// second attempt at this would quietly fail.
//
// The default page size is what ten was, so it stays as the default
// here rather than being raised silently.
const LIB_SEARCH_PAGE_SIZE = 10;

// Classes whose search carries a symbolType argument before the paging
// pair. Everything hangs off this one fact, so it is named once.
const LIB_SEARCH_HAS_SYMBOL_TYPE = ['lib_Symbol', 'lib_Device'];

// The second argument scopes the search to one library, measured: the
// system uuid returns the same ten, and the personal, project and
// favorite uuids return none for a term the system library matches.
//
// Only lib_Symbol refuses an empty query, and it refuses by NEVER
// ANSWERING rather than by throwing, so the guard there protects the
// connection. The other three return a default page for an empty
// query, and guarding them invented a restriction the editor does not
// have while advertising the parameter as optional.
async function librarySearch(className, params, allowEmpty) {
  const query = params.query || '';
  if (!query && !allowEmpty) {
    throw new Error(
      'query is required: ' + className + '.search does not answer an '
      + 'empty query, and the call hangs rather than being refused');
  }
  const libraryUuid = params.library_uuid || '';
  const instance = eda[className];
  const size = Number(params.items_of_page) > 0
    ? Number(params.items_of_page) : LIB_SEARCH_PAGE_SIZE;
  const page = Number(params.page) > 0 ? Number(params.page) : 1;

  // Built positionally, because the classes disagree on how many
  // arguments precede the paging pair. undefined leaves an optional
  // slot at its default rather than sending an empty value.
  const args = [query, libraryUuid || undefined, undefined];
  if (LIB_SEARCH_HAS_SYMBOL_TYPE.indexOf(className) !== -1) {
    args.push(undefined);            // symbolType
  }
  args.push(size, page);

  const found = (await instance.search.apply(instance, args)) || [];
  return {
    found: found,
    meta: {
      result_count: found.length,
      page: page,
      items_of_page: size,
      // A full page means there is probably another one. Unlike before,
      // there is now something the caller can do about it.
      more_likely: found.length >= size,
      next_page: found.length >= size ? page + 1 : null,
      library_uuid: libraryUuid || null,
      query: query,
    },
  };
}

handlers['lib.search_devices'] = async (params) => {
  const out = await librarySearch('lib_Device', params, true);
  return Object.assign({ devices: out.found }, out.meta);
};

handlers['lib.devices_by_lcsc'] = async (params) => {
  const ids = params.lcsc_ids;
  if (!Array.isArray(ids) || ids.length === 0) {
    throw new Error('lcsc_ids must be a non-empty array');
  }
  return { devices: (await eda.lib_Device.getByLcscIds(ids)) || [] };
};

handlers['lib.search_symbols'] = async (params) => {
  // The one class that hangs on an empty query, so the one that keeps
  // the guard.
  const out = await librarySearch('lib_Symbol', params, false);
  return Object.assign({ symbols: out.found }, out.meta);
};

handlers['lib.search_footprints'] = async (params) => {
  const out = await librarySearch('lib_Footprint', params, true);
  return Object.assign({ footprints: out.found }, out.meta);
};

// AN OBJECT, AND A BLOB BACK. Both render calls take a single source
// object carrying BOTH uuids, not a bare id:
//
//   getRenderImage({ symbolUuid, libraryUuid, subPartName? })
//   getRenderImage({ footprintUuid, libraryUuid })
//
// and both return Promise<Blob | undefined>, which JSON.stringify
// flattens to {} exactly as the manufacture exports did before
// packedFile existed.
//
// Passing a bare uuid was recorded as the editor never answering, and
// both commands were listed in NEVER_ANSWERED on that basis. The ids
// were good; the argument was the wrong shape. library_uuid was never
// collected at all, so it has to come from the caller now.
handlers['lib.symbol_image'] = async (params) => {
  const uuid = params.uuid;
  if (!uuid) throw new Error('uuid is required');
  if (!params.library_uuid) {
    throw new Error('library_uuid is required: getRenderImage takes '
      + '{ symbolUuid, libraryUuid } and cannot resolve a symbol from '
      + 'its own uuid alone. lib.search_symbols reports it per hit.');
  }
  // A picture is the only way to catch geometry that scores well and
  // looks wrong, which is a recurring failure in this project's own
  // library work.
  const source = { symbolUuid: uuid, libraryUuid: params.library_uuid };
  if (params.sub_part_name) source.subPartName = params.sub_part_name;
  return { image: await packedFile(
    await eda.lib_Symbol.getRenderImage(source)) };
};

handlers['lib.footprint_image'] = async (params) => {
  const uuid = params.uuid;
  if (!uuid) throw new Error('uuid is required');
  if (!params.library_uuid) {
    throw new Error('library_uuid is required: getRenderImage takes '
      + '{ footprintUuid, libraryUuid } and cannot resolve a footprint '
      + 'from its own uuid alone. lib.search_footprints reports it.');
  }
  return { image: await packedFile(
    await eda.lib_Footprint.getRenderImage(
      { footprintUuid: uuid, libraryUuid: params.library_uuid })) };
};

handlers['proj.list'] = async () => ({
  project_uuids: (await eda.dmt_Project.getAllProjectsUuid()) || [],
});

handlers['proj.get'] = async (params) => {
  if (typeof params.uuid !== 'string' || !params.uuid) {
    throw new Error('uuid is required');
  }
  return { project: (await eda.dmt_Project.getProjectInfo(params.uuid))
    || null };
};

handlers['proj.open'] = async (params) => {
  if (typeof params.uuid !== 'string' || !params.uuid) {
    throw new Error('uuid is required');
  }
  return { opened: await eda.dmt_Project.openProject(params.uuid) === true };
};

handlers['proj.create'] = async (params) => {
  if (typeof params.name !== 'string' || !params.name) {
    throw new Error('name is required');
  }
  const uuid = await eda.dmt_Project.createProject(
    params.name,
    typeof params.internal_name === 'string' && params.internal_name
      ? params.internal_name : undefined,
    typeof params.team_uuid === 'string' && params.team_uuid
      ? params.team_uuid : undefined,
    typeof params.folder_uuid === 'string' && params.folder_uuid
      ? params.folder_uuid : undefined,
    typeof params.description === 'string' && params.description
      ? params.description : undefined,
  );
  return { uuid: uuid || null, created: Boolean(uuid) };
};

handlers['proj.info'] = async () => ({
  project: (await eda.dmt_Project.getCurrentProjectInfo()) || null,
});

handlers['sch.list_schematics'] = async () => ({
  schematics: (await eda.dmt_Schematic.getAllSchematicsInfo()) || [],
});

handlers['sch.list_pages'] = async () => ({
  pages:
    (await eda.dmt_Schematic.getCurrentSchematicAllSchematicPagesInfo()) || [],
});

// Pins placed directly on a document, which is what a SYMBOL holds.
// A schematic's part pins are not here; those come from the netlist.
handlers['sch.pins'] = async () => ({
  pins: (await eda.sch_PrimitivePin.getAll()) || [],
});

handlers['sch.assembly_variants'] = async () => ({
  variants: (await eda.sch_ManufactureData.getAssemblyVariantsConfigs()) || [],
});

handlers['export.sch_bom'] = async () => ({
  file: await packedFile(await eda.sch_ManufactureData.getBomFile()),
});

handlers['export.simulation_netlist'] = async () => ({
  file: await packedFile(await eda.sch_ManufactureData.getSimulationNetlistFile()),
});

handlers['editor.render_image'] = async () => {
  // The only way to see what the board actually looks like. This
  // project's own experience is that geometry can score well and look
  // wrong, and no numeric check substitutes for looking.
  //
  // PACKED, like every other binary the editor hands back. This
  // returned the raw value, and the raw value is a Blob:
  // JSON.stringify(blob) is {}, so the whole reply serialised to an
  // empty object and the image vanished on the way out. The tool then
  // reported success with nothing in it, which is the worst outcome
  // for the one check that exists to make somebody LOOK.
  const packed = await packedFile(
    await eda.dmt_EditorControl.getCurrentRenderedAreaImage());
  if (packed === null) {
    return {
      rendered: false,
      failed: 'the editor returned no image. Nothing was rendered, so '
        + 'this is not a picture of an empty board.',
    };
  }
  return { rendered: true, image: packed };
};

handlers['pcb.selection'] = async () => ({
  selected: (await eda.pcb_SelectControl.getAllSelectedPrimitives()) || [],
});

handlers['pcb.clear_selection'] = async () => {
  await eda.pcb_SelectControl.clearSelected();
  return { cleared: true };
};

// CROSS-PROBING IS BY DESIGNATOR, NOT BY PRIMITIVE ID. The signature is
//
//   doCrossProbeSelect(components?, pins?, nets?, highlight?, select?)
//
// where components are designators ('U1'), pins are designator_pin
// ('U1_1') and nets are net names. This used to be handed
// params.primitive_ids, which is a different identifier space
// entirely, so it selected nothing and reported a count anyway.
//
// It was also a duplicate: pcb.select already selects by primitive id
// through doSelectPrimitives, correctly, and reads the boolean back.
// So this is now the operation its name always claimed, which is the
// one the API actually offers and the one pcb.select cannot do.
handlers['pcb.cross_probe'] = async (params) => {
  const list = (v) => (Array.isArray(v) ? v.filter(Boolean) : []);
  const components = list(params.designators);
  const pins = list(params.pins);
  const nets = list(params.nets);
  if (!components.length && !pins.length && !nets.length) {
    throw new Error(
      'give designators, pins or nets. Cross-probing selects by '
      + "designator ('U1'), pin ('U1_1') or net name, not by primitive "
      + 'id. To select by primitive id use pcb.select.');
  }
  // Both flags default true: a cross-probe that neither highlights nor
  // selects puts nothing in front of anyone, which is the whole point.
  const highlight = params.highlight !== false;
  const select = params.select !== false;
  const ok = await eda.pcb_SelectControl.doCrossProbeSelect(
    components, pins, nets, highlight, select);
  // Read the answer rather than assuming it. The old reply counted the
  // ids it was given, so it reported success for a call that matched
  // nothing at all.
  return {
    cross_probed: ok === true,
    designators: components,
    pins: pins,
    nets: nets,
  };
};

handlers['pcb.modify_component'] = async (params) => {
  const { primitive_id, changes } = params;
  if (!primitive_id) throw new Error('primitive_id is required');
  if (!changes || typeof changes !== 'object' ||
      Object.keys(changes).length === 0) {
    throw new Error(
      'changes must name at least one property; an empty change would ' +
        'report success while doing nothing',
    );
  }
  // The RESULT is read, not discarded.
  //
  // modify answers falsey when the editor will not make the change; it
  // does not throw. Ignoring that and reporting `changed` from the keys
  // we ASKED for told the caller the component had moved when it had
  // not, and the only way to find out otherwise was to look at the
  // board. Measured against a declining fake: this
  // returned {"primitive_id":"P1","changed":["x","y"]} for a change
  // that never happened.
  const applied = await eda.pcb_PrimitiveComponent.modify(
    primitive_id, changes);
  if (applied === false || applied === null || applied === undefined) {
    return {
      primitive_id: primitive_id,
      modified: 0,
      requested: Object.keys(changes),
      failed: 'the editor declined the change and it was NOT applied',
    };
  }
  return {
    primitive_id: primitive_id,
    modified: 1,
    changed: Object.keys(changes),
  };
};

handlers['pcb.arcs'] = async () => ({
  arcs: (await eda.pcb_PrimitiveArc.getAll()) || [],
});

handlers['pcb.regions'] = async () => ({
  regions: (await eda.pcb_PrimitiveRegion.getAll()) || [],
});

handlers['sch.wires'] = async () => ({
  wires: (await eda.sch_PrimitiveWire.getAll()) || [],
});

//: Read a collection the fast way, and the other way if that stalls.
//:
//: Measured: sch.attributes, pcb.attributes, pcb.strings
//: and pcb.poured each accepted a getAll() and never answered. Between
//: them they block three board audits and one library check, because
//: the data simply never arrives. WHY they hang is not established and
//: may be the editor's business rather than ours.
//:
//: Every one of those classes also offers getAllPrimitiveId() and
//: get(id), which is a second route to the same rows. Whether that
//: route survives when getAll() does not cannot be answered from this
//: side, because the hang lives in the editor and nothing here can
//: reproduce it. So both are tried and the answer carries WHICH ONE
//: replied, which is the part that settles the question on the next
//: live run rather than after another round of guessing.
//:
//: getAll keeps a short budget of its own rather than the dispatcher's
//: full ceiling. A fallback that waited for the outer timeout would
//: never run: the dispatcher ends the whole command at that point.
const FAST_READ_MS = 4000;

//: Shape a readAll result as a handler reply: the rows under the name
//: the command has always used, plus the route that produced them.
//: Kept in one place so the four callers cannot drift into reporting
//: the route three different ways, which is how an aggregate ends up
//: unable to read its own inputs.
function withRoute(key, read) {
  const out = {};
  out[key] = read.rows;
  out.via = read.via;
  if (read.ids_seen !== undefined) out.ids_seen = read.ids_seen;
  if (read.getall_failed !== undefined) out.getall_failed = read.getall_failed;
  if (read.unreadable !== undefined) out.unreadable = read.unreadable;
  return out;
}

async function readAll(api, name) {
  let timer = null;
  try {
    const rows = await Promise.race([
      api.getAll(),
      new Promise((_, reject) => {
        timer = setTimeout(
          () => reject(new Error(`${name}.getAll did not answer in `
            + `${FAST_READ_MS}ms`)), FAST_READ_MS);
      }),
    ]).finally(() => { if (timer !== null) clearTimeout(timer); });
    // An empty collection is a real answer: a board with no such
    // primitives reads zero. Treating that as a failure would make
    // every clean board pay for the per-item path and would report the
    // wrong route for a call that worked.
    return { rows: rows || [], via: 'getAll' };
  } catch (e) {
    // Fall through. The reason is kept so a caller can tell a stall
    // from a refusal, which call for different next steps.
    var why = String((e && e.message) || e);
  }

  const ids = (await api.getAllPrimitiveId()) || [];
  const rows = [];
  for (let i = 0; i < ids.length; i += 1) {
    // One bad id must not lose the rest of the collection: a partial
    // read that says so beats no read at all.
    try {
      const row = await api.get(ids[i]);
      if (row) rows.push(row);
    } catch (inner) { /* counted below by the shortfall */ }
  }
  const out = { rows: rows, via: 'ids', ids_seen: ids.length,
                getall_failed: why };
  if (rows.length !== ids.length) {
    out.unreadable = ids.length - rows.length;
  }
  return out;
}

handlers['pcb.attributes'] = async () => withRoute('attributes',
  await readAll(eda.pcb_PrimitiveAttribute, 'pcb_PrimitiveAttribute'));

handlers['sch.attributes'] = async () => withRoute('attributes',
  await readAll(eda.sch_PrimitiveAttribute, 'sch_PrimitiveAttribute'));

handlers['pcb.dimensions'] = async () => ({
  dimensions: (await eda.pcb_PrimitiveDimension.getAll()) || [],
});

handlers['sch.create_net_label'] = async (params) => {
  // A label is placed AT a point, so the coordinates are not optional
  // decoration. An earlier version of this passed the net name as the
  // first argument, which is x: the label went nowhere and the net
  // stayed unconnected, with nothing in the reply saying so.
  const name = params.name;
  if (!name) throw new Error('name is required');
  requireNumbers(params, ['x', 'y']);
  const label = await eda.sch_PrimitiveAttribute.createNetLabel(
    params.x, params.y, name);
  return { name, created: label || null };
};

handlers['lib.search_3d_models'] = async (params) => {
  const out = await librarySearch('lib_3DModel', params, true);
  return Object.assign({ models: out.found }, out.meta);
};

handlers['sys.paths'] = async () => ({
  // Where the editor keeps things, reported rather than assumed. The
  // extension runs sandboxed, so the server cannot infer these and a
  // guessed path is how an export lands somewhere nobody looks.
  eda: await eda.sys_FileSystem.getEdaPath(),
  documents: await eda.sys_FileSystem.getDocumentsPath(),
  projects: await eda.sys_FileSystem.getProjectsPaths(),
  libraries: await eda.sys_FileSystem.getLibrariesPaths(),
});

// Switching tabs and framing the view.
//
// Seven dmt_EditorControl methods were exposed nowhere. These three are
// the ones whose call shape follows something this file already does:
// the two zooms take nothing at all, and activateDocument takes a TAB
// ID rather than a document uuid, which is not the same thing.
//
// The rest are left alone deliberately. generateIndicatorMarkers and
// removeIndicatorMarkers are the interesting pair, being EasyEDA's way
// to mark primitives in the editor the way an Altium review highlights
// violations, but their arguments are not known and inventing a
// signature for a call that draws on somebody's board is not a guess
// worth making offline.
// A TAB ID, NOT A DOCUMENT UUID. activateDocument(tabId) is documented
// with no note allowing a document uuid, and the docs are explicit when
// one IS allowed: closeDocument carries exactly such a note and this
// does not. IDMT_EditorTabItem has a tabId and no document uuid, so the
// two identifier spaces are separate.
//
// Passing a uuid was justified by analogy with openDocument, which does
// take one. That analogy was wrong, and openDocument RETURNS the tab
// id, which is the only place in the API one can be obtained. So
// editor.open_document now reports tab_id, and this consumes it.
handlers['editor.activate_document'] = async (params) => {
  const tabId = params.tab_id;
  if (!tabId) {
    throw new Error(
      'tab_id is required: activateDocument takes a TAB ID, not a '
      + 'document uuid, and there is no call that converts one to the '
      + 'other. editor.open_document returns tab_id; use that. To bring '
      + 'a document forward when you only have its uuid, open it.');
  }
  // Read the answer: this declines by returning false rather than
  // throwing, and reporting an unmade switch as made would send every
  // following command to the wrong document.
  const answer = await eda.dmt_EditorControl.activateDocument(tabId);
  if (answer === false) {
    return { tab_id: tabId, activated: false,
      failed: 'the editor declined to switch to that tab' };
  }
  return { tab_id: tabId, activated: true };
};

// MARKING FINDINGS ON THE BOARD, which is what a review needs and what
// this backend has never had. Markers are an OVERLAY: they draw on top
// of the design, change no geometry, and are cleared in one call. That
// is why they are not confirm-guarded while everything else that draws
// is.
//
// Previously left alone because the arguments were unknown and
// inventing a signature for a call that draws on somebody's board is
// not a guess worth making. They are documented after all:
//
//   generateIndicatorMarkers(markers, color?, lineWidth?, zoom?, tabId?)
//   removeIndicatorMarkers(tabId?)
//
// with each marker an IDMT_IndicatorMarkerShape whose `type` decides
// which fields apply. The five types and their fields come from
// EDMT_IndicatorMarkerType and the interface, not from guesswork:
const MARKER_FIELDS = {
  point: ['x', 'y'],
  line: ['startX', 'startY', 'endX', 'endY'],
  rectangle: ['left', 'right', 'top', 'bottom'],
  circle: ['x', 'y', 'r'],
  arc: ['startX', 'startY', 'endX', 'endY', 'angle'],
};

handlers['editor.mark_findings'] = async (params) => {
  const raw = Array.isArray(params.markers) ? params.markers : [];
  if (!raw.length) {
    throw new Error('markers must be a non-empty array');
  }
  // Validated per type before anything is sent. A marker missing the
  // fields its own type needs is accepted by the editor and drawn
  // somewhere unintended, which is worse than a refusal: a review
  // would be pointing at the wrong place with full confidence.
  const markers = raw.map((m, i) => {
    const type = String((m && m.type) || '').toLowerCase();
    const need = MARKER_FIELDS[type];
    if (!need) {
      throw new Error(
        `markers[${i}].type is ${JSON.stringify(m && m.type)}; expected `
        + `one of ${Object.keys(MARKER_FIELDS).join(', ')}`);
    }
    const out = { type: type };
    for (const key of need) {
      if (typeof m[key] !== 'number') {
        throw new Error(
          `markers[${i}] is a ${type} and needs a numeric ${key}; `
          + `a ${type} uses ${need.join(', ')}`);
      }
      out[key] = m[key];
    }
    return out;
  });

  const colour = params.color && typeof params.color === 'object'
    ? {
      r: Number(params.color.r) || 0,
      g: Number(params.color.g) || 0,
      b: Number(params.color.b) || 0,
      alpha: params.color.alpha === undefined ? 1 : Number(params.color.alpha),
    }
    : undefined;
  const ok = await eda.dmt_EditorControl.generateIndicatorMarkers(
    markers, colour,
    typeof params.line_width === 'number' ? params.line_width : undefined,
    params.zoom === true,
    params.tab_id || undefined) === true;
  return { marked: ok, count: markers.length };
};

handlers['editor.clear_findings'] = async (params) => ({
  cleared: await eda.dmt_EditorControl.removeIndicatorMarkers(
    (params && params.tab_id) || undefined) === true,
});

handlers['editor.zoom_to_all'] = async () => ({
  zoomed: await eda.dmt_EditorControl.zoomToAllPrimitives() !== false,
});

handlers['editor.zoom_to_selection'] = async () => ({
  zoomed: await eda.dmt_EditorControl.zoomToSelectedPrimitives() !== false,
});

handlers['editor.open_document'] = async (params) => {
  const uuid = params.uuid;
  if (!uuid) throw new Error('uuid is required');
  // FAILURE IS undefined, NOT false, and success is the TAB ID.
  // openDocument(documentUuid, splitScreenId?) returns
  // Promise<string | undefined>. Testing `=== false` could never be
  // true, so the failure branch was dead: a failed open fell straight
  // into the readiness loop below, which asks what the CURRENT document
  // is. With any schematic or PCB tab already in front that loop
  // succeeds, and the handler answered {opened: true, ready: true} for
  // a document it had not opened.
  const tabId = await eda.dmt_EditorControl.openDocument(uuid);
  if (!tabId) {
    return { uuid: uuid, opened: false, tab_id: null,
      failed: 'the editor declined to open that document' };
  }

  // Wait for the document to become readable, not merely opened.
  //
  // openDocument resolves before the document can answer reads, so a
  // read issued immediately afterwards times out while the same read a
  // moment later succeeds. That is indistinguishable from a broken
  // read, and any caller that switches documents hits it repeatedly.
  //
  // Readiness means the document reports a kind, which is the cheapest
  // question requiring it to be loaded. The wait is bounded and its
  // outcome reported: opened but not readable is a distinct state and
  // must not be returned as a plain success.
  const READY_TRIES = 20;
  const READY_GAP_MS = 250;
  let kind = 'unknown';
  for (let i = 0; i < READY_TRIES; i += 1) {
    try {
      kind = await currentDocumentKind();
    } catch (e) {
      kind = 'unknown';
    }
    if (kind === 'pcb' || kind === 'schematic') break;
    await delay(READY_GAP_MS);
  }
  if (kind !== 'pcb' && kind !== 'schematic') {
    return { uuid: uuid, opened: true, ready: false, document: kind,
      tab_id: tabId,
      failed: `the document opened but did not become readable within `
        + `${READY_TRIES * READY_GAP_MS}ms; a read now may hang` };
  }
  // The tab id is worth returning rather than discarding: it is the
  // ONLY thing activateDocument accepts, and nothing else in the API
  // hands one out.
  return { uuid: uuid, opened: true, ready: true, document: kind,
    tab_id: tabId };
};

// PCB_PrimitiveString, not PrimitiveText. Text on copper and silk is
// what silkscreen audits read, and the class name is easy to guess
// wrong: there is no PCB_PrimitiveText.
handlers['pcb.strings'] = async () => withRoute('strings',
  await readAll(eda.pcb_PrimitiveString, 'pcb_PrimitiveString'));

handlers['pcb.pours'] = async () => ({
  // The pour OUTLINE the user drew. Distinct from pcb.regions, and
  // distinct again from the poured copper below.
  pours: (await eda.pcb_PrimitivePour.getAll()) || [],
});

// The copper actually filled in after pouring. A pour whose outline
// exists but which has never been poured leaves no copper, and the two
// lists disagreeing is exactly that case.
handlers['pcb.poured'] = async () => withRoute('poured',
  await readAll(eda.pcb_PrimitivePoured, 'pcb_PrimitivePoured'));

handlers['pcb.fills'] = async () => ({
  fills: (await eda.pcb_PrimitiveFill.getAll()) || [],
});

handlers['sch.buses'] = async () => ({
  buses: (await eda.sch_PrimitiveBus.getAll()) || [],
});

handlers['sch.save'] = async () => {
  const answer = await eda.sch_Document.save();
  if (answer === false) {
    return { saved: false, failed: 'the editor declined to save' };
  }
  return { saved: true };
};

handlers['pcb.images'] = async () => ({
  images: (await eda.pcb_PrimitiveImage.getAll()) || [],
});

// Not the same thing as an image, despite the name. PCB_PrimitiveObject
// holds BINARY EMBEDDED objects, the colour-silkscreen kind, whose
// payload travels as binary data rather than as geometry. Kept separate
// from pcb.images because merging them would report two different
// object kinds under one heading and hide which is which.
handlers['pcb.embedded_objects'] = async () => ({
  objects: (await eda.pcb_PrimitiveObject.getAll()) || [],
});

handlers['pcb.bboxes'] = async (params) => {
  const ids = params.primitive_ids;
  if (!Array.isArray(ids) || ids.length === 0) {
    throw new Error('primitive_ids must be a non-empty array');
  }
  // One box PER id, which the single-box call cannot give: it encloses
  // everything it is handed, so a caller wanting each one separately
  // would pay a round trip apiece.
  //
  // A read, so it does NOT stop at the first failure. A box that cannot
  // be measured is reported as null and the rest are still returned;
  // stopping would throw away the answers already gathered.
  const boxes = [];
  let measured = 0;
  for (const id of ids) {
    try {
      const box = await eda.pcb_Primitive.getPrimitivesBBox([id]);
      if (box) {
        boxes.push({ primitive_id: id, bbox: box });
        measured += 1;
      } else {
        boxes.push({ primitive_id: id, bbox: null });
      }
    } catch (e) {
      boxes.push({ primitive_id: id, bbox: null, error: String(e) });
    }
  }
  return { boxes, measured, of: ids.length };
};

handlers['pcb.bbox'] = async (params) => {
  const ids = params.primitive_ids;
  if (!Array.isArray(ids) || ids.length === 0) {
    throw new Error('primitive_ids must be a non-empty array');
  }
  // Signature checked against the reference: it takes an ARRAY of ids
  // and returns {minX, minY, maxX, maxY}. Passing a bare id would look
  // reasonable and return undefined.
  const box = await eda.pcb_Primitive.getPrimitivesBBox(ids);
  if (!box) throw new Error('no bounding box for those primitive ids');
  return { bbox: box, count: ids.length };
};

handlers['sys.environment'] = async () => ({
  // Which EasyEDA this actually is. Pro, JLCEDA Pro and the private
  // edition differ in what the API exposes, and offline mode changes
  // what a library call can reach. Reporting it means a later failure
  // can be attributed rather than guessed at.
  version: await eda.sys_Environment.getEditorCurrentVersion(),
  is_pro: await eda.sys_Environment.isEasyEDAProEdition(),
  is_jlceda_pro: await eda.sys_Environment.isJLCEDAProEdition(),
  is_client: await eda.sys_Environment.isClient(),
  is_offline: await eda.sys_Environment.isOfflineMode(),
});

handlers['dmt.team'] = async () => ({
  team: (await eda.dmt_Team.getCurrentTeamInfo()) || null,
});

handlers['dmt.folders'] = async (params) => {
  // Signatures verified against the installed api-types.d.ts, every
  // one of them wanting the team uuid first.
  if (typeof params.team_uuid !== 'string' || !params.team_uuid) {
    throw new Error('team_uuid is required; read it from dmt.team');
  }
  const uuids = (await eda.dmt_Folder.getAllFoldersUuid(params.team_uuid))
    || [];
  const folders = [];
  for (const uuid of uuids) {
    try {
      const info = await eda.dmt_Folder.getFolderInfo(
        params.team_uuid, uuid);
      folders.push(info || { uuid: uuid });
    } catch (e) {
      folders.push({ uuid: uuid, error: String(e) });
    }
  }
  return { folders, count: folders.length };
};

handlers['dmt.create_folder'] = async (params) => {
  if (typeof params.name !== 'string' || !params.name) {
    throw new Error('name is required');
  }
  if (typeof params.team_uuid !== 'string' || !params.team_uuid) {
    throw new Error('team_uuid is required; read it from dmt.team');
  }
  const uuid = await eda.dmt_Folder.createFolder(
    params.name,
    params.team_uuid,
    typeof params.parent_folder_uuid === 'string' && params.parent_folder_uuid
      ? params.parent_folder_uuid : undefined,
    typeof params.description === 'string' && params.description
      ? params.description : undefined,
  );
  return { uuid: uuid || null, created: Boolean(uuid) };
};

handlers['dmt.move_project_to_folder'] = async (params) => {
  if (typeof params.project_uuid !== 'string' || !params.project_uuid) {
    throw new Error('project_uuid is required');
  }
  const moved = await eda.dmt_Project.moveProjectToFolder(
    params.project_uuid,
    typeof params.folder_uuid === 'string' && params.folder_uuid
      ? params.folder_uuid : undefined,
  );
  return { moved: moved === true };
};

handlers['dmt.boards'] = async () => ({
  boards: (await eda.dmt_Board.getAllBoardsInfo()) || [],
});

handlers['dmt.panels'] = async () => ({
  panels: (await eda.dmt_Panel.getAllPanelsInfo()) || [],
});

// Panel documents, which had a read and nothing else.
//
// dmt_Panel is method-for-method parallel to dmt_Pcb: copy, create,
// delete, getAll, getCurrent, get, modifyName. The parallel does NOT
// extend to create taking a name. createPcb(boardName) and
// createSchematic(boardName) do, but the reference declares
// createPanel() with no parameters at all, so the name passed here was
// silently dropped and the tool advertised a naming behaviour that
// never happened. The panel is created unnamed, then renamed.
//
// What is NOT here is a way to put a board INTO a panel: dmt_Panel has
// no add, insert or place method, and neither does dmt_Board. So this
// creates and manages the document; arranging boards inside it is not
// something the extension API appears to expose, and none of these
// tools should be read as an equivalent to a step-and-repeat.
handlers['dmt.create_panel'] = async (params) => {
  const uuid = await eda.dmt_Panel.createPanel();
  const wanted = typeof params.name === 'string' && params.name
    ? params.name : '';
  // Reported separately from `created`, because a panel that exists
  // under the wrong name is a different outcome from one that was
  // never made, and collapsing them is how the dropped argument went
  // unnoticed in the first place.
  let named = null;
  if (uuid && wanted) {
    named = await eda.dmt_Panel.modifyPanelName(uuid, wanted) === true;
  }
  return {
    uuid: uuid || null,
    created: Boolean(uuid),
    requested_name: wanted || null,
    named: named,
  };
};

handlers['dmt.current_panel'] = async () => {
  const info = await eda.dmt_Panel.getCurrentPanelInfo();
  // No panel open is a legitimate answer and not a failure, so it is
  // reported as such rather than thrown.
  return { panel: info || null, open: Boolean(info) };
};

handlers['dmt.panel_info'] = async (params) => {
  if (!params.uuid) throw new Error('uuid is required');
  const info = await eda.dmt_Panel.getPanelInfo(params.uuid);
  return { panel: info || null, found: Boolean(info) };
};

handlers['dmt.rename_panel'] = async (params) => {
  if (!params.uuid) throw new Error('uuid is required');
  if (!params.name) throw new Error('name is required');
  // Read the answer. These methods decline by returning falsey rather
  // than raising, and six handlers once reported work they had not
  // done because nobody looked at what came back.
  const done = await eda.dmt_Panel.modifyPanelName(params.uuid, params.name);
  return { renamed: done !== false, uuid: params.uuid };
};

handlers['dmt.delete_panel'] = async (params) => {
  if (!params.uuid) throw new Error('uuid is required');
  requireConfirm(params, 'delete_panel removes the panel document.');
  return { deleted: await eda.dmt_Panel.deletePanel(params.uuid) === true };
};

// ---- transport ------------------------------------------------------

function explainFailure(error) {
  // "Cannot read properties of undefined (reading 'getAll')" is what
  // the editor says when the eda.* class a command needs is not present
  // in the current context, and it names the METHOD rather than the
  // missing class, so it reads like a bug in the caller.
  //
  // A live session can produce dozens of these in a row.
  // Every one looked like a defect in this project and none was. The
  // raw text is kept, because it is the real error, with the reading
  // added after it.
  const text = String((error && error.message) || error);

  if (/Cannot read properties of (?:undefined|null) \(reading /.test(text)
      || /is not a function/.test(text)) {
    return (
      `${text} -- this usually means the eda.* class or method this `
      + `command needs is not present in the current context rather `
      + `than that the command is wrong. EasyEDA injects a different `
      + `API surface depending on the open document. Call `
      + `system.capabilities to see what is actually available here.`);
  }
  return text;
}

async function dispatch(raw) {
  let request;
  try {
    request = JSON.parse(raw);
  } catch (e) {
    // Not addressed to us, or corrupt. Staying silent is right: there
    // is no id to answer to.
    return;
  }

  // A SERVER THAT IS NOT OURS, ANSWERING ON A PORT WE SCANNED.
  //
  // The fallback discovery path has no /health check: with no fetch in
  // the runtime it opens a WebSocket to each candidate port and keeps
  // whichever connects. EasyEDA's own bridge server listens on the same
  // ten ports and the same /eda path, so that path can attach to it,
  // and nothing downstream would notice. Its handshake carries no
  // `command`, so it used to be dropped here in silence while the
  // extension stayed attached and reported itself connected. Every
  // later command would then go to a server speaking a different
  // protocol.
  //
  // It announces itself on connect, which is the opening this uses:
  // {type: "handshake", service: "easyeda-bridge"}. Any service that
  // names itself and is not us gets the socket closed, so the retry
  // loop moves on to the next port instead of settling on the wrong
  // server.
  if (request && request.service && request.service !== SERVICE_ID) {
    connected = false;
    noteOutcome(
      `port answered, but it is ${request.service}, not ${SERVICE_ID}`,
    );
    try {
      eda.sys_WebSocket.close(WS_ID);
    } catch (e) { /* it is going away regardless */ }
    return;
  }

  const { id, command, params } = request || {};
  if (!id || !command) return;

  const handler = handlers[command];
  if (!handler) {
    send({
      id: id,
      error: `unknown command ${command}. Known: ${Object.keys(handlers).join(', ')}`,
    });
    return;
  }

  // Refuse a command whose API is not present in THIS runtime, before
  // running it.
  //
  // Measured on a live schematic tab: of 90 read-only tools, 33 came
  // back as "Cannot read properties of null (reading 'map')" and 14
  // never replied at all, costing 20 to 60 seconds each. Both were the
  // same thing. EasyEDA injects its API per document type, so on a
  // schematic every pcb_* class is missing; calling one either throws
  // an opaque TypeError from somewhere inside a handler, or returns a
  // promise that never settles.
  //
  // Neither failure tells the caller the useful fact, which is simply
  // that the wrong document is in front. Checking first turns both into
  // an instant, specific refusal, and turns a 60-second hang into a
  // reply. The check is here rather than in each handler because there
  // are 161 of them and one that forgets is one that hangs.
  const missing = await wrongDocumentFor(command);
  if (missing) {
    send({ id: id, error: missing });
    return;
  }

  try {
    // Always ANSWER, even when the editor's own call never returns.
    //
    // Measured: sch.attributes, pcb.attributes,
    // pcb.strings, pcb.poured, sys.paths and sch.selection never
    // replied, costing 20 to 60 seconds each while the caller waited on
    // a socket that would stay quiet forever. Why they hang is not
    // established and may be EasyEDA's business rather than ours, but
    // the cost of not knowing is a dead session, and a reply saying "no
    // answer in 15s" is actionable where silence is not.
    //
    // The handler is not cancelled: nothing here can stop a promise
    // that will not settle. What changes is that the caller stops
    // waiting on it, so one bad command no longer eats a whole run.
    // That is why the message says the command was NOT refused; a hung
    // WRITE may have completed, and reporting it as refused would
    // invite a caller to run it a second time.
    // Exports get a longer ceiling than reads.
    //
    // The default is sized for a read, which answers in well under a
    // second. Generating a PDF, a DXF or an IPC-2581 file renders the
    // whole board and can legitimately take much longer, so the read
    // ceiling would report a working export as a hang and there would
    // be no way to tell that apart from a real one.
    // Commands measured never to answer get a SHORT budget.
    //
    // Probed one class at a time against a live editor: nine of the
    // eleven schematic primitive classes answer getAll in about a
    // second, and two never return at all. The same two families fail
    // on the PCB side, so this is the attribute and embedded-object
    // accessors rather than anything about one document.
    //
    // Still ATTEMPTED, not refused. A later EasyEDA release may fix
    // them, and a hard refusal here would hide that forever. What
    // changes is the price of finding out: three seconds instead of
    // fifteen, which matters because a review that touches several of
    // these spends most of its time waiting for silence.
    const NEVER_ANSWERED = [
      'sch.attributes', 'pcb.attributes', 'sch.selection',
      'pcb.strings', 'pcb.poured', 'sys.paths',
      // lib.symbol_image and lib.footprint_image WERE listed here, on a
      // measurement that both render calls never return for ids taken
      // from a search that had just succeeded. The ids were good and
      // the observation was real, but the diagnosis was not: both take
      // a source OBJECT carrying the library uuid as well
      // ({ symbolUuid, libraryUuid }), and were being handed a bare
      // string. They are attempted normally again, and the note that
      // packing "would have fixed nothing" was wrong twice over, since
      // the documented return is a Blob and now goes through
      // packedFile. If they hang with the correct argument, put them
      // back with that measurement recorded rather than this one.
      // Measured twice on a live schematic holding 111 parts: the call
      // is accepted and never returns. Not a missing class, and not an
      // empty project, since the same document answers sch.components
      // and sch.netlist. Budgeted like the rest so the caller waits
      // three seconds for the refusal instead of the full timeout.
      'sch.assembly_variants',
    ];
    const budget = command.indexOf('export.') === 0
      ? EXPORT_TIMEOUT_MS
      : (NEVER_ANSWERED.indexOf(command) !== -1
        ? Math.min(3000, HANDLER_TIMEOUT_MS)
        : HANDLER_TIMEOUT_MS);
    let timer = null;
    const result = await Promise.race([
      handler(params || {}),
      new Promise((_, reject) => {
        timer = setTimeout(
          () => reject(new Error(
            `${command} did not answer within ${budget}ms. `
            + 'The editor accepted the call and never returned; the '
            + 'command was NOT refused and may still be running. '
            + 'Known to happen for the attribute, string and poured '
            + 'reads.')),
          budget);
      }),
    // Without this every completed command leaves a live timer behind,
    // so a long session accumulates one per call and, off the browser,
    // the pending timers alone keep the process from exiting.
    ]).finally(() => { if (timer !== null) clearTimeout(timer); });
    send({ id: id, result: result });
  } catch (e) {
    // Answer with the failure rather than going quiet. A missing reply
    // is indistinguishable from a hung editor at the other end.
    send({ id: id, error: explainFailure(e) });
  }
}

// Whether this command's namespace matches the document in front.
//
// Decided by document kind, not by class presence. Every one of the
// API classes is present in every runtime, including the pcb_* classes
// on a schematic tab: the API surface is uniform and it is the DATA
// that is missing, so pcb_PrimitivePad.getAll fails inside EasyEDA
// with "Cannot read properties of null" rather than being undefined.
//
// A probe for missing classes therefore never fires. It only
// ever worked against the Node fake, where classes genuinely are
// absent. Document kind is the real discriminator, which is the exact
// opposite of what the old comment here claimed.
//
// Only a POSITIVE mismatch refuses. `unknown` is left alone: it has
// been seen on a working editor, and refusing everything then would be
// worse than the failure being prevented.
async function wrongDocumentFor(command) {
  // These answer whatever document is in front, because they reach
  // project-level or net-level data rather than the open document's
  // primitives. Refusing them by namespace would break tools that work
  // correctly from either tab, trading a slow failure for a fast wrong
  // answer.
  const WORKS_ANYWHERE = [
    'pcb.nets', 'pcb.net_length', 'pcb.net_lengths', 'pcb.list_boards',
    // Listing the documents in a PROJECT is not reading a schematic's
    // contents, and refusing it from a board tab is a trap: to find a
    // schematic's uuid you must already be in a schematic. Measured
    // that makes it impossible to navigate back from the
    // PCB, which is the one direction anything automating a review
    // needs. pcb.list_boards was exempt for exactly this reason and
    // its schematic twin was not.
    'sch.list_schematics', 'sch.list_pages',
  ];
  if (WORKS_ANYWHERE.indexOf(command) !== -1) return null;

  // Commands whose NAMESPACE does not say which document they need.
  // Every one of these reaches a pcb_* or sch_* class, so the gate
  // below could not see it and the command ran on whichever tab was
  // in front. It then failed inside EasyEDA with a null dereference,
  // which reads as a broken tool rather than as the wrong document.
  //
  // Each entry is taken from the class family the handler actually
  // touches, not from its name.
  const NEEDS_DOCUMENT = {
    'design.snapshot': 'pcb',
    'design.run_drc': 'pcb',
    'design.run_erc': 'schematic',
    'export.bom': 'pcb',
    'export.dxf': 'pcb',
    'export.model_3d': 'pcb',
    'export.gerber': 'pcb',
    'export.ipc2581': 'pcb',
    'export.ipcd356': 'pcb',
    'export.netlist': 'pcb',
    'export.altium': 'pcb',
    'export.pdf': 'pcb',
    'export.pick_and_place': 'pcb',
    'export.test_points': 'pcb',
    'export.flying_probe': 'pcb',
    'export.dsn': 'pcb',
    'export.pads': 'pcb',
    'export.pcb_info': 'pcb',
    'export.schematic_document': 'schematic',
    'export.schematic_netlist': 'schematic',
    'export.sch_bom': 'schematic',
    'export.simulation_netlist': 'schematic',
  };

  const namespace = String(command || '').split('.')[0];
  const needs =
    NEEDS_DOCUMENT[command] || { pcb: 'pcb', sch: 'schematic' }[namespace];
  if (!needs) return null;

  let kind;
  try {
    kind = await currentDocumentKind();
  } catch (e) {
    return null;                        // cannot tell; do not invent one
  }
  if (kind === needs) return null;

  const wanted = needs === 'pcb' ? 'a PCB' : 'a schematic';

  // NOTHING OPEN IS A DEFINITE ANSWER, NOT AN UNKNOWN ONE.
  // currentDocumentKind returns 'unknown' only after BOTH probes ran
  // and neither found a document, so it means no PCB and no schematic
  // is open rather than "could not tell". Letting commands through on
  // that reading is what turns an empty editor into a confusing
  // failure: sch.add_wire reached the editor and came back
  // "create failed!", and sch.components came back with an untranslated
  // Chinese error, neither of which says the obvious thing.
  //
  // A genuine cannot-tell is the THROW above, which still returns null
  // rather than inventing a document kind.
  if (kind !== 'pcb' && kind !== 'schematic') {
    return (
      `${command} needs ${wanted} document and none is open. Neither ` +
      `dmt_Pcb.getCurrentPcbInfo nor ` +
      `dmt_Schematic.getCurrentSchematicInfo reported a document, so ` +
      `the editor is on the start page or a document type this cannot ` +
      `drive. Open ${wanted} and try again. Nothing was run, so this ` +
      `is not evidence the command would fail.`
    );
  }
  // The class family, not the command's namespace. An export command
  // reaches pcb_* classes while being called export.gerber, and naming
  // "export_*" here would send a reader looking for something that
  // does not exist.
  const family = needs === 'pcb' ? 'pcb' : 'sch';
  return (
    `${command} needs ${wanted} document and the active one is a ` +
    `${kind}. The ${family}_* classes exist in every runtime, so ` +
    `this would not fail with "undefined": it fails inside EasyEDA ` +
    `with a null, or does not answer at all. Open ${wanted} and ` +
    `connect from there, or call system.capabilities to see what is ` +
    `available in the current context. Nothing was run, so this is ` +
    `not evidence the command would fail.`
  );
}


function send(payload) {
  // A plain send, deliberately.
  //
  // Catching a throw here to detect a dropped socket does not work: a
  // send on a dead socket does not throw in this runtime, so nothing
  // is detected, and a throw for any other reason would clear the
  // connected flag and cause the retry loop to tear down a healthy
  // connection.
  //
  // Liveness is handled by the idle reattach below, which needs no
  // signal from here.
  eda.sys_WebSocket.send(WS_ID, JSON.stringify(payload));
}

// Exported names here must match the `registerFn` values in
// extension.json. EasyEDA resolves them by string at load time, so a
// rename on either side fails as a menu item that does nothing rather
// than as an error.
// Ports to look on, matching the convention EasyEDA's own bridge server
// uses. A fixed port has to be agreed by hand and silently fails when it
// is taken; scanning finds whichever one the server got.
const PORT_START = 49620;
const PORT_END = 49629;
const SERVICE_ID = 'eda-agent-bridge';

// How often to look again when nothing is there yet. THE POINT OF THIS:
// SYS_WebSocket.register() fails silently if nothing is listening at
// that instant and never tries again, so a correct extension and a
// correct server can sit side by side and never meet. Retrying is what
// makes the order of starting them stop mattering.
const RETRY_MS = 5000;
//: How long to wait for a port to report a connection when there is no
//: health probe to ask. Long enough for a loopback socket to open, short
//: enough that walking eleven dead ports stays under a second.
const PROBE_MS = 250;

let retryTimer = null;
let connected = false;

function candidatePorts() {
  const ports = [];
  for (let p = PORT_START; p <= PORT_END; p += 1) ports.push(p);
  ports.push(8787); // the previous fixed default, still honoured
  return ports;
}

// Whether this runtime gives the extension a usable fetch.
//
// EasyEDA's own guidance is that standard browser APIs are forbidden in
// the extension's main process and that EDA-provided alternatives
// should be used instead. So fetch may simply not be there, and the
// /health probe below is a preference, not a requirement.
//
// This mattered: with discovery resting on fetch alone, a runtime
// without it finds nothing on every port and reports "no server found",
// which is the same message as the server genuinely being down. The
// extension would look correct and never connect.
function hasFetch() {
  return typeof fetch === 'function';
}

// ---- timers ----------------------------------------------------------
//
// EasyEDA publishes SYS_Timer as the EDA-provided replacement for the
// host timer functions, and says the host ones are not available to an
// extension's main process. So they are preferred here, with the host
// versions as a fallback.
//
// This is not a style choice. The retry loop is what makes starting
// order stop mattering, and it was armed with a bare setInterval inside
// connect(), which activate() calls at load. On a runtime without it,
// that throws while the module is initialising, so the extension does
// not merely fail to retry, it fails to LOAD, and no menu item appears
// to say so.
//
// SYS_Timer identifies timers by string rather than by handle, so the
// two kinds cannot be cleared the same way and the handle carries its
// own kind.
const RETRY_TIMER_ID = 'eda-agent-retry';
let probeSerial = 0;


//: How long an idle link is trusted before it is reopened regardless.
//:
//: sys_WebSocket offers close, register and send and nothing else: no
//: readyState, no close callback, and no way to ask whether the socket
//: is open. A dropped connection therefore cannot be detected, so this
//: does not try. After this long without a message the link is closed
//: and reopened whether or not it was healthy.
//:
//: Reattaching to a working server costs one socket; staying attached
//: to a dead one costs the session, and the API supports no third
//: option.
//:
//: Counted in retry ticks. Twelve at five seconds gives a minute of
//: silence before reconnecting, long enough that an active session
//: never reattaches and short enough that a restarted server is picked
//: up without intervention.
const IDLE_REATTACH_TICKS = 12;
let idleTicks = 0;

function startInterval(fn, ms) {
  // ARM BOTH TIMERS, for the reason delay() does: preferring the
  // editor's timer and falling back only when it is ABSENT does not
  // cover a timer that exists and never fires. This one carries the
  // whole reconnection loop, so if it is silent nothing ever notices a
  // dropped link.
  //
  // The tick is rate limited below rather than here, so two sources
  // firing does not make it run twice as often.
  let edaArmed = false;
  let hostId = null;

  if (eda.sys_Timer && typeof eda.sys_Timer.setIntervalTimer === 'function') {
    try {
      eda.sys_Timer.setIntervalTimer(RETRY_TIMER_ID, ms, fn);
      edaArmed = true;
    } catch (e) { /* the host timer below is the fallback */ }
  }
  if (typeof setInterval === 'function') {
    hostId = setInterval(fn, ms);
  }

  if (!edaArmed && hostId === null) {
    // Neither is available. One connection attempt still happens; only
    // the retry is lost, and saying so beats throwing at load.
    return null;
  }
  return {
    kind: edaArmed && hostId !== null ? 'both'
      : (edaArmed ? 'eda' : 'host'),
    id: edaArmed ? RETRY_TIMER_ID : null,
    hostId: hostId,
  };
}

function stopInterval(handle) {
  if (!handle) return;
  // Both may be armed, so clear both. Clearing only the one named by
  // `kind` would leave the other firing after an explicit disconnect,
  // which would reconnect the user straight back.
  try {
    if (handle.id !== null && handle.id !== undefined) {
      eda.sys_Timer.clearIntervalTimer(handle.id);
    }
  } catch (e) { /* already gone */ }
  try {
    if (handle.hostId !== null && handle.hostId !== undefined) {
      clearInterval(handle.hostId);
    }
  } catch (e) { /* already gone */ }
}

function delay(ms) {
  // ARM BOTH TIMERS AND TAKE WHICHEVER FIRES FIRST.
  //
  // Preferring the editor's timer and falling back only when it is
  // ABSENT covers the wrong failure. A timer that exists and never
  // fires leaves this promise pending forever, and everything awaiting
  // it stops: the port walk stalls on its first candidate and the
  // attach that owns it never finishes. Whether the editor's timer
  // fires while the extension is idle is not established, so this stops
  // depending on the answer.
  //
  // Resolving twice is harmless; a promise keeps its first settlement.
  return new Promise((resolve) => {
    let armed = false;
    if (eda.sys_Timer
        && typeof eda.sys_Timer.setTimeoutTimer === 'function') {
      probeSerial += 1;
      try {
        eda.sys_Timer.setTimeoutTimer(
          `${RETRY_TIMER_ID}-probe-${probeSerial}`, ms, resolve);
        armed = true;
      } catch (e) { /* fall through to the host timer */ }
    }
    if (typeof setTimeout === 'function') {
      setTimeout(resolve, ms);
      armed = true;
    }
    // No timer at all: do not hang. Resolving at once makes the port
    // walk check `connected` immediately, which is a worse probe but a
    // finite one.
    if (!armed) resolve();
  });
}

async function findServerByHealth() {
  // Ask each port who it is rather than assuming whatever answers is
  // ours. Another service on the port would otherwise get a WebSocket
  // handshake it never asked for.
  lastProbeNotes = [];
  for (const port of candidatePorts()) {
    try {
      // BOUNDED. A fetch with no timeout of its own is how the whole
      // attach wedges: attach() holds `attaching` for its duration, so
      // one probe that never settles means every later retry returns
      // immediately and the extension never reconnects again. Measured:
      // after the server restarted, nothing reattached for three
      // minutes although the retry timer was still firing.
      //
      // Racing a delay is used rather than AbortSignal.timeout, which
      // is not guaranteed present in this runtime. The probe is left to
      // finish in the background; only the waiting is bounded.
      const response = await Promise.race([
        fetch(`http://127.0.0.1:${port}/health`, { method: 'GET' }),
        delay(PROBE_MS * 4).then(() => null),
      ]);
      if (!response || !response.ok) continue;
      const body = await Promise.race([
        response.json(),
        delay(PROBE_MS * 4).then(() => null),
      ]);
      if (body && body.service === SERVICE_ID) {
        return `ws://127.0.0.1:${port}/eda`;
      }
      // Something is there and it is not us. Worth recording rather
      // than skipping: this range and path are the ones EasyEDA's own
      // bridge server uses, so the likeliest occupant is that server,
      // and the two are then competing for the same ten ports. A bare
      // "no server found" hides the difference between an empty range
      // and a range somebody else already owns.
      lastProbeNotes.push(
        `${port}: ${(body && body.service) || 'answered, not a health reply'}`,
      );
    } catch (e) {
      // Nothing listening there. Expected for most of the range.
    }
  }
  return null;
}

async function findServer() {
  if (hasFetch()) {
    const found = await findServerByHealth();
    if (found) return found;
  }
  // No fetch, or nothing answered /health. Fall back to the ports
  // themselves: the caller opens a WebSocket to each in turn and keeps
  // the one that connects. Less precise than asking who is listening,
  // and the reason the probe is tried first, but a connection that
  // works beats an identification that cannot be made.
  return null;
}

async function attach() {
  attachAttempts += 1;
  if (connected) return;

  // ONE ATTACH AT A TIME.
  //
  // A scan is slow: eleven candidate ports, each with a probe delay,
  // behind fetch probes that have no timeout of their own. It routinely
  // outlives the retry interval, and the retry tick called attach()
  // again regardless, so two scans ran side by side.
  //
  // That is fatal rather than merely wasteful, because they share
  // WS_ID. The second scan renews the id and closes the first scan's
  // socket; the first then wakes from its delay, sees no connection,
  // and closes what is now the SECOND scan's socket, including one that
  // had just connected. Two overlapping scans destroy each other's
  // sockets indefinitely, which looks exactly like a retry loop that
  // runs forever and never attaches.
  // AN IN-FLIGHT ATTACH EXPIRES. The guard below is correct while an
  // attach is genuinely running, and fatal if one never finishes:
  // `attaching` stays true, every retry returns here, and the extension
  // never reconnects for the rest of the session. That is not
  // hypothetical, it was measured after a server restart, and the
  // `finally` that clears the flag is no protection because it does not
  // run while an await is still pending.
  //
  // So the flag carries a deadline. Past it, a new attach proceeds and
  // takes ownership; the stalled one is left to finish whenever it
  // does, and cannot clear a flag it no longer owns.
  const startedAt = Date.now();
  if (attaching && (startedAt - attachingSince) < ATTACH_STALL_MS) return;
  attaching = true;
  attachingSince = startedAt;
  const myAttach = startedAt;
  try {
    const configured =
      eda.sys_Storage && eda.sys_Storage.getExtensionUserConfig
        ? eda.sys_Storage.getExtensionUserConfig('serverUrl')
        : null;
    const url = configured || (await findServer());

    if (url) {
      noteOutcome(`found a server, dialling ${url}`);
      openSocket(url);
      return;
    }

    // Nothing identified itself, which on a runtime without fetch is
    // the normal case rather than a failure. Try the ports directly and
    // keep whichever connects. Each attempt is closed before the next,
    // so a port that answers but is not us leaves no socket behind.
    for (const port of candidatePorts()) {
      if (connected) return;
      // Close by the id THIS call registered. Closing WS_ID would close
      // whatever the global points at, which is the other half of the
      // race above.
      const mine = openSocket(`ws://127.0.0.1:${port}/eda`);
      // Give the socket a moment to report success. The connected
      // callback is what sets `connected`, so this is the only way to
      // tell a live port from a dead one without a health probe.
      await delay(PROBE_MS);
      if (connected) return;
      try {
        eda.sys_WebSocket.close(mine);
      } catch (e) { /* nothing was open */ }
    }

    // Every candidate walked and nothing answered. Say so IN THE
    // EDITOR. This path used to return in silence, which is the worst
    // possible outcome to report: identical to never having installed
    // the extension, and indistinguishable from a server that is up but
    // unreachable.
    noteOutcome(
      lastProbeNotes.length
        ? `no eda-agent server found. Ports answered by something else: ${lastProbeNotes.join(', ')}`
        : `no eda-agent server found on ${PORT_START}-${PORT_END}. Is it running?`,
    );
    const now = Date.now();
    if (now - lastFailureToastAt >= FAILURE_TOAST_MS) {
      lastFailureToastAt = now;
      toast(`eda-agent: ${lastOutcome}`);
    }
  } finally {
    if (attachingSince === myAttach) {
      attaching = false;
    }
  }
}

function toast(text) {
  try {
    eda.sys_Message.showToastMessage(text);
  } catch (e) { /* nothing to show on runtimes without a UI */ }
}

function noteOutcome(text) {
  lastOutcome = text;
  lastOutcomeAt = Date.now();
}

// What the extension currently believes, shown in the editor.
//
// THE POINT. Everything else the extension knows about its own
// connection is reported in the ping reply, over the socket, and is
// therefore readable exactly when a connection already works. The
// question anybody actually asks is the opposite one, and it has to be
// answerable with no socket at all.
//
// It states the build too, because a re-import at a version already
// installed is a silent no-op, so "the fix is not working" and "the fix
// is not installed" look the same from here.
// MODULE STATE ALONE IS NOT ENOUGH, and the first version of this that
// shipped proved it. It reported "NOT connected, attempts 0, retry
// timer NOT ARMED" while the server showed the editor connected, and
// both were accurate.
//
// A mid-session re-import loads a SECOND copy of this module. The
// editor fires onStartupFinished only at startup, so the new copy never
// runs activate(), yet it is the copy wired to the freshly imported
// menu. The older copy keeps the working socket and answers every
// command. Asking the menu's copy how the connection is going therefore
// asks the one that never made one.
//
// The server sees every copy, so the second half of this reads /health
// and reports what IT observes, including which builds are actually
// holding sockets. Comparing that against BUILD_ID is what distinguishes
// "not connected" from "connected, but not by this copy of the code".
export function status() {
  const age =
    lastOutcomeAt === 0
      ? 'never'
      : `${Math.round((Date.now() - lastOutcomeAt) / 1000)}s ago`;
  const lines = [
    `eda-agent ${BUILD_ID}`,
    connected ? 'this copy believes it is CONNECTED' : 'this copy: NOT connected',
    `last scan: ${lastOutcome} (${age})`,
    `attempts ${attachAttempts}, sockets opened ${wsSerial}`,
    `retry timer: ${retryTimer ? 'armed' : 'NOT ARMED'}`,
  ];
  if (attachAttempts === 0 && retryTimer === null) {
    lines.push(
      'This copy never started. It was imported mid session, so',
      'another loaded copy probably holds the socket. Asking the server.',
    );
  }
  toast(lines.join('\n'));

  // Asked second and reported separately, so the local answer appears
  // at once even on a runtime with no fetch and no server to ask.
  askServer().then((seen) => {
    if (!seen) {
      toast(
        hasFetch()
          ? `eda-agent: no server answered on ${PORT_START}-${PORT_END}`
          : 'eda-agent: this runtime has no fetch, so the server cannot be asked',
      );
      return;
    }
    // ABSENT IS NOT EMPTY. A server older than this field answers
    // without editor_builds at all, and treating that as "no builds
    // are connected" turns a question nobody asked into a confident
    // wrong answer: it would report this build as not holding a socket
    // when the server simply never said. Read nothing and nothing
    // there have to stay distinguishable.
    const lines = [
      `server on ${seen.port}: editor ${seen.editor_connected ? 'CONNECTED' : 'not connected'}`,
    ];
    if (!Array.isArray(seen.editor_builds)) {
      lines.push(
        'that server does not report which builds hold sockets,',
        'so whether this one does is unknown. Restart the server.',
      );
    } else if (seen.editor_builds.length === 0) {
      lines.push('no build has identified itself to it yet');
    } else {
      lines.push(`builds holding sockets: ${seen.editor_builds.join(', ')}`);
      lines.push(
        seen.editor_builds.indexOf(BUILD_ID) !== -1
          ? 'one of them is this build'
          : `this build (${BUILD_ID}) is NOT one of them`,
      );
    }
    toast(lines.join('\n'));
  });

  return lines.join('\n');
}

// What the server says, from whichever port answers first.
async function askServer() {
  if (!hasFetch()) return null;
  for (const port of candidatePorts()) {
    try {
      const response = await Promise.race([
        fetch(`http://127.0.0.1:${port}/health`, { method: 'GET' }),
        delay(PROBE_MS * 4).then(() => null),
      ]);
      if (!response || !response.ok) continue;
      const body = await Promise.race([
        response.json(),
        delay(PROBE_MS * 4).then(() => null),
      ]);
      if (body && body.service === SERVICE_ID) {
        return {
          port,
          editor_connected: body.editor_connected,
          editor_builds: body.editor_builds,
        };
      }
    } catch (e) {
      // Nothing there. Expected for most of the range.
    }
  }
  return null;
}

export function connect() {
  // Announce IMMEDIATELY, before anything can fail. The absence of
  // this toast after a click means the code running in the editor is
  // not this build, which is exactly the ambiguity this removes: same
  // uuid, same version, and the re-import was silently a no-op.
  toast(`eda-agent ${BUILD_ID}: connecting...`);

  // Start from a clean slate every time, because nothing else can.
  //
  // register() takes no close or error callback (checked against the
  // published signature: id, serviceUri, receiveMessageCallFn,
  // connectedCallFn, protocols), so the extension is never told when
  // the server at the other end goes away. `connected` stays true, and
  // attach() begins with `if (connected) return`, so picking Connect
  // again does nothing at all and the retry loop skips too. The menu
  // item looks broken when the truth is that it thinks its work is
  // already done.
  //
  // Closing first matters for a second reason. The register() remarks
  // warn that re-registering an ID that is still ACTIVE silently
  // ignores the new parameters, so a stale socket would swallow every
  // later attempt to point at a different port.
  connected = false;
  try {
    eda.sys_WebSocket.close(WS_ID);
  } catch (e) { /* nothing was open, which is the usual case */ }

  // attach() is async and this call site cannot await it (EasyEDA
  // invokes registerFn synchronously), so a throw inside would vanish
  // as an unhandled rejection. That is a SILENT dead click, and it is
  // the failure mode that could not be told apart from a stale build.
  attach().catch((e) => {
    toast(`eda-agent failed to connect: ${(e && e.message) || e}`);
  });
  if (retryTimer === null) {
    retryTimer = startInterval(() => {
      // Two timer sources may be armed, so the body is rate limited to
      // one run per interval. Without this the idle counter advances
      // twice per period and the reattach window is half what
      // idle_limit says it is, which would make the reported numbers
      // lies.
      const now = Date.now();
      if (now - lastTickAt < RETRY_MS * 0.75) return;
      lastTickAt = now;

      if (connected) {
        idleTicks += 1;
        if (idleTicks >= IDLE_REATTACH_TICKS) {
          // Long enough without a word. Whether the server went away
          // or simply had nothing to say cannot be told apart here, so
          // the cheap option is taken: drop it and reattach.
          idleTicks = 0;
          connected = false;
          try {
            eda.sys_WebSocket.close(WS_ID);
          } catch (e) { /* already gone, which is the case in point */ }
        }
      }
      if (!connected) {
        attach().catch(() => { /* the first failure was already shown */ });
      }
    }, RETRY_MS);
  }
}

// Returns the id it registered under, so a caller that needs to undo
// this closes ITS OWN socket rather than whatever WS_ID happens to hold
// by then. WS_ID is global and moves under any concurrent attach.
function openSocket(url) {
  // Close the previous registration and take a new id before opening.
  const previous = renewSocketId();
  const mine = WS_ID;
  try {
    eda.sys_WebSocket.close(previous);
  } catch (e) { /* nothing was open under that id */ }
  eda.sys_WebSocket.register(
    WS_ID,
    url,
    (event) => {
      // register() hands back a MessageEvent, not a string. Verified
      // against the published signature:
      //   receiveMessageCallFn?: (event: MessageEvent<any>) => void
      // Calling String(event) yields "[object MessageEvent]", so every
      // command would be silently discarded while the socket looked
      // perfectly healthy.
      // Anything arriving proves the link is alive, which is the only
      // positive evidence this API provides.
      idleTicks = 0;
      const raw =
        event && typeof event === 'object' && 'data' in event
          ? event.data
          : event;
      dispatch(typeof raw === 'string' ? raw : String(raw));
    },
    () => {
      connected = true;
      noteOutcome(`connected to ${url}`);
      // Reset the failure window, so the NEXT time it goes away the
      // operator is told at once rather than after a quiet minute.
      lastFailureToastAt = 0;
      eda.sys_Message.showToastMessage(`eda-agent connected: ${url}`);
    },
  );
  return mine;
}

export function disconnect() {
  connected = false;
  if (retryTimer !== null) {
    stopInterval(retryTimer);
    retryTimer = null;
  }
  try {
    eda.sys_WebSocket.close(WS_ID);
  } catch (e) { /* already closed */ }
}


// EasyEDA calls activate() on load; connecting immediately is what makes
// the bridge usable without a menu click, and the menu items remain for
// reconnecting after the server restarts.
export function activate() {
  connect();
}

export function deactivate() {
  disconnect();
}
