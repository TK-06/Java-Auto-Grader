// The HTML shell both webviews share. The content itself is drawn by the
// scripts in media/ from the model the extension posts; nothing here is built
// from runtime data, so there is no place for a file name or a CSV cell to be
// interpreted as markup.

import * as crypto from "crypto";
import * as vscode from "vscode";

export function webviewHtml(webview: vscode.Webview, extensionUri: vscode.Uri, script: string, title: string): string {
  const nonce = crypto.randomBytes(16).toString("base64");
  const media = (file: string) => webview.asWebviewUri(vscode.Uri.joinPath(extensionUri, "media", file)).toString();
  const csp = [
    "default-src 'none'",
    `style-src ${webview.cspSource} https://fonts.googleapis.com`,
    `font-src ${webview.cspSource} https://fonts.gstatic.com`,
    `img-src ${webview.cspSource} data:`,
    `script-src 'nonce-${nonce}'`,
  ].join("; ");
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,650&family=JetBrains+Mono:wght@400;600&family=Public+Sans:wght@400;500;600&display=swap">
<link rel="stylesheet" href="${media("style.css")}">
</head>
<body>
<div id="app" class="app"></div>
<script nonce="${nonce}" src="${media("common.js")}"></script>
<script nonce="${nonce}" src="${media(script)}"></script>
</body>
</html>`;
}
