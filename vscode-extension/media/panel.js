// The step page: what to do and why, where things stand, the inputs, the
// buttons that run the step, its live progress, and prompts for an agent.
"use strict";

const app = document.getElementById("app");
const openPrompts = new Set();
let model;
const timers = {};

function setValue(id, value, now) {
  clearTimeout(timers[id]);
  if (now) post({ type: "setValue", id, value });
  else timers[id] = setTimeout(() => post({ type: "setValue", id, value }), 300);
}

function topbar() {
  const r = model && model.ok ? model.running : undefined;
  if (!r) return h("div", { class: "topbar", id: "topbar" });
  const pct = r.percent;
  return h(
    "div",
    { class: `topbar ${pct === undefined ? "indeterminate" : ""}`, id: "topbar" },
    h("div", { class: "fill", style: pct === undefined ? "" : `width:${pct}%` }),
  );
}

function fieldInput(f, archived) {
  const id = `f-${f.id}`;
  const invalid = f.error && f.value ? "invalid" : undefined;
  if (f.kind === "zips") {
    const zips = f.value ? f.value.split("\n").filter(Boolean) : [];
    return [
      zips.length
        ? h(
            "ul",
            { class: "zips" },
            zips.map((z) => {
              const cut = Math.max(z.lastIndexOf("\\"), z.lastIndexOf("/"));
              return h("li", { title: z }, h("span", { class: "zip-name" }, z.slice(cut + 1)), h("span", { class: "zip-dir" }, z.slice(0, cut + 1)));
            }),
          )
        : h("div", { class: "hint" }, "No zip picked yet."),
      h(
        "div",
        { class: "actions" },
        h("button", { onclick: () => post({ type: "pickZips" }) }, zips.length ? "Choose again…" : "Choose zip files…"),
        zips.length ? h("button", { class: "ghost", onclick: () => setValue("zips", "", true) }, "Clear") : null,
      ),
    ];
  }
  if (f.kind === "week") {
    const listId = "weeks-list";
    return [
      h("input", {
        type: "text",
        id,
        list: listId,
        class: invalid,
        value: f.value,
        placeholder: f.placeholder,
        autocomplete: "off",
        oninput: (e) => setValue(f.id, e.target.value.trim()),
        onchange: (e) => setValue(f.id, e.target.value.trim(), true),
      }),
      h("datalist", { id: listId }, archived.map((w) => h("option", { value: w.week }, `${w.week} (${w.questions.join(", ")})`))),
    ];
  }
  if (f.kind === "deadline") {
    return h("input", {
      type: "datetime-local",
      step: "1",
      id,
      class: invalid,
      value: f.value,
      onchange: (e) => setValue(f.id, e.target.value, true),
    });
  }
  return h("input", {
    type: f.kind === "question" ? "number" : "text",
    min: f.kind === "question" ? "1" : undefined,
    id,
    class: invalid,
    value: f.value,
    placeholder: f.placeholder,
    autocomplete: "off",
    spellcheck: "false",
    oninput: (e) => setValue(f.id, e.target.value),
    onchange: (e) => setValue(f.id, e.target.value, true),
  });
}

function fieldBlock(f, archived) {
  return h(
    "div",
    { class: "field" },
    h("label", { for: `f-${f.id}` }, f.label),
    fieldInput(f, archived),
    f.error && f.value ? h("div", { class: "error" }, f.error) : f.hint ? h("div", { class: "hint" }, f.hint) : null,
  );
}

function fieldsCard(step, archived) {
  const owned = new Set(step.actions.flatMap((a) => a.uses));
  const fields = step.fields.filter((f) => !owned.has(f.id));
  if (!fields.length) return null;
  const items = fields.map((f) => fieldBlock(f, archived));
  // Week + question sit side by side; anything with a list or a long value gets the full width.
  const allShort = fields.every((f) => f.kind === "week" || f.kind === "question");
  return h("section", { class: "card" }, h("h2", {}, "Inputs"), allShort ? h("div", { class: "field-row" }, items) : items);
}

