// The side-bar checklist. Click a step to open its page; the chevron expands
// it in place to show what it is about and what disk says.
"use strict";

document.body.classList.add("sidebar");
const app = document.getElementById("app");
const expanded = new Set((vscode.getState() || {}).expanded || []);
let model;

function toggle(id) {
  if (expanded.has(id)) expanded.delete(id);
  else expanded.add(id);
  vscode.setState({ expanded: [...expanded] });
  render();
}

function runningBlock(running) {
  const pct = running.percent;
  return h(
    "div",
    { class: "running", id: "running" },
    h("div", { class: `bar ${pct === undefined ? "indeterminate" : ""}` }, h("div", { class: "fill", style: pct === undefined ? "" : `width:${pct}%` })),
    h("div", { class: "msg" }, running.message || `${running.label}...`),
  );
}

function build() {
  if (!model) return h("div");
  if (!model.ok) return problemView(model.problem);
  return h(
    "div",
    { class: "side" },
    h(
      "div",
      { class: "side-head" },
      h("div", { class: "eyebrow" }, "This week"),
      h("div", { class: "week-name" }, model.weekLabel),
      h("div", { class: "where", title: model.gradingDir }, model.gradingDir),
    ),
    h(
      "ol",
      { class: "steps" },
      model.steps.map((s) => {
        const current = s.index === model.current;
        const open = expanded.has(s.id);
        const running = model.running && model.running.stepIndex === s.index;
        return h(
          "li",
          { class: [s.done && "done", current && "current"].filter(Boolean).join(" ") },
          h(
            "button",
            { class: "row ghost", title: "Open this step", onclick: () => post({ type: "open", index: s.index }) },
            h("span", { class: "num" }, s.done ? "✓" : String(s.index + 1)),
            h("span", {}, h("div", { class: "t" }, s.title), current && !open ? h("div", { class: "s" }, s.summary) : null),
            h(
              "span",
              {
                class: "chev",
                role: "button",
                title: open ? "Collapse" : "Expand",
                onclick: (e) => {
                  e.stopPropagation();
                  toggle(s.id);
                },
              },
              open ? "▾" : "▸",
            ),
          ),
          running ? runningBlock(model.running) : null,
          open
            ? h(
                "div",
                { class: "more" },
                h("div", { class: "s" }, s.summary),
                s.facts.length ? factList(s.facts) : null,
                h("div", { class: "s" }, s.detected === undefined ? (s.ticked ? "Ticked by hand." : "Tick it on its page when done.") : s.detected ? "Detected as done from disk." : s.ticked ? "Ticked by hand." : "Not done yet."),
              )
            : null,
        );
      }),
    ),
    h(
      "div",
      { class: "side-foot" },
      h("button", { class: "primary", onclick: () => post({ type: "open", index: model.current }) }, `Continue: step ${model.current + 1}`),
      h("button", { onclick: () => post({ type: "openTerminal" }) }, "Terminal"),
      h("button", { class: "ghost", onclick: () => post({ type: "newWeek" }) }, "New week…"),
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
    model.running = msg.running;
    const el = document.getElementById("running");
    if (el) el.replaceWith(runningBlock(msg.running));
    else render();
  }
});

post({ type: "ready" });
