// Shared by view.js (side-bar checklist) and panel.js (step page).
// Everything is built with createElement + textContent: values that come from
// disk or the TA (file names, CSV counts, typed inputs) are never parsed as
// HTML. The one exception is a step's `body`, which is static text written in
// src/steps.ts and passed through `trustedHtml`.
/* eslint-disable no-unused-vars */
"use strict";

const vscode = acquireVsCodeApi();

function post(msg) {
  vscode.postMessage(msg);
}

/** h("div", {class: "x", onclick: fn}, child, "text", [more]) */
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "value") el.value = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, String(v));
  }
  for (const c of children.flat(Infinity)) {
    if (c === undefined || c === null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

function trustedHtml(html) {
  const div = document.createElement("div");
  div.className = "prose";
  div.innerHTML = html;
  return div;
}

/** Re-render without losing the TA's place: the focused input keeps what was
 *  typed (the model may still hold the value from before a debounce), its
 *  caret, and the page keeps its scroll position. */
function renderInto(root, build) {
  const active = document.activeElement;
  const keep =
    active && active.id && active.tagName === "INPUT"
      ? { id: active.id, value: active.value, start: active.selectionStart, end: active.selectionEnd }
      : undefined;
  const scroll = window.scrollY;
  root.replaceChildren(build());
  if (keep) {
    const el = document.getElementById(keep.id);
    if (el) {
      el.value = keep.value;
      el.focus();
      try {
        el.setSelectionRange(keep.start, keep.end);
      } catch (_) {
        /* datetime-local has no selection */
      }
    }
  }
  window.scrollTo(0, scroll);
}

function factList(facts) {
  return h(
    "dl",
    { class: "facts" },
    facts.map((f) => [h("dt", {}, h("span", { class: `dot ${f.tone || ""}` }), f.label), h("dd", {}, f.value)]),
  );
}

function problemView(problem) {
  return h("div", { class: "problem" }, h("h1", {}, "DSA Grading can't find the grader"), h("p", {}, problem));
}