function runCard(step) {
  if (!step.actions.length) return null;
  const r = model.running;
  const grouped = step.actions.some((a) => a.uses.length);
  const busyHere = r && r.stepIndex === step.index;
  const busyElsewhere = r && !busyHere;
  const buttons = step.actions.map((a) =>
    h(
      "button",
      {
        class: a.primary ? "primary" : undefined,
        disabled: !!a.disabled || !!r,
        title: a.disabled || a.command,
        onclick: () => post({ type: "run", action: a.id }),
      },
      a.primary ? `▶ ${a.label}` : a.label,
    ),
  );
  const reasons = step.actions.filter((a) => a.disabled);
  const ready = step.actions.filter((a) => !a.disabled);
  const last = model.last;

  return h(
    "section",
    { class: "card" },
    h("h2", {}, grouped ? "Audits" : "Run"),
    busyHere
      ? h(
          "div",
          { class: "run" },
          h("div", { class: "line" }, h("strong", {}, `${r.label}…`), h("span", { id: "run-pct" }, r.percent === undefined ? "" : `${r.percent}%`)),
          h("div", { class: `bar ${r.percent === undefined ? "indeterminate" : ""}`, id: "run-bar" }, h("div", { class: "fill", style: r.percent === undefined ? "" : `width:${r.percent}%` })),
          h("div", { class: "msg", id: "run-msg" }, r.message || "Running – the full output is in the DSA Grading terminal below."),
          h("div", { class: "actions" }, h("button", { class: "danger", onclick: () => post({ type: "cancel" }) }, "Cancel")),
        )
      : step.actions.some((a) => a.uses.length)
        ? [
            step.actions.map((a, i) =>
              h(
                "div",
                { class: "audit" },
                h("h3", {}, a.label.replace(/^Run /, "")),
                h(
                  "div",
                  { class: "field-row" },
                  a.uses.map((id) => fieldBlock(step.fields.find((f) => f.id === id), model.archived)),
                ),
                h("div", { class: "actions" }, buttons[i]),
                busyElsewhere ? null : a.disabled ? h("div", { class: "why-disabled" }, a.disabled) : h("div", { class: "will-run" }, "Runs: ", a.command),
              ),
            ),
            busyElsewhere ? h("div", { class: "why-disabled" }, `Step ${r.stepIndex + 1} is running – wait for it to finish.`) : null,
          ]
        : [
          h("div", { class: "actions" }, buttons),
          busyElsewhere ? h("div", { class: "why-disabled" }, `Step ${r.stepIndex + 1} is running – wait for it to finish.`) : null,
          !busyElsewhere && reasons.length
            ? reasons.map((a) => h("div", { class: "why-disabled" }, step.actions.length > 1 ? `${a.label}: ${a.disabled}` : a.disabled))
            : null,
          ready.map((a) => h("div", { class: "will-run" }, step.actions.length > 1 ? `${a.label} → ` : "Runs: ", a.command)),
        ],
    !busyHere && last
      ? h(
          "div",
          { class: `result ${last.cancelled ? "cancel" : last.code === 0 ? "ok" : "bad"}`, style: "margin-top:12px" },
          h("strong", {}, last.cancelled ? "Cancelled" : last.code === 0 ? "✓ Finished" : `✗ Exited with code ${last.code}`),
          h("span", {}, `${last.label} · ${last.seconds}s · ${new Date(last.at).toLocaleTimeString()}`),
          last.summary
            ? h("span", {}, `· ${last.summary.compiled}/${last.summary.rows} compiled · average ${last.summary.average.toFixed(2)} · ${last.summary.timeouts} timeout(s)`)
            : null,
        )
      : null,
  );
}

function copyCard(step) {
  if (!step.copyCommands.length) return null;
  return h(
    "section",
    { class: "card" },
    h("h2", {}, "Commands to run yourself"),
    step.copyCommands.map((c) => [
      h("div", { class: "copy-label" }, c.label),
      h("pre", { class: "cmd" }, c.text),
      h("button", { onclick: () => post({ type: "copy", text: c.text, what: "Command copied" }) }, "Copy"),
    ]),
  );
}

function promptsCard(step) {
  if (!step.prompts.length) return null;
  return h(
    "section",
    { class: "card" },
    h("h2", {}, "Ask your agent"),
    step.prompts.map((p, i) => {
      const key = `${step.id}:${i}`;
      const open = openPrompts.has(key);
      return h(
        "div",
        { class: "prompt" },
        h("div", { class: "title" }, p.label),
        h("div", { class: `text ${open ? "open" : ""}` }, p.text),
        h(
          "div",
          { class: "buttons" },
          h("button", { class: "primary", onclick: () => post({ type: "copy", text: p.text, what: "Prompt copied" }) }, "Copy prompt"),
          h(
            "button",
            {
              class: "ghost",
              onclick: () => {
                if (open) openPrompts.delete(key);
                else openPrompts.add(key);
                render();
              },
            },
            open ? "Show less" : "Show all",
          ),
          h("button", { class: "ghost", onclick: () => post({ type: "openTerminal" }) }, "Open a terminal"),
        ),
      );
    }),
  );
}

function linksRow(step) {
  if (!step.links.length) return null;
  return h(
    "div",
    { class: "links", style: "margin:16px 0" },
    step.links.map((l) => h("button", { disabled: !l.available, onclick: () => post({ type: "link", id: l.id }) }, l.label)),
  );
}

function build() {
  if (!model) return h("div");
  if (!model.ok) return problemView(model.problem);
  const step = model.step;
  const last = model.steps.length - 1;
  return h(
    "div",
    {},
    topbar(),
    h(
      "div",
      { class: "page" },
      h("div", { class: "eyebrow" }, `Step ${step.index + 1} of ${model.steps.length}  `, h("span", { class: "week" }, `· ${model.weekLabel}`)),
      h(
        "nav",
        { class: "stepper", "aria-label": "Steps" },
        model.steps.map((s) =>
          h(
            "button",
            {
              class: [s.done && "done", s.index === model.current && "current", s.index === step.index && "here"].filter(Boolean).join(" "),
              title: `${s.index + 1}. ${s.title}${s.done ? " (done)" : ""}`,
              onclick: () => post({ type: "goto", index: s.index }),
            },
            s.done && s.index !== step.index ? "✓" : String(s.index + 1),
          ),
        ),
      ),
      h("h1", {}, step.title),
      h("p", { class: "lede" }, step.summary),
      h(
        "div",
        {},
        step.done
          ? h("span", { class: "status ok" }, step.detected ? "✓ Done · detected from disk" : "✓ Marked done")
          : h("span", { class: "status todo" }, step.detected === undefined ? "Tick it below when it's done" : "Not done yet"),
      ),
      step.facts.length ? h("section", { class: "card" }, h("h2", {}, "Where things stand"), factList(step.facts)) : null,
      h("section", { class: "card" }, h("h2", {}, "What to do"), trustedHtml(step.body)),
      fieldsCard(step, model.archived),
      runCard(step),
      linksRow(step),
      copyCard(step),
      promptsCard(step),
      h(
        "div",
        { class: "footer" },
        h("button", { disabled: step.index === 0, onclick: () => post({ type: "goto", index: step.index - 1 }) }, "← Back"),
        h(
          "label",
          { class: "tick" },
          h("input", { type: "checkbox", checked: step.ticked, onchange: (e) => post({ type: "tick", value: e.target.checked }) }),
          "Mark this step done",
        ),
        h(
          "button",
          { class: "primary", disabled: step.index === last, onclick: () => post({ type: "goto", index: step.index + 1 }) },
          "Continue →",
        ),
      ),
    ),
  );
}

function render() {
  renderInto(app, build);
}

window.addEventListener("message", (e) => {
  const msg = e.data;
  if (msg.type === "model") {
    model = msg.model;
    render();
  } else if (msg.type === "progress" && model && model.ok) {
    // Per-student updates: patch the bars in place instead of rebuilding the page.
    const wasRunning = !!model.running;
    model.running = msg.running;
    const r = msg.running;
    const pct = r.percent;
    const bar = document.getElementById("run-bar");
    if (!wasRunning || !bar) return render();
    document.getElementById("topbar").replaceWith(topbar());
    bar.className = `bar ${pct === undefined ? "indeterminate" : ""}`;
    bar.firstChild.style.width = pct === undefined ? "" : `${pct}%`;
    document.getElementById("run-pct").textContent = pct === undefined ? "" : `${pct}%`;
    document.getElementById("run-msg").textContent = r.message || "";
  }
});

post({ type: "ready" });
